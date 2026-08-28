"""
query_avantes_trigger.py — Does the Autolab digital-out actually reach the
Avantes hardware-trigger input?

This is the one thing docs/metrohm-bench-check.md leaves open for Step 4: the sync
is a digital-out edge into the Avantes external-trigger input, but whether the
cable on this bench carries it — and with the right polarity — is unconfirmed.

This check answers it end to end from ONE Python process, no NOVA:

  1. connect the Autolab, set DIO port P1.A to output, drive it low
  2. arm the Avantes for a single HARDWARE-triggered scan (m_Trigger_m_Mode = 1)
  3. the moment the Avantes is armed, pulse P1.A  low -> high -> low
  4. report whether the scan completed

CELL-SAFE: only the DIO line is touched — the potentiostat cell (Ei) is never
switched on and no procedure is run. Even so, the FIRST time you run any new
instrument-control code, do it with the working electrode disconnected or on a
dummy cell.

Needs, all in one 64-bit environment (the SpecEchem conda env):
  - avaspec + its DLL .................. see query_avantes_setup.md
  - pythonnet + the Metrohm Autolab SDK  see query_autolab_setup.md

Close NOVA first — the SDK and NOVA cannot both hold the Autolab.

Usage:
    python query_avantes_trigger.py
"""
import os
import sys
import time

# ---------------------------------------------------------------------------
# EDIT THESE to match this bench. See query_avantes_setup.md (AVASPEC_DLL_DIR)
# and query_autolab_setup.md (SDK / ADX / HDW) — the HDW file is best taken from
# NOVA's serial-specific C:\ProgramData\Metrohm Autolab\<ver>\HardwareSetup.<serial>.xml.
# ---------------------------------------------------------------------------
AVASPEC_DLL_DIR = r"C:\AvaSpecX64-DLL_9.14.0.0"

SDK = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\EcoChemie.Autolab.Sdk"
ADX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Hardware Setup Files\Adk.x"
HDW = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml"

# Which P1 DIO port carries the trigger wire. Index into DioPortsP1:
#   0 = P1.A, 1 = P1.B, ...   P1.A is what the NOVA spectro procedures pulse here.
DIO_PORT_INDEX = 0

# Trigger pulse shape and how long to wait for the scan afterwards.
PULSE_WIDTH_S = 0.002
TRIGGER_TIMEOUT_S = 20.0

# Integration time for the test scan, in ms. Keep it above this detector's floor
# (the AvaSpec-ULS2048L minimum is ~1.05 ms).
INTEGRATION_MS = 5.0
# ---------------------------------------------------------------------------

# Avantes trigger config values (avaspec MeasConfigType):
#   m_Trigger_m_Mode:       0 = free-run, 1 = hardware trigger
#   m_Trigger_m_Source:     0 = external trigger input, 1 = sync input
#   m_Trigger_m_SourceType: 0 = edge, 1 = level
TRIGGER_MODE_HARDWARE = 1
TRIGGER_SOURCE_EXTERNAL = 0
TRIGGER_SOURCETYPE_EDGE = 0


def _hr(title):
    print("=" * 64)
    print(title)
    print("=" * 64)


def _text(raw):
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace").strip("\x00 ")
    return str(raw)


# --- Avantes ---------------------------------------------------------------

def _load_avaspec():
    """Import avaspec, having first put its DLL folder on the search path."""
    if os.path.isdir(AVASPEC_DLL_DIR) and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(AVASPEC_DLL_DIR)
    try:
        import avaspec
        return avaspec
    except Exception as exc:  # ImportError, or a DLL-load OSError
        print(f"Could not import 'avaspec': {exc}")
        print("The Avantes Python SDK is not importable here — see "
              "query_avantes_setup.md.")
        return None


