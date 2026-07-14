"""
Print what hardware is actually attached, so the README/SOP can name the exact models
this system has been tested against instead of guessing.

Run it on the instrument PC with the spectrometer (and, for the Gamry section, the
32-bit toolkitpy environment) available:

    conda activate SpecEchem32      # or SpecEchem — the Avantes half works in either
    python examples/identify_hardware.py

Read-only: it opens the devices, reads their identity, and closes. It does not measure,
does not apply a potential, and does not touch the cell.
"""
import sys


def show_spectrometer():
    print("=" * 60)
    print("AVANTES SPECTROMETER")
    print("=" * 60)
    try:
        from avaspec import (AVS_Init, AVS_GetNrOfDevices, AVS_GetList, AVS_Activate,
                             AVS_GetParameter, AVS_GetLambda, AVS_Done)
    except ImportError as exc:
        print(f"avaspec not available here ({exc}) — run this on the instrument PC.\n")
        return

    AVS_Init(0)
    n = AVS_GetNrOfDevices()
    print(f"Devices found: {n}")
    if n < 1:
        print("No spectrometer — is it plugged in and powered?\n")
        AVS_Done()
        return

    ident = AVS_GetList(1)[0]
    handle = AVS_Activate(ident)

    def text(raw):
        return raw.decode("utf-8", "replace").strip("\x00 ") if isinstance(raw, bytes) else str(raw)

    print(f"Serial number      : {text(ident.SerialNumber)}")
    print(f"User friendly name : {text(getattr(ident, 'UserFriendlyName', ''))}")

    devcon = AVS_GetParameter(handle, 63484)
    pixels = devcon.m_Detector_m_NrPixels
    print(f"Detector pixels    : {pixels}")
    print(f"Sensor type (enum) : {getattr(devcon, 'm_Detector_m_SensorType', '?')}")

    wl = AVS_GetLambda(handle)
    print(f"Wavelength range   : {wl[0]:.1f} – {wl[pixels - 1]:.1f} nm  (full detector)")
    print("\nThe MODEL is usually not in the SDK — read it off the label on the unit")
    print("(e.g. AvaSpec-ULS2048CL-EVO) and pair it with the serial number above.\n")

    AVS_Done()


def show_potentiostat():
    print("=" * 60)
    print("GAMRY POTENTIOSTAT")
    print("=" * 60)
    try:
        from spec_echem.potentiostat import TOOLKITPY_AVAILABLE, probe_identity
    except ImportError as exc:
        print(f"spec_echem not importable ({exc})\n")
        return

    if not TOOLKITPY_AVAILABLE:
        print("toolkitpy not available in this environment.")
        print(f"(Python {sys.maxsize > 2**32 and '64' or '32'}-bit — EchemToolkitPy needs 32-bit.)")
        print("The Gamry model is on the label; External mode doesn't query it.\n")
        return

    try:
        label, serial = probe_identity()
        print(f"Label  : {label}")
        print(f"Serial : {serial}\n")
    except Exception as exc:  # noqa: BLE001 — this is a diagnostic; report and move on
        print(f"Could not query the Gamry: {exc}\n")


if __name__ == "__main__":
    show_spectrometer()
    show_potentiostat()
