"""
probe_current_ranges.py -- which current ranges does THIS instrument actually have?

`AUTOLAB_CURRENT_RANGES` lists twenty members, CR19_1pA through CR00_1000A, because
that is what the SDK's enum type defines for the whole Autolab family. It is not a
statement about the instrument on this bench, and the GUI offers all twenty.

So: set each one with the cell OFF and read it back. A member that does not stick, or
that raises, is not available here. Nothing is driven and no potential is applied --
setting a range only configures the current amplifier.

Worth knowing because the ranges run a long way past anything an OMIEC film should
ever see. 10 mA through a polymer film is already a lot; an amp is not a measurement,
it is an accident.

    >> Safe with a dummy, or with nothing connected. Cell stays OFF throughout. <<

Usage:
    python probe_current_ranges.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

UNITS = {"pA": 1e-12, "nA": 1e-9, "uA": 1e-6, "mA": 1e-3, "A": 1.0}


def full_scale_a(member):
    tail = str(member).split("_", 1)[-1]
    for suffix in sorted(UNITS, key=len, reverse=True):
        if tail.endswith(suffix):
            try:
                return float(tail[: -len(suffix)]) * UNITS[suffix]
            except ValueError:
                return None
    return None


def main():
    rule("probe_current_ranges -- what this instrument actually offers")
    inst = ac.connect()
    if inst is None:
        return
    try:
        ac.cell_off_quietly(inst)
        from EcoChemie.Autolab.Sdk import EI
        from System import Enum

        members = list(Enum.GetNames(type(EI.EICurrentRange.CR10_1mA)))
        say("  The enum type defines %d members." % len(members))
        say("")
        say("  %-14s %12s  %-10s %s" % ("member", "full scale", "accepted", "reads back as"))

        accepted, rejected = [], []
        for name in members:
            fs = full_scale_a(name)
            try:
                inst.Ei.CurrentRange = getattr(EI.EICurrentRange, name)
                back = str(inst.Ei.CurrentRange)
                ok = (back == name)
                if ok:
                    accepted.append((name, fs))
                else:
                    rejected.append((name, fs, back))
                say("  %-14s %12s  %-10s %s"
                    % (name, ("%.3g" % fs) if fs else "?", "yes" if ok else "NO", back))
            except Exception as exc:  # noqa: BLE001
                rejected.append((name, fs, "raised"))
                say("  %-14s %12s  %-10s %s"
                    % (name, ("%.3g" % fs) if fs else "?", "NO", str(exc)[:40]))

        rule("Summary")
        say("  accepted : %d" % len(accepted))
        say("  rejected : %d" % len(rejected))
        if accepted:
            fss = [fs for _n, fs in accepted if fs]
            if fss:
                say("  span     : %.3g A to %.3g A full scale" % (min(fss), max(fss)))
        say("")
        say("  Anything above ~10 mA is far beyond what an OMIEC film should draw.")
        say("  A range being SETTABLE is not a statement that it is safe to use on a")
        say("  sample -- it sets what the instrument will source before it flags an")
        say("  overload, so an oversized range removes the protection an overload")
        say("  would otherwise give a film.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
        ac.write_transcript(os.path.join(HERE, "probe_current_ranges_report.txt"))


if __name__ == "__main__":
    main()
