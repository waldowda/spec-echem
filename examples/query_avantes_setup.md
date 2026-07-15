# Talking to an Avantes spectrometer from Python — setup

A short guide for running `query_avantes.py`, a **read-only** check that answers one
question: *can this PC open the Avantes spectrometer from Python?* It prints the
serial number, name, detector pixels, and wavelength span, then closes. It does not
measure, trigger, or apply anything. It also reports whether Windows sees a
Metrohm/Autolab USB device (presence only — see the last section).

This works with any AvaSpec model the SDK supports (ULS2048i, VRS-series, EVO, …);
the model name isn't in the SDK, so read it off the unit's label.

---

## 1. What you need installed

1. **The Avantes AvaSpec-DLL SDK.** This is the piece that matters, and it is
   *separate* from Autolab/NOVA. NOVA talking to the spectrometer does **not** mean
   the Python SDK is present — NOVA uses its own driver path. The SDK gives you two
   things:
   - the DLL — `avaspecx64.dll` (64-bit) or `avaspec.dll` (32-bit), and
   - the Python wrapper `avaspec.py` (plus a small `globals.py`), found under the
     SDK's `examples\PyQt5_simple\` folder.

   If you don't have the SDK, it's the "AvaSpec-DLL" download from Avantes, or it's
   on the USB stick that shipped with the spectrometer.

2. **Python — 64-bit is recommended.** For an Avantes-only check there's no reason to
   use 32-bit. Just match Python's bitness to the DLL: **64-bit Python ↔
   `avaspecx64.dll`**. (Anaconda/Miniconda or python.org are both fine.)

3. **No other packages required.** The script uses only the standard library plus
   `avaspec`. `numpy` is *not* required.

---

## 2. Put `avaspec.py` where Python can import it

Two options:

- **Easiest:** run the script from inside the SDK's `examples\PyQt5_simple\` folder,
  where `avaspec.py`, `globals.py`, and often the DLL already sit together. Copy
  `query_avantes.py` into that folder and run it there.
- **Or:** copy `avaspec.py` (and `globals.py`) into your Python environment's
  `site-packages\` folder, or keep them next to `query_avantes.py`.

### The three edits to `avaspec.py`

The SDK's `avaspec.py` usually needs three small edits before it will import cleanly
(these are Avantes' own vestigial lines, not something we added). Open it in a text
editor:

1. Comment out `import globals` if `globals.py` isn't alongside it.
2. Comment out `from PyQt5.QtCore import *` (this script doesn't use PyQt, and you
   likely don't have it installed).
3. **DLL path:** newer `avaspec.py` loads the DLL by a relative name, which fails
   unless the DLL folder is on the path. `query_avantes.py` handles this for you —
   set `AVASPEC_DLL_DIR` at the top of the script to your DLL folder (e.g.
   `C:\AvaSpecX64-DLL_9.14.0.0`). So you can usually leave edit #3 in `avaspec.py`
   alone.

> Use the `avaspec.py` that came with **your** SDK version. Don't copy someone else's
> — the wrapper and the DLL are a version-matched pair.

---

## 3. Run it

```
python query_avantes.py
```

A good result looks like:

```
================================================================
AVANTES SPECTROMETER
================================================================
Devices found: 1
Serial number      : 1234567U1
User friendly name :
Activated OK (handle 0) — Python can talk to it. [OK]
Detector pixels    : 2048
Sensor type (enum) : 22
Wavelength range   : 200.3 - 1100.7 nm  (full detector)
```

Even if the `Detector pixels` / `Sensor type` line reports it couldn't read the
parameters (the SDK struct can differ by model), an **"Activated OK"** line with a
serial and a wavelength range still means Python can talk to the spectrometer.

---

## 4. If it can't find the spectrometer

Most likely causes, in order:

1. **Another program owns it.** Only one process can hold the Avantes open. If NOVA,
   AvaSoft, or any other Avantes software is running — even minimized — close it and
   retry.
2. **Bitness mismatch.** 64-bit Python must load `avaspecx64.dll`; 32-bit Python must
   load `avaspec.dll`. A mismatch shows up as an import/DLL-load error.
3. **Wrong `AVASPEC_DLL_DIR`.** Point it at the folder that actually contains the DLL.
4. **USB driver.** The Avantes USB driver must be installed (the SDK installer
   normally does this). A driver that only NOVA installed may not be the one the DLL
   expects.

---

## 5. The Metrohm / Autolab line at the bottom

The script also runs a Windows-only scan and reports whether a device named
"Metrohm" or "Autolab" is present. This **only** confirms Windows enumerates it —
it does **not** mean Python can command the potentiostat. Real control needs
Metrohm's **Autolab SDK** (COM/ActiveX) or the **NOVA SDK**, which is a separate
exercise. The Autolab can also appear as a generic USB/FTDI device with no vendor
name, so a "not found" here isn't conclusive.
