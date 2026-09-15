"""
Find the spectrometer's MINIMUM integration time — two ways, on the hardware.

Requested: "the initial value put in the int time is not connected to the
actual Avantes spectrometer. My version on one rig can go to much smaller int times than
the other."

He is right, and it matters more than a bad default: `lin_start_ms` is 0.022 and
`lin_stop_ms` is 0.15, while docs/metrohm-rig-status.md records the AvaSpec-ULS2048L as
~1.05 ms minimum. The whole linearity ramp there sits BELOW what the detector can do.

This script answers the question on the actual device rather than from a datasheet:

  1. Dumps every DeviceConfigType field AVS_GetParameter returns, so we can see
     whether the SDK exposes a minimum directly. It may not — the minimum is a
     property of the detector, and the struct is mostly calibration.
  2. Failing that, finds it EMPIRICALLY: bisect the integration time and ask
     AVS_PrepareMeasure to accept it. An out-of-range value returns an error code,
     so the smallest accepted value is the minimum the device will honour.

Run it on BOTH rigs and paste the output — the numbers decide what the defaults become.

    python examples/probe_min_integration.py
"""
import sys

try:
    import avaspec
except ImportError:
    sys.exit("avaspec is not available — run this on an instrument machine.")

from avaspec import (AVS_Init, AVS_GetList, AVS_Activate, AVS_GetParameter,
                     AVS_PrepareMeasure, MeasConfigType)

ACCEPT_TOLERANCE_MS = 1e-4          # bisect until the bracket is this tight


def _config(handle, pixels, integration_ms):
    m = MeasConfigType()
    m.m_StartPixel = 0
    m.m_StopPixel = pixels - 1
    m.m_IntegrationTime = float(integration_ms)
    m.m_IntegrationDelay = 0
    m.m_NrAverages = 1
    m.m_CorDynDark_m_Enable = 0
    m.m_CorDynDark_m_ForgetPercentage = 0
    m.m_Smoothing_m_SmoothPix = 0
    m.m_Smoothing_m_SmoothModel = 0
    m.m_SaturationDetection = 0
    m.m_Trigger_m_Mode = 0
    m.m_Trigger_m_Source = 0
    m.m_Trigger_m_SourceType = 0
    m.m_Control_m_StrobeControl = 0
    m.m_Control_m_LaserDelay = 0
    m.m_Control_m_LaserWidth = 0
    m.m_Control_m_LaserWaveLength = 0.0
    m.m_Control_m_StoreToRam = 0
    return m


