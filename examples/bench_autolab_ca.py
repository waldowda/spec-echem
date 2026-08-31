"""
bench_autolab_ca.py — the chronoamperometry bench script for the Autolab.

Closes open item 6 of docs/autolab-run-api.md §4, which is the biggest remaining
gap: THREE of spec-echem's four data types (doping, dedoping, pre-dedoping) are
constant-potential holds, and only CV has been characterised so far.

Unlike the CV script, the CA parameter indices are NOT yet known. This script
establishes them the same way the CV map was established — set a distinctive value,
run, and check the recorded data agrees:

    phase 0  list the CA procedure's commands and every parameter (index, type,
             value). Nothing is energized. This alone usually makes the map obvious:
             a 5.0 is a duration, a 0.0 a potential.
    phase 1  with candidate indices filled in below, apply distinctive values, run,
             and VERIFY from the data:
                 potential -> mean EI_0.CalcPotential ~= HOLD_V
                 duration  -> CalcTime span         ~= HOLD_S
                 interval  -> median dt             ~= INTERVAL_S
             Each is reported CONFIRMED or NOT, so an index is never adopted on the
             strength of it looking plausible.
    phase 2  the same back-to-back question the CV script asks (item 2): does a
             second run's .Signals still carry the first run's points?

It also reports whether the template contains a WAIT command before the hold. That
matters beyond bookkeeping: the standard CV's 5 s FHWait is the window the driver
uses to pulse the trigger after arming the spectrometer. If CA has no such wait,
the trigger timing for doping/dedoping segments needs a different answer.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

Usage:
    python bench_autolab_ca.py
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

# --- what to run -----------------------------------------------------------
# Browse "Standard Nova Procedures" for the chronoamperometry template; the exact
# file name varies by SDK build ("Chrono amperometry.nox", "Chronoamperometry.nox").
NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Chrono amperometry.nox"

ENERGIZE_CELL = False     # False = phase 0 only (parameter map, nothing energized)
RUN_LIFECYCLE = True      # phase 2

# Candidate parameter indices, from what phase 0 prints. Leave None on the first
# run — phase 1 needs at least the potential index to be meaningful.
IDX_POTENTIAL = None      # the hold potential (V)
IDX_DURATION = None       # the hold duration (s)
IDX_INTERVAL = None       # the sampling interval (s), if the template has one

# Distinctive values — deliberately not round defaults, so a parameter that did NOT
# take is obvious in the recorded data rather than coincidentally right.
HOLD_V = 0.35
HOLD_S = 8.0
INTERVAL_S = 0.05

# IdNames worth trying for the hold command; phase 0 prints the real list.
CA_IDS = ("FHChronoAmperometry", "FHChronoAmperometry2", "FHLevel", "FHRecord",
          "Chrono amperometry", "Record signals")
WAIT_IDS = ("FHWait", "Wait time (s)")

HERE = os.path.dirname(os.path.abspath(__file__))


def find_hold_command(proc, idnames):
    """The command that owns the hold. Try known IdNames, then anything that looks
    like a chrono/level/record step from the live list."""
    cmd, used = ac.command(proc, *CA_IDS)
    if cmd is not None:
        return cmd, used
    for idn in idnames:
        low = str(idn).lower()
        if any(k in low for k in ("chrono", "level", "record", "amperometry")):
            got = safe(lambda i=idn: proc.Commands[i])
            if got is not None:
                return got, idn
    return None, None


def check(label, measured, expected, tol, unit=""):
    """Report CONFIRMED / NOT for one index, from the data rather than from hope."""
    if measured is None:
        say(f"    {label:<22} no data — cannot confirm")
        return False
    ok = abs(measured - expected) <= tol
    say(f"    {label:<22} measured {measured:.4g}{unit}, set {expected:.4g}{unit} "
        f"-> {'CONFIRMED' if ok else 'NOT CONFIRMED'}")
    return ok


def main():
    rule("AUTOLAB — CHRONOAMPEROMETRY BENCH (parameter map + verification)")
    say(f"NOX           : {NOX}")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    say(f"indices       : potential={IDX_POTENTIAL} duration={IDX_DURATION} "
        f"interval={IDX_INTERVAL}")
    if ENERGIZE_CELL:
        say("")
        say("*** The cell WILL be energized. 10 kOhm dummy resistor only. ***")

    inst = ac.connect()
    if inst is None:
        say("")
        say("Stopped: no connection. Nothing was energized.")
        return 0

    try:
        # --- phase 0: the map (safe) --------------------------------------
        rule("PHASE 0 — CA command list and parameter map")
        if not os.path.isfile(NOX):
            say(f"  NOX does not exist: {NOX}")
            say("  Browse the Standard Nova Procedures folder and fix NOX at the top.")
            return 1
        proc = ac.load(inst, NOX)
        idnames = ac.list_commands(proc)

        hold, used = find_hold_command(proc, idnames)
        if hold is None:
            say("")
            say("  Could not identify the hold command. Add its IdName (from the list")
            say("  above) to CA_IDS at the top of this script and re-run.")
            return 1
        say(f"  Hold command: {used}")
        values = ac.dump_parameters(hold, used)
        say("")
        say("  Read that list against what a hold needs: a POTENTIAL (V), a DURATION")
        say("  (s) and possibly a SAMPLING INTERVAL (s). Put the indices in")
        say("  IDX_POTENTIAL / IDX_DURATION / IDX_INTERVAL and re-run with")
        say("  ENERGIZE_CELL = True to verify them against recorded data.")

        # The trigger window question.
        wait, wused = ac.command(proc, *WAIT_IDS)
        say("")
        if wait is not None:
            wv = ac.dump_parameters(wait, wused)
            say(f"  WAIT command present ({wused}) — this is the arm-margin window the")
            say("  driver pulses the trigger in, exactly as the standard CV's FHWait is.")
            if wv:
                say(f"  Its duration parameter reads {wv[0]}.")
        else:
            say("  NO wait command in this template. The driver then has no built-in")
            say("  window between Measure() and the hold starting, so trigger timing for")
            say("  doping/dedoping needs its own answer — add an FHWait in NOVA, or")
            say("  pulse DIO before Measure() and accept the skew.")

        if not ENERGIZE_CELL:
            say("")
            say("ENERGIZE_CELL is False — verification skipped. Fill in the indices")
            say("above, put the 10 kOhm in, and re-run with it True.")
            return 0

        # --- phase 1: verify the indices against the data -----------------
        rule("PHASE 1 — apply distinctive values and check the recording")
        if IDX_POTENTIAL is None:
            say("  IDX_POTENTIAL is None — nothing to verify. Fill in the indices from")
            say("  phase 0 first.")
            return 1
        say("")
        say("  applying:")
        ac.set_param(hold, IDX_POTENTIAL, HOLD_V, "hold V")
        if IDX_DURATION is not None:
            ac.set_param(hold, IDX_DURATION, HOLD_S, "duration s")
        if IDX_INTERVAL is not None:
            ac.set_param(hold, IDX_INTERVAL, INTERVAL_S, "interval s")

        ac.switch_cell(inst, True)
        ac.run(proc, inst, live=True)
        ac.switch_cell(inst, False)
        sig = ac.read_signals(hold)
        ac.report_signals(sig)
        ac.write_csv(sig, os.path.join(HERE, "bench_autolab_ca_run1.csv"))

        pot = sig.get("EI_0.CalcPotential") or sig.get("SetpointApplied") or []
        t = sig.get("CalcTime") or []
        n1 = len(sig.get("EI_0.CalcCurrent", []))

        say("")
        say("  Verdicts:")
        check("potential (V)", statistics.mean(pot) if pot else None, HOLD_V, 0.02, " V")
        if IDX_DURATION is not None:
            span = (t[-1] - t[0]) if len(t) > 1 else None
            check("duration (s)", span, HOLD_S, max(0.5, HOLD_S * 0.1), " s")
        if IDX_INTERVAL is not None and len(t) > 2:
            dts = [t[i + 1] - t[i] for i in range(len(t) - 1)]
            check("interval (s)", statistics.median(dts), INTERVAL_S,
                  max(0.005, INTERVAL_S * 0.2), " s")
        say("")
        say("  A NOT CONFIRMED means that index is something else — do not adopt it.")
        say("  Re-read phase 0's list and try the next candidate.")

        # --- phase 2: the contamination question --------------------------
        if RUN_LIFECYCLE:
            rule("PHASE 2 — item 2 for CA: does run 2 reuse run 1's buffer?")
            ac.switch_cell(inst, True)
            ac.run(proc, inst)
            ac.switch_cell(inst, False)
            n2 = len(ac.read_signals(hold).get("EI_0.CalcCurrent", []))
            say("")
            say(f"  run 1: {n1} points, run 2: {n2} points")
            if n1 and n2:
                if abs(n2 - n1) <= max(2, n1 // 100):
                    say("  >> CLEAN: .Signals is replaced per run; no reset needed.")
                elif n2 >= n1 * 1.8:
                    say("  >> CUMULATIVE: the driver MUST reset or reload between")
                    say("     segments, or every segment after the first is contaminated.")
                else:
                    say("  >> UNCLEAR — compare the CSVs before trusting either answer.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)

    rule("NEXT")
    say("Record the confirmed CA index map in docs/autolab-run-api.md — that plus the")
    say("CV map is everything the driver needs for all four data types.")
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_autolab_ca_report.txt"))
    sys.exit(code)
