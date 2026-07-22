"""
query_autolab.py — Can this PC talk to the Metrohm Autolab from Python?

A standalone, read-only, CELL-SAFE connect probe. It loads the Autolab SDK through
pythonnet, connects to the instrument, reports whether the link came up, and
disconnects. It does NOT turn the cell on, apply a potential, or run any procedure:
connecting and cell power are separate operations in the SDK, so nothing is
energized here.

This file does not import the spec-echem package. It needs the Metrohm AUTOLAB SDK
(v1.11) installed and `pip install pythonnet`. See query_autolab_setup.md.

The connect pattern is adapted from the MIT-licensed pyMetrohmAUTOLAB (Jonah Liu);
we use our own ~15 lines of clr rather than depend on that package.

Usage:
    python query_autolab.py
"""
import os
import sys

# ---------------------------------------------------------------------------
# EDIT THESE THREE to match your install and your instrument model. They all live
# under the AUTOLAB SDK folder created by the SDK installer.
#
#   SDK  the EcoChemie.Autolab.Sdk assembly (NO .dll extension — clr adds it)
#   ADX  the Adk.x hardware driver the SDK launches ("EmbeddedExeFileToStart")
#   HDW  the hardware-setup XML for YOUR model — the folder name IS the model
#        (PGSTAT302N here); pick the .xml that matches your module (e.g. FRA32M)
# ---------------------------------------------------------------------------
SDK = r"C:\Program Files\Metrohm Autolab\autolabsdk\EcoChemie.Autolab.Sdk"
ADX = r"C:\Program Files\Metrohm Autolab\autolabsdk\Hardware Setup Files\Adk.x"
HDW = r"C:\Program Files\Metrohm Autolab\autolabsdk\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml"


def query_autolab():
    print("=" * 64)
    print("METROHM AUTOLAB  (read-only, cell-safe connect probe)")
    print("=" * 64)

    try:
        import clr  # provided by pythonnet
    except Exception as exc:  # ImportError, or a .NET runtime load error
        print(f"Could not import 'clr' (pythonnet): {exc}\n")
        print("Install it with:  pip install pythonnet")
        print("and make sure the Metrohm AUTOLAB SDK is installed. See "
              "query_autolab_setup.md.")
        return

    # Help pythonnet find the assembly: put its folder on the path, then reference it.
    sdk_dir = os.path.dirname(SDK)
    if sdk_dir and sdk_dir not in sys.path:
        sys.path.append(sdk_dir)

    if not clr.FindAssembly(SDK):
        print(f"Cannot find the SDK assembly at:\n  {SDK}\n")
        print("Fix the SDK path at the top of this script (no .dll extension). The "
              "file is created by the AUTOLAB SDK installer.")
        return

    try:
        clr.AddReference(SDK)
        from EcoChemie.Autolab.Sdk import Instrument
    except Exception as exc:
        print(f"Found the assembly but could not load it: {exc}\n")
        print("Usually a bitness mismatch (Python vs the SDK) or a missing .NET "
              "runtime. See query_autolab_setup.md.")
        return

    inst = Instrument()
    connected = False
    try:
        # Point the SDK at the hardware driver + your model's setup file, then connect.
        # NOTE: none of this energizes the cell — cell power is a separate Ei call we
        # deliberately never make.
        inst.AutolabConnection.EmbeddedExeFileToStart = ADX
        inst.set_HardwareSetupFile(HDW)
        inst.Connect()
        connected = inst.AutolabConnection.IsConnected
    except Exception as exc:  # noqa: BLE001 — diagnostic: report and move on
        print(f"Connect failed: {exc}\n")
        print("Common causes: the hardware-setup XML doesn't match this instrument, "
              "or another program (NOVA / a prior script) still holds the link — "
              "close it and retry.")
    finally:
        # Release the link so NOVA / the next run can use the instrument.
        try:
            if inst.AutolabConnection.IsConnected:
                inst.Disconnect()
        except Exception:  # noqa: BLE001
            pass

    if connected:
        print("Connected OK — Python can talk to the Autolab. [OK]")
        print(f"Hardware setup file : {HDW}")
        print("\n(The cell was NOT turned on and no procedure was run — this is a "
              "connection check only.)")
    else:
        print("Not connected. See the messages above and query_autolab_setup.md.")


if __name__ == "__main__":
    query_autolab()
