"""
bench_autolab_ca.py — the chronoamperometry bench script for the Autolab.

Closes open item 6 of docs/autolab-run-api.md §4, which is the biggest remaining
gap: THREE of spec-echem's four data types (doping, dedoping, pre-dedoping) are
constant-potential holds, and only CV has been characterised so far.

Phase 0 on the rig (2026-09-03) established the map: Chrono amperometry.nox is a
three-step template and the hold potential is on the FHSetSetpointPotential command,
not the FHLevel recorder (potential = FHSetSetpointPotential[0], duration =
FHLevel[1], interval = FHLevel[0]; FHWait[0] = 5.0 s is the trigger window). Phase 1
now CONFIRMS that map against recorded data the same way the CV map was confirmed —
apply a distinctive value, run, check the recording agrees:

    phase 0  list the CA procedure's commands and every parameter (index, type,
             value). Nothing is energized. This alone usually makes the map obvious:
             a 5.0 is a duration, a 0.0 a potential.
    phase 1  apply distinctive values (potential on FHSetSetpointPotential,
             duration/interval on FHLevel), run, and VERIFY from the data:
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

# Parameter map from phase 0 on the rig, 2026-09-03. Chrono amperometry.nox is a
# THREE-step template: (Set potential -> Record signals -> i vs t) x3, bracketed by
# cell on/off, with an FHWait(5.0 s) before the first hold. spec-echem needs ONE hold
# per segment, so this bench drives step 1 and leaves steps 2-3 at their defaults
# (three holds on a resistor is harmless; the DRIVER will need to zero or drop the
# extra steps for a real sample — separate note in docs §4 item 6).
#
# The hold POTENTIAL is not a parameter of the recorder command — it is the separate
# FHSetSetpointPotential command, exactly as in the CV template.
IDX_SETPOINT_V = 0        # FHSetSetpointPotential.param[0] — the hold potential (V)
IDX_DURATION = 1          # FHLevel.param[1] — the hold duration (s), default 5.0
IDX_INTERVAL = 0          # FHLevel.param[0] — the sampling interval (s), default 0.01
                          # FHLevel.param[2] is a bool, left untouched
SETPOINT_IDS = ("FHSetSetpointPotential", "Set potential")

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
    say(f"indices       : setpoint_V={IDX_SETPOINT_V} duration={IDX_DURATION} "
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
        say(f"  Hold (recorder) command: {used}")
        ac.dump_parameters(hold, used)
        setpot, sused = ac.command(proc, *SETPOINT_IDS)
        if setpot is not None:
            say("")
            say(f"  Setpoint command: {sused} — this holds the potential, not the recorder")
            ac.dump_parameters(setpot, sused)
        say("")
        say("  Map (phase 0, rig 2026-09-03): potential = FHSetSetpointPotential[0],")
        say("  duration = FHLevel[1], interval = FHLevel[0]. Re-run with ENERGIZE_CELL =")
        say("  True to apply distinctive values and confirm them against the recording.")

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
        if setpot is None:
            say("  Could not find the FHSetSetpointPotential command — cannot set the")
            say("  hold potential. Check SETPOINT_IDS against phase 0's command list.")
            return 1
        say("")
        say("  applying (potential on the setpoint command, duration/interval on FHLevel):")
        ac.set_param(setpot, IDX_SETPOINT_V, HOLD_V, "hold V")
        ac.set_param(hold, IDX_DURATION, HOLD_S, "duration s")
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

        # --- phase 2: the lifecycle question -----------------------------
        # Two distinct questions, and phase 1 on the rig (2026-09-03) showed the
        # first one bites: (a) can a procedure object be re-Measure()d at all, and
        # (b) if so, does run 2's .Signals still carry run 1's points.
        if RUN_LIFECYCLE:
            rule("PHASE 2 — item 2 for CA: re-Measure() the same object, then reload")
            ac.switch_cell(inst, True)
            elapsed2 = ac.run(proc, inst)
            ac.switch_cell(inst, False)
            n2 = len(ac.read_signals(hold).get("EI_0.CalcCurrent", []))
            say("")
            say(f"  run 1: {n1} points ({25.0:.0f}s-ish), run 2: {n2} points "
                f"(Measure() took {elapsed2:.2f}s)")
            reused_object_is_inert = elapsed2 < 1.0
            if reused_object_is_inert:
                say("  >> INERT: a second Measure() on the SAME procedure object did not")
                say("     execute (returned instantly, IsMeasuring never True). The points")
                say("     above are run 1's, still in .Signals. The driver MUST reload the")
                say("     procedure for every segment — a reused object cannot re-measure.")
            elif n1 and n2 and abs(n2 - n1) <= max(2, n1 // 100):
                say("  >> CLEAN: run 2 executed and .Signals was replaced per run.")
            elif n1 and n2 and n2 >= n1 * 1.8:
                say("  >> CUMULATIVE: run 2's .Signals carries run 1's points too — the")
                say("     driver MUST reload or reset between segments.")
            else:
                say("  >> UNCLEAR — compare the CSVs before trusting either answer.")

            say("")
            say("  Now the same question after an explicit reload:")
            proc = ac.load(inst, NOX)
            hold, _ = find_hold_command(proc, ac.list_commands(proc))
            setpot, _ = ac.command(proc, *SETPOINT_IDS)
            ac.set_param(setpot, IDX_SETPOINT_V, HOLD_V, "hold V")
            ac.set_param(hold, IDX_DURATION, HOLD_S, "duration s")
            ac.set_param(hold, IDX_INTERVAL, INTERVAL_S, "interval s")
            ac.switch_cell(inst, True)
            elapsed3 = ac.run(proc, inst)
            ac.switch_cell(inst, False)
            n3 = len(ac.read_signals(hold).get("EI_0.CalcCurrent", []))
            say("")
            say(f"  run 3 (after reload): {n3} points (Measure() took {elapsed3:.2f}s)")
            if elapsed3 >= 1.0 and n1 and abs(n3 - n1) <= max(2, n1 // 100):
                say("  >> reload is a clean reset — run 3 matches run 1. This is what the")
                say("     driver does per segment.")
            else:
                say("  >> reload did NOT behave like run 1 — investigate before the driver")
                say("     relies on it.")
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