def main():
    if AVS_Init(0) <= 0:
        sys.exit("No Avantes device found.")
    devices = AVS_GetList(1)
    serial = devices[0].SerialNumber.decode("utf-8")
    handle = AVS_Activate(devices[0])
    devcon = AVS_GetParameter(handle, 63484)
    pixels = devcon.m_Detector_m_NrPixels
    print(f"serial   : {serial}")
    print(f"pixels   : {pixels}")

    print("\n--- 1. the stated minimum, if the wrapper exposes it ---")
    # The SDK's DeviceConfigType DOES carry m_Detector.m_MinIntegrationTime /
    # m_MaxIntegrationTime. But THIS project's avaspec wrapper returns the struct
    # with FLATTENED names -- the working code reads devcon.m_Detector_m_NrPixels,
    # not devcon.m_Detector.m_NrPixels -- so try both spellings rather than assume.
    stated_min = stated_max = None
    for flat, nested in ((("m_Detector_m_MinIntegrationTime",), ("m_Detector", "m_MinIntegrationTime")),
                         (("m_Detector_m_MaxIntegrationTime",), ("m_Detector", "m_MaxIntegrationTime"))):
        value = getattr(devcon, flat[0], None)
        how = f"devcon.{flat[0]}"
        if value is None:
            parent = getattr(devcon, nested[0], None)
            value = getattr(parent, nested[1], None) if parent is not None else None
            how = f"devcon.{nested[0]}.{nested[1]}"
        label = "min" if "Min" in flat[0] else "max"
        if value is None:
            print(f"  {label}: NOT exposed under either spelling")
        else:
            print(f"  {label}: {float(value):.6g} ms   (via {how})")
            if label == "min":
                stated_min = float(value)
            else:
                stated_max = float(value)

    print("\n--- every DeviceConfigType field, for the record ---")
    for name in sorted(dir(devcon)):
        if name.startswith("_"):
            continue
        try:
            value = getattr(devcon, name)
        except Exception as exc:                      # noqa: BLE001 — probe script
            value = f"<unreadable: {exc}>"
        if callable(value):
            continue
        print(f"  {name:<46} {str(value)[:70]}")

    print("\n--- 2. smallest AVS_PrepareMeasure actually ACCEPTS ---")
    # Cross-check, not a fallback only: the EEPROM value and what the device
    # will honour need not agree, and it is the accepted value that governs.
    lo, hi = 0.0, 1.0
    # widen until something is accepted, so a slow detector is not assumed fast
    while AVS_PrepareMeasure(handle, _config(handle, pixels, hi)) < 0:
        lo, hi = hi, hi * 2.0
        if hi > 10000.0:
            sys.exit("  nothing accepted below 10 s — check the device.")
    print(f"  accepted at {hi:g} ms, rejected at {lo:g} ms — bisecting")
    while hi - lo > ACCEPT_TOLERANCE_MS:
        mid = (lo + hi) / 2.0
        if AVS_PrepareMeasure(handle, _config(handle, pixels, mid)) < 0:
            lo = mid
        else:
            hi = mid
    print(f"\n  ACCEPTED MINIMUM ≈ {hi:.4g} ms   (largest rejected: {lo:.4g} ms)")
    if stated_min is not None:
        agree = abs(hi - stated_min) <= max(0.01 * stated_min, ACCEPT_TOLERANCE_MS)
        print(f"  STATED minimum   = {stated_min:.6g} ms"
              + ("   -- agrees" if agree else "   -- DISAGREES with what is accepted"))
    if stated_max is not None:
        print(f"  stated maximum   = {stated_max:.6g} ms")
    print("\n--- 3. is that exposure actually honoured? (LAMP MUST BE ON) ---")
    # The clock cannot answer this. MEASURED on a 2048 px detector: elapsed time is
    # ~1.2-1.8 ms of fixed overhead (USB round trip + readout) plus the integration,
    # and the SCATTER in that overhead is ~0.6 ms -- larger than every request below
    # 1 ms. A 0.05 ms request came back FASTER than a 0.009 ms one, which is noise,
    # not signal.
    #
    # The detector's own integral is the sensitive probe. Accumulated counts are
    # proportional to the time actually integrated, so if the hardware clamps
    # everything below some floor F, counts are FLAT below F and rise linearly above
    # it. That knee is the real minimum, and it does not care about host timing.
    from avaspec import AVS_Measure, AVS_PollScan, AVS_GetScopeData
    import time

    print("  Needs steady illumination. With the lamp off every row reads the dark")
    print("  floor and the knee cannot be seen.\n")
    print(f"  {'requested (ms)':>16}  {'mean counts':>12}  {'counts/ms':>12}")

    ladder = [hi, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
    rows = []
    for requested in ladder:
        cfg = _config(handle, pixels, requested)
        if AVS_PrepareMeasure(handle, cfg) < 0:
            continue
        if AVS_Measure(handle, 0, 1) < 0:
            continue
        deadline = time.perf_counter() + max(2.0, requested / 1000.0 * 5.0 + 2.0)
        while not AVS_PollScan(handle) and time.perf_counter() < deadline:
            time.sleep(0.0005)
        try:
            _stamp, spectrum = AVS_GetScopeData(handle)
        except Exception as exc:                      # noqa: BLE001 — probe script
            print(f"  {requested:16.4g}  read failed: {exc}")
            continue
        counts = sum(spectrum[:pixels]) / float(pixels)
        rows.append((requested, counts))
        print(f"  {requested:16.4g}  {counts:12.1f}  {counts / requested:12.1f}")

    # The knee: walk up from the shortest time until counts start rising with it.
    # Below the floor the detector integrates for the SAME real time regardless of
    # what was asked, so counts barely move; above it they scale.
    floor = None
    for (t_a, c_a), (t_b, c_b) in zip(rows, rows[1:]):
        if t_b <= t_a:
            continue
        grew = (c_b - c_a) / max(c_a, 1.0)
        asked = (t_b - t_a) / max(t_a, 1e-9)
        if grew > 0.5 * asked:            # counts tracking the request, not flat
            floor = t_a
            break
    print(f"\n  counts start tracking the request at ~{floor} ms"
          if floor else "\n  counts never tracked — is the lamp on?")
    print("\n  Below that, the detector integrates for the same real time whatever")
    print("  is asked for. THAT is the minimum the defaults should follow.")
    print("\nPaste this whole output back.")