def _build_measconfig(avaspec, pixels):
    """A minimal, fully-populated MeasConfigType armed for a hardware trigger."""
    cfg = avaspec.MeasConfigType()
    cfg.m_StartPixel = 0
    cfg.m_StopPixel = pixels - 1
    cfg.m_IntegrationTime = INTEGRATION_MS
    cfg.m_IntegrationDelay = 0
    cfg.m_NrAverages = 1
    cfg.m_CorDynDark_m_Enable = 0
    cfg.m_CorDynDark_m_ForgetPercentage = 0
    cfg.m_Smoothing_m_SmoothPix = 0
    cfg.m_Smoothing_m_SmoothModel = 0
    cfg.m_SaturationDetection = 0
    cfg.m_Trigger_m_Mode = TRIGGER_MODE_HARDWARE
    cfg.m_Trigger_m_Source = TRIGGER_SOURCE_EXTERNAL
    cfg.m_Trigger_m_SourceType = TRIGGER_SOURCETYPE_EDGE
    cfg.m_Control_m_StrobeControl = 0
    cfg.m_Control_m_LaserDelay = 0
    cfg.m_Control_m_LaserWidth = 0
    cfg.m_Control_m_LaserWaveLength = 0.0
    cfg.m_Control_m_StoreToRam = 0
    return cfg


def _open_avantes(avaspec):
    """Return (handle, pixels) for the first spectrometer, or (None, None)."""
    if avaspec.AVS_Init(0) < 0:
        print("AVS_Init failed — the DLL loaded but the library would not start.")
        return None, None
    if avaspec.AVS_GetNrOfDevices() < 1:
        print("No spectrometer seen. Plugged in and powered? Is another program "
              "(AvaSoft / NOVA) holding it open?")
        return None, None
    ident = avaspec.AVS_GetList(1)[0]
    print(f"Avantes serial     : {_text(ident.SerialNumber)}")
    handle = avaspec.AVS_Activate(ident)
    if handle < 0:
        print(f"AVS_Activate failed (code {handle}).")
        return None, None
    try:
        pixels = avaspec.AVS_GetParameter(handle, 63484).m_Detector_m_NrPixels
    except Exception:
        pixels = len(avaspec.AVS_GetLambda(handle)) or 2048
    print(f"Detector pixels    : {pixels}")
    return handle, pixels


# --- Autolab DIO ---------------------------------------------------------------

def _open_autolab_dio():
    """Connect the Autolab and return (instrument, port, dir_low, dir_output_done).

    Returns (None, None) on any failure, having printed why.
    """
    try:
        import clr
    except Exception as exc:
        print(f"Could not import 'clr' (pythonnet): {exc}")
        print("pip install pythonnet — see query_autolab_setup.md.")
        return None, None

    sdk_dir = os.path.dirname(SDK)
    if sdk_dir and sdk_dir not in sys.path:
        sys.path.append(sdk_dir)
    if not clr.FindAssembly(SDK):
        print(f"Cannot find the SDK assembly at:\n  {SDK}")
        return None, None
    try:
        clr.AddReference(SDK)
        from EcoChemie.Autolab.Sdk import Instrument, DIO
        from System import Enum
    except Exception as exc:
        print(f"Found the SDK assembly but could not load it: {exc}")
        print("Usually a bitness mismatch or a missing .NET runtime.")
        return None, None

    inst = Instrument()
    try:
        inst.AutolabConnection.EmbeddedExeFileToStart = ADX
        inst.set_HardwareSetupFile(HDW)
        inst.Connect()
        if not inst.AutolabConnection.IsConnected:
            print("Connect failed — cell untouched. Close NOVA? Right HDW file?")
            return None, None
    except Exception as exc:
        print(f"Connect failed: {exc}")
        print("Close NOVA / a prior script that still holds the link, or fix HDW.")
        return None, None

    print("Autolab connected  : OK (cell NOT switched on)")

    # DioPortDirection lives in EcoChemie100; reach it via the DIO property's type
    # so we don't depend on that namespace importing cleanly.
    dio = inst.Dio
    # DioPortDirection lives in EcoChemie100; reach the enum type via the DIO
    # property rather than importing that namespace directly.
    dir_type = clr.GetClrType(DIO).GetProperty("DioPortDirection").PropertyType
    output = Enum.Parse(dir_type, "Output")

    ports = dio.DioPortsP1
    if DIO_PORT_INDEX >= len(ports):
        print(f"DioPortsP1 has {len(ports)} ports; DIO_PORT_INDEX={DIO_PORT_INDEX} "
              "is out of range.")
        return None, None
    port = ports[DIO_PORT_INDEX]
    try:
        port.PortDirection = output
    except Exception:
        # Some SDK builds set direction at the DIO level, not per-port.
        dio.DioPortDirection = output
    port.Value = 0  # idle low
    try:
        port_name = str(port.PortName)
    except Exception:
        port_name = f"index {DIO_PORT_INDEX}"
    print(f"DIO port           : {port_name}  (DioPortsP1[{DIO_PORT_INDEX}]) "
          "set to Output, driven low")
    return inst, port


