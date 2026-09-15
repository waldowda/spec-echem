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
import time

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
    print("\n--- 3. is that exposure actually honoured? (LAMP MUST BE ON) ---",
          flush=True)
    # The clock cannot answer this. MEASURED on a 2048 px detector: elapsed time is
    # ~1.2-1.8 ms of fixed overhead (USB round trip + readout) plus the integration,
    # and the SCATTER in that overhead is ~0.6 ms -- larger than every request below
    # 1 ms. A 0.05 ms request came back FASTER than a 0.009 ms one, which is noise.
    #
    # Counts are the sensitive probe: accumulated signal is proportional to the time
    # actually integrated, so if the hardware clamps below some floor, counts are
    # FLAT below it and rise linearly above. That knee ignores host timing.
    #
    # Every step announces itself BEFORE it runs and flushes, so a hang or a hard
    # failure shows where it happened instead of leaving a silent console.
    import traceback

    try:
        from avaspec import AVS_Measure, AVS_PollScan, AVS_GetScopeData
    except Exception:                                 # noqa: BLE001 — probe script
        print("  could not import the measurement calls:", flush=True)
        traceback.print_exc()
        return

    print("  Needs steady illumination. With the lamp off every row reads the dark",
          flush=True)
    print("  floor and there is no knee to find.\n", flush=True)
    print(f"  {'requested (ms)':>16}  {'mean counts':>12}  {'counts/ms':>12}", flush=True)

    rows = []
    for requested in (hi, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0):
        print(f"  {requested:16.4g}  ", end="", flush=True)
        try:
            cfg = _config(handle, pixels, requested)
            rc = AVS_PrepareMeasure(handle, cfg)
            if rc < 0:
                print(f"prepare rejected it (code {rc})", flush=True)
                continue
            rc = AVS_Measure(handle, 0, 1)
            if rc < 0:
                print(f"measure failed (code {rc})", flush=True)
                continue
            deadline = time.perf_counter() + max(3.0, requested / 1000.0 * 5.0 + 3.0)
            while not AVS_PollScan(handle):
                if time.perf_counter() > deadline:
                    print("timed out waiting for data", flush=True)
                    break
                time.sleep(0.0005)
            else:
                result = AVS_GetScopeData(handle)
                spectrum = result[1]
                counts = sum(spectrum[:pixels]) / float(pixels)
                rows.append((requested, counts))
                print(f"{counts:12.1f}  {counts / requested:12.1f}", flush=True)
        except Exception:                             # noqa: BLE001 — probe script
            print("raised:", flush=True)
            traceback.print_exc()

    if not rows:
        print("\n  no rows measured — the traceback or message above says why.",
              flush=True)
        return

    # The knee: below the floor the detector integrates for the same real time
    # whatever is asked, so counts barely move; above it they scale with the request.
    floor = None
    for (t_a, c_a), (t_b, c_b) in zip(rows, rows[1:]):
        grew = (c_b - c_a) / max(abs(c_a), 1.0)
        asked = (t_b - t_a) / max(t_a, 1e-9)
        if grew > 0.5 * asked:
            floor = t_a
            break
    if floor is not None:
        print(f"\n  counts start tracking the request at ~{floor} ms", flush=True)
        print("  Below that the detector integrates the same real time whatever is",
              flush=True)
        print("  asked for. THAT is the minimum the defaults should follow.", flush=True)
    else:
        print("\n  counts never tracked the request. Either the lamp is off, or the",
              flush=True)
        print("  detector is saturated at every step — check the counts column.",
              flush=True)
    print("\nPaste this whole output back.", flush=True)
