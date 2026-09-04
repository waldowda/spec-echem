"""
probe_nox_dio.py — Is the P1.A trigger step reachable through the SDK?

READ-ONLY and CELL-SAFE. Connects, loads procedures, prints what commands they
contain, and looks for the digital-output step. It never calls Measure() and
never switches the cell on.

Why it matters: `AutolabPotentiostat.fire()` currently sleeps
`autolab_pulse_delay_s` and pulses P1.A **from Python**, aiming at the variable
`FHPreCurrentRangingCV` gap — a host-timed guess (measured -51 ms skew, but it
jitters with the OS). If the Autolab fires the edge itself from inside the
procedure, Python leaves the timing path entirely and the residual skew becomes
sub-millisecond.

A byte-level scan of the .nox files on 2026-09-04 found `HDio` / `Dio_0` /
`DioGroup` / `DioPorts` / `P1.A` / `HOptionGetSetValuesPulse` inside
PC_SpectralChronoAmperometry. What that scan CANNOT say is whether the step is a
top-level Command (which `_dio_step_present()` looks for, by scanning
Commands.IdNames for "dio") or an OPTION hanging off a command such as
FHGetSetValues — in which case the driver's guard would never see it and would
refuse a procedure that really does fire.

This script settles that. Usage:  python probe_nox_dio.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autolab_common import connect, load, say  # noqa: E402

NOVA_PROCEDURES = os.path.join(os.path.expanduser("~"), "Documents",
                               "Nova 2.1", "Procedures")
SDK_PROCEDURES = (r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1"
                  r"\Standard Nova Procedures")

TARGETS = [
    ("stock CV (what we run now)", os.path.join(SDK_PROCEDURES, "Cyclic voltammetry.nox")),
    ("stock CA (what we run now)", os.path.join(SDK_PROCEDURES, "Chrono amperometry.nox")),
    ("spectral CA — has P1.A in its bytes",
     os.path.join(NOVA_PROCEDURES, "PC_SpectralChronoAmperometry_0.36-0.8V.nox")),
    ("Sung-Joo's spectro CV", os.path.join(NOVA_PROCEDURES, "spectroelectrochem_CV.nox")),
]


def describe(proc, label):
    say("=" * 70)
    say(label)
    try:
        idnames = list(proc.Commands.IdNames)
    except Exception as exc:  # noqa: BLE001
        say(f"  could not read Commands.IdNames: {exc}")
        return
    say(f"  {len(idnames)} commands:")
    for i, name in enumerate(idnames):
        say(f"    [{i:2d}] {name}")

    hits = [n for n in idnames if "dio" in n.lower()]
    say(f"  commands matching 'dio' (what the driver's guard looks for): {hits or 'NONE'}")

    # If it is not a command, is it an option on one? Options are where NOVA hides
    # "Autolab control" digital I/O, which is the leading theory for why "FHDIO"
    # cannot be found as a command.
    say("  scanning each command's options for a digital-output step ...")
    found_any = False
    for i, name in enumerate(idnames):
        try:
            cmd = list(proc.Commands)[i]
        except Exception:  # noqa: BLE001
            continue
        for attr in ("Options", "CommandOptions", "HOptions"):
            opts = getattr(cmd, attr, None)
            if opts is None:
                continue
            try:
                labels = [str(o) for o in opts]
            except Exception:  # noqa: BLE001
                try:
                    labels = list(opts.IdNames)
                except Exception:  # noqa: BLE001
                    continue
            dio = [l for l in labels if "dio" in l.lower() or "p1." in l.lower()]
            if dio:
                found_any = True
                say(f"    {name}.{attr}: {dio}")
    if not found_any:
        say("    no option-level DIO found (or options are not exposed by this SDK)")


def main():
    inst = connect()
    if inst is None:
        return 1
    try:
        for label, path in TARGETS:
            if not os.path.exists(path):
                say("=" * 70)
                say(f"{label}: NOT FOUND at {path}")
                continue
            try:
                proc = load(inst, path)
            except Exception as exc:  # noqa: BLE001
                say("=" * 70)
                say(f"{label}: LoadProcedure failed: {exc}")
                continue
            describe(proc, f"{label}\n  {path}")
    finally:
        try:
            inst.Disconnect()
        except Exception:  # noqa: BLE001
            pass
        say("=" * 70)
        say("Disconnected. Nothing was measured; the cell was never switched on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