def _pulse(port):
    port.Value = 0
    time.sleep(0.001)
    port.Value = 0xFF          # rising edge -> should trigger the Avantes
    time.sleep(PULSE_WIDTH_S)
    port.Value = 0             # falling edge


# --- the check ----------------------------------------------------------------

def main():
    _hr("AVANTES  <-  AUTOLAB DIO   hardware-trigger check  (cell-safe)")

    avaspec = _load_avaspec()
    if avaspec is None:
        return

    inst = port = handle = None
    try:
        inst, port = _open_autolab_dio()
        if inst is None:
            return

        handle, pixels = _open_avantes(avaspec)
        if handle is None:
            return

        cfg = _build_measconfig(avaspec, pixels)
        if avaspec.AVS_PrepareMeasure(handle, cfg) < 0:
            print("AVS_PrepareMeasure failed — bad measurement config.")
            return

        # Arm for exactly one scan. After this call the device is waiting for the
        # edge; the pulse MUST come after arming or it is silently missed.
        if avaspec.AVS_Measure(handle, 0, 1) < 0:
            print("AVS_Measure failed — spectrometer not armed.")
            return
        print("\nAvantes armed, waiting for the external trigger edge…")

        _pulse(port)
        print(f"Pulsed P1[{DIO_PORT_INDEX}]  low->high->low  "
              f"({PULSE_WIDTH_S * 1000:.1f} ms)")

        deadline = time.time() + TRIGGER_TIMEOUT_S
        ready = False
        while time.time() < deadline:
            r = avaspec.AVS_PollScan(handle)
            if r == 1:
                ready = True
                break
            if r < 0:
                print(f"AVS_PollScan error (code {r}).")
                return
            time.sleep(0.002)

        print()
        _hr("RESULT")
        if ready:
            ts, spectrum = avaspec.AVS_GetScopeData(handle)
            print("Scan completed — the trigger edge reached the Avantes. [OK]")
            print(f"  timestamp {ts},  {len(spectrum)} pixels,  "
                  f"max {max(spectrum):.0f} counts")
            print("\nThe Autolab P1[%d] -> Avantes trigger line works, and the "
                  "polarity is right." % DIO_PORT_INDEX)
        else:
            print(f"No scan after {TRIGGER_TIMEOUT_S:.0f} s — the edge did NOT "
                  "reach the Avantes trigger input.")
            print("\nThings to try, roughly in order:")
            print("  - a different P1 port  (set DIO_PORT_INDEX = 1, 2, …)")
            print("  - the falling edge instead of the rising one "
                  "(swap 0x00/0xFF in _pulse)")
            print("  - confirm which back-panel pin the cable lands on vs. the "
                  "Avantes external-trigger input (SOP §2.1)")
    finally:
        try:
            if handle is not None:
                avaspec.AVS_Done()
        except Exception:
            pass
        try:
            if port is not None:
                port.Value = 0
                port.Release()
        except Exception:
            pass
        try:
            if inst is not None and inst.AutolabConnection.IsConnected:
                inst.Disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
