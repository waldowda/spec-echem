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

    print("\n--- 1. every DeviceConfigType field (looking for a stated minimum) ---")
    for name in sorted(dir(devcon)):
        if name.startswith("_"):
            continue
        try:
            value = getattr(devcon, name)
        except Exception as exc:                      # noqa: BLE001 — probe script
            value = f"<unreadable: {exc}>"
        if callable(value):
            continue
        text = str(value)
        print(f"  {name:<46} {text[:70]}")

    print("\n--- 2. smallest integration time AVS_PrepareMeasure accepts ---")
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
    print(f"\n  MINIMUM INTEGRATION TIME ≈ {hi:.4g} ms")
    print(f"  (largest rejected: {lo:.4g} ms)")
    print("\nPaste this whole output back — the defaults and the linearity ramp "
          "should follow this number, not a hardcoded 0.022 ms.")


if __name__ == "__main__":
    main()
