"""
Print the Gamry's I/E (current) range ladder, as the INSTRUMENT reports it.

The point: `IERange 8` is 600 µA on a Reference 600/620 and 100 µA on an Interface
1010, whose rungs run in 1/10/100 decades rather than 6/60/600. spec-echem reads the
ladder from the instrument for exactly this reason, and this script shows you what it
will see — on a model nobody here has tested against yet, that is the thing to check
first.

Run it on a machine with the 32-bit toolkitpy environment and the potentiostat on USB:

    conda activate SpecEchem32
    python examples/probe_gamry_ladder.py

Read-only and cell-safe: it opens the instrument, reads its identity and its list of
ranges, and closes. **It does not switch the cell on, apply a potential, or measure.**
No spectrometer and no dummy cell are needed.

**More than one Gamry on USB:** every one is listed and read, each opened by its own
section name (`tkp.enum_sections()`). `tkp.Pstat("PSTAT")` alone -- what the rest of
spec-echem uses -- is documented by Gamry for a SINGLE connected instrument; with two
it opens whichever the toolkit picks, and nothing says which.
"""
import os
import struct
import sys

# `python examples/probe_gamry_ladder.py` puts examples/ on sys.path, NOT the repo
# root, so `import spec_echem` fails unless the package happens to be installed.
# Reported 2026-09-28 from a fresh clone. Bootstrap the root so the script runs from
# a clone with nothing installed, which is the whole point of taking it to a rig.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def show_environment():
    """Print the interpreter first: a failed toolkitpy import reads as a dead
    instrument and is nearly always the wrong environment, so the answer should be
    on screen before the question is asked."""
    print(f"python     {sys.version.split()[0]} ({struct.calcsize('P') * 8}-bit)")
    print(f"env        {os.environ.get('CONDA_DEFAULT_ENV') or sys.prefix}")
    try:
        import toolkitpy
        print(f"toolkitpy  YES  {getattr(toolkitpy, '__version__', 'version unknown')}")
        print(f"           {toolkitpy.__file__}")
    except ImportError as exc:
        print(f"toolkitpy  NO   {exc}")
    print()


def main():
    show_environment()
    try:
        from spec_echem.potentiostat import (
            TOOLKITPY_AVAILABLE, TOOLKITPY_IMPORT_ERROR, probe_identity,
            probe_gamry_ladder, gamry_range_full_scale, GAMRY_CURRENT_RANGES)
    except ImportError as exc:
        print(f"Cannot import spec_echem: {exc}\n")
        print(f"Looking in: {_REPO_ROOT}")
        if not os.path.isdir(os.path.join(_REPO_ROOT, "spec_echem")):
            print("There is no spec_echem/ there — run this from inside the clone.")
        else:
            print("The package is there, so this is a missing dependency.")
            print("This script needs numpy and pandas:  pip install numpy pandas")
        return 1

    if not TOOLKITPY_AVAILABLE:
        print(f"toolkitpy is not importable: {TOOLKITPY_IMPORT_ERROR}")
        if struct.calcsize("P") * 8 == 64:
            print("\nThis interpreter is 64-bit and the shipping toolkitpy is 32-bit"
                  " only.\nTry `conda activate SpecEchem32`. (A 64-bit, pip-installable"
                  "\ntoolkitpy is expected from Gamry — when it lands this note is"
                  " stale.)")
        else:
            print("\nThe interpreter is 32-bit, so this is toolkitpy itself: it ships"
                  "\nwith Gamry Framework rather than from pip. Check Framework is"
                  "\ninstalled on this machine.")
        return 1

    from spec_echem.potentiostat import read_gamry_ladder, tkp
    try:
        sections = list(tkp.enum_sections() or [])
    except Exception as exc:  # noqa: BLE001 -- an older toolkitpy may lack it
        print(f"(could not list instruments: {exc}; reading the default one)\n")
        sections = []

    if len(sections) <= 1:
        try:
            label, serial = probe_identity()
            who = f"{label} (serial {serial})" if (label or "").strip() else f"serial {serial}"
        except Exception as exc:  # noqa: BLE001
            print(f"Could not open the potentiostat: {exc}")
            return 1
        print(f"Potentiostat: {who}\n")
        show_ladder(probe_gamry_ladder(), gamry_range_full_scale, GAMRY_CURRENT_RANGES)
        return 0

    print(f"{len(sections)} Gamry instruments connected:")
    for sec in sections:
        print(f"  {sec}")
    print("\nspec-echem itself opens the DEFAULT one (tkp.Pstat('PSTAT')), which Gamry")
    print("documents for a single instrument -- with several connected, unplug the ones")
    print("not in use before a run. Each is read below by name.\n")
    failed = 0
    for sec in sections:
        print("=" * 62)
        print(f"Section: {sec}")
        tkp.toolkitpy_init("spec-echem-ladder")
        pstat = None
        try:
            pstat = tkp.Pstat("PSTAT", sec)
            label, serial = pstat.label(), pstat.serial_no()
            print(f"Potentiostat: {label} (serial {serial})\n")
            show_ladder(read_gamry_ladder(pstat), gamry_range_full_scale,
                        GAMRY_CURRENT_RANGES)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"Could not read it: {type(exc).__name__}: {exc}")
        finally:
            if pstat is not None:
                try:
                    pstat.close()
                except Exception:  # noqa: BLE001
                    pass
                del pstat
            tkp.toolkitpy_close()
        print()
    return 1 if failed else 0


def show_ladder(ladder, gamry_range_full_scale, GAMRY_CURRENT_RANGES):
    if not ladder:
        print("This instrument did not report a range list.")
        print("spec-echem will fall back to the documented Reference 600 table:")
        for i, (amps, lbl) in enumerate(GAMRY_CURRENT_RANGES, start=1):
            print(f"  IERange {i:2d}   {lbl:>8s}   {amps:.3e} A")
        print("\nIf this instrument is NOT a Reference 600/620, those values are wrong")
        print("for it, and the per-segment '% of full scale' advice would be too.")
        return

    print(f"{'IERange':>8}  {'label':>10}  {'full scale':>12}   documented (REF 600)")
    print("-" * 62)
    disagreements = 0
    for index, amps, lbl in ladder:
        documented = gamry_range_full_scale(index)
        same = documented is not None and abs(documented - amps) <= amps * 1e-6
        note = "same" if same else (f"** {documented:.3e} A" if documented
                                    else "** not in the table")
        disagreements += 0 if same else 1
        print(f"{index:8d}  {lbl:>10s}  {amps:12.3e}   {note}")

    print(f"\n{len(ladder)} ranges reported.")
    if disagreements:
        print(f"{disagreements} differ from the documented Reference 600 table — which is")
        print("EXPECTED on another model, and is the whole reason the ladder is read")
        print("from the instrument. spec-echem will use the values above.")
    else:
        print("All agree with the documented Reference 600 table.")


if __name__ == "__main__":
    sys.exit(main())
