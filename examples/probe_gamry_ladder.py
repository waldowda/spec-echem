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
"""
import sys


def main():
    from spec_echem.potentiostat import (TOOLKITPY_AVAILABLE, TOOLKITPY_IMPORT_ERROR,
                                         probe_identity, probe_gamry_ladder,
                                         gamry_range_full_scale, GAMRY_CURRENT_RANGES)

    if not TOOLKITPY_AVAILABLE:
        print(f"toolkitpy is not importable here: {TOOLKITPY_IMPORT_ERROR}")
        print("\nThis is almost always the wrong conda environment rather than the")
        print("instrument: toolkitpy is 32-bit only. Try `conda activate SpecEchem32`.")
        return 1

    try:
        label, serial = probe_identity()
        who = f"{label} (serial {serial})" if (label or "").strip() else f"serial {serial}"
    except Exception as exc:  # noqa: BLE001
        print(f"Could not open the potentiostat: {exc}")
        return 1
    print(f"Potentiostat: {who}\n")

    ladder = probe_gamry_ladder()
    if not ladder:
        print("This instrument did not report a range list.")
        print("spec-echem will fall back to the documented Reference 600 table:")
        for i, (amps, lbl) in enumerate(GAMRY_CURRENT_RANGES, start=1):
            print(f"  IERange {i:2d}   {lbl:>8s}   {amps:.3e} A")
        print("\nIf this instrument is NOT a Reference 600/620, those values are wrong")
        print("for it, and the per-segment '% of full scale' advice would be too.")
        return 0

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
