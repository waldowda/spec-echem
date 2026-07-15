"""
query_avantes.py — Can this PC talk to the Avantes spectrometer from Python?

A standalone, read-only check. It opens the spectrometer through the Avantes
AvaSpec-DLL, prints what the SDK reports (serial, name, detector pixels,
wavelength span), and closes. It does NOT measure, trigger, or apply anything.

It also (Windows only) reports whether a Metrohm / Autolab USB device is visible
to Windows — presence ONLY, not proof that Python can command it.

This file does not import the spec-echem package — it is all you need, plus the
Avantes SDK (avaspec.py + its matching DLL). See query_avantes_setup.md.

Usage:
    python query_avantes.py
"""
import os
import sys

# ---------------------------------------------------------------------------
# EDIT THIS to the folder that holds your Avantes DLL (avaspecx64.dll for 64-bit
# Python, or avaspec.dll for 32-bit). It is created by the AvaSpec-DLL SDK
# installer; the version in the folder name will differ from the example below.
# If avaspec.py already finds its own DLL (e.g. you run this from inside the SDK
# examples folder), a wrong/missing path here is harmless — it is skipped when
# the folder does not exist.
# ---------------------------------------------------------------------------
AVASPEC_DLL_DIR = r"C:\AvaSpecX64-DLL_9.14.0.0"

if os.path.isdir(AVASPEC_DLL_DIR) and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(AVASPEC_DLL_DIR)


def _text(raw):
    """Decode the SDK's fixed-length byte fields to a clean string."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace").strip("\x00 ")
    return str(raw)


def query_avantes():
    print("=" * 64)
    print("AVANTES SPECTROMETER")
    print("=" * 64)
    try:
        from avaspec import (AVS_Init, AVS_GetNrOfDevices, AVS_GetList,
                             AVS_Activate, AVS_GetParameter, AVS_GetLambda,
                             AVS_Done)
    except Exception as exc:  # ImportError, or a DLL-load OSError
        print(f"Could not import 'avaspec': {exc}\n")
        print("The Avantes Python SDK is not importable here. See "
              "query_avantes_setup.md:")
        print("  - install the AvaSpec-DLL SDK (gives you the DLL + avaspec.py),")
        print("  - put avaspec.py where Python can import it,")
        print("  - make Python's bitness match the DLL (64-bit Python <-> "
              "avaspecx64.dll).")
        return

    if AVS_Init(0) < 0:
        print("AVS_Init failed — the DLL loaded but the library would not start. "
              "Check the DLL/Python bitness match.")
        return
    try:
        n = AVS_GetNrOfDevices()
        print(f"Devices found: {n}")
        if n < 1:
            print("No spectrometer seen. Is it plugged in and powered? Is NOVA "
                  "(or other Avantes software) holding it open? Close that and "
                  "retry — only one program can own the device at a time.")
            return

        ident = AVS_GetList(1)[0]
        print(f"Serial number      : {_text(ident.SerialNumber)}")
        print(f"User friendly name : {_text(getattr(ident, 'UserFriendlyName', ''))}")

        handle = AVS_Activate(ident)
        if handle < 0:
            print(f"AVS_Activate failed (code {handle}). The device is listed but "
                  "will not open — usually another program has it open, or a USB "
                  "driver problem.")
            return
        print(f"Activated OK (handle {handle}) — Python can talk to it. [OK]")

        wl = AVS_GetLambda(handle)

        # The struct size passed to AVS_GetParameter (63484) is model/SDK-version
        # specific; on a different model it can fail. If it does, the serial,
        # activation, and wavelength span above/below are still valid answers to
        # "can we talk to it?" — so treat the parameter read as best-effort.
        pixels = None
        try:
            devcon = AVS_GetParameter(handle, 63484)
            pixels = devcon.m_Detector_m_NrPixels
            print(f"Detector pixels    : {pixels}")
            print(f"Sensor type (enum) : {getattr(devcon, 'm_Detector_m_SensorType', '?')}")
        except Exception as exc:
            print(f"(Could not read device parameters: {exc}\n"
                  " This can happen when the SDK struct differs for this model; "
                  "the rest below is still valid.)")

        if pixels is None:
            # Infer the pixel count from the calibration array itself.
            vals = list(wl)
            pixels = sum(1 for v in vals if v > 0) or len(vals)
        print(f"Wavelength range   : {wl[0]:.1f} - {wl[pixels - 1]:.1f} nm  (full detector)")

        print("\nThe model name is NOT in the SDK — read it off the unit's label "
              "(e.g. AvaSpec-ULS2048i) and pair it with the serial above.")
    finally:
        AVS_Done()


def query_metrohm_usb():
    print()
    print("=" * 64)
    print("METROHM / AUTOLAB  (USB presence only)")
    print("=" * 64)
    if not sys.platform.startswith("win"):
        print("Windows-only check — skipped on this OS.")
        return

    import subprocess
    ps = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.Name -match 'Metrohm|Autolab' -or "
        "$_.Manufacturer -match 'Metrohm|Autolab' } | "
        "Select-Object -ExpandProperty Name"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except Exception as exc:
        print(f"Could not run the USB scan: {exc}")
        return

    names = [ln.strip() for ln in out.stdout.decode("utf-8", "replace").splitlines()
             if ln.strip()]
    if names:
        print("Windows sees these Metrohm/Autolab device(s):")
        for name in names:
            print(f"  - {name}")
    else:
        print("No device with 'Metrohm' or 'Autolab' in its name was found.")
        print("(The Autolab can also appear as a generic USB or FTDI device with "
              "no vendor name — absence here is not conclusive.)")

    print("\nNOTE: this only confirms Windows enumerates the device. Actually "
          "commanding the Autolab from Python needs Metrohm's Autolab SDK "
          "(COM/ActiveX) or the NOVA SDK — not covered by this script.")


if __name__ == "__main__":
    query_avantes()
    query_metrohm_usb()
