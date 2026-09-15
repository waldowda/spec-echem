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
    print("\n--- 3. is that exposure actually HONOURED? ---")
    # AVS_PrepareMeasure accepting a value only means it passed PARAMETER VALIDATION.
    # It does not mean the detector integrates for that long: a 2048-pixel readout
    # alone takes longer than 9 us, so an accepted-but-not-honoured value would be
    # silently clamped. The real floor is where elapsed time stops tracking the
    # request. MEASURED 0.009033 ms accepted on a 2048 px detector whose own
    # stand-alone default is 2.2 ms, which is why this stage exists.
    from avaspec import AVS_Measure, AVS_PollScan, AVS_GetScopeData
    import time

    print(f"  {'requested (ms)':>16}  {'elapsed (ms)':>14}  {'ratio':>7}  honoured?")
    honoured_floor = None
    for requested in (hi, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0):
        cfg = _config(handle, pixels, requested)
        if AVS_PrepareMeasure(handle, cfg) < 0:
            print(f"  {requested:16.4g}  {'rejected':>14}")
            continue
        t0 = time.perf_counter()
        if AVS_Measure(handle, 0, 1) < 0:
            print(f"  {requested:16.4g}  {'measure failed':>14}")
            continue
        deadline = t0 + max(2.0, requested / 1000.0 * 5.0 + 2.0)
        while not AVS_PollScan(handle) and time.perf_counter() < deadline:
            time.sleep(0.0005)
        elapsed = (time.perf_counter() - t0) * 1000.0
        try:
            AVS_GetScopeData(handle)
        except Exception:                             # noqa: BLE001 — probe script
            pass
        ratio = elapsed / requested if requested else float("inf")
        # Honoured means the elapsed time grew with the request. Well below the
        # floor, elapsed is dominated by fixed readout and barely moves.
        ok = "yes" if ratio < 20 else "NO — clamped/readout-bound"
        if ok == "yes" and honoured_floor is None:
            honoured_floor = requested
        print(f"  {requested:16.4g}  {elapsed:14.3f}  {ratio:7.1f}  {ok}")

    print(f"\n  lowest exposure that tracks the request: "
          f"{honoured_floor if honoured_floor else 'none of those tried'}")
    print("\nPaste this whole output back. The defaults and the linearity ramp should "
          "follow the HONOURED floor, not merely what PrepareMeasure accepts.")


if __name__ == "__main__":
    main()
