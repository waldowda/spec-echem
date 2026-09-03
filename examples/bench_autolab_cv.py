"""
bench_autolab_cv.py — the standard-CV bench script for the Autolab.

Closes open items 1, 2 and 3 of docs/autolab-run-api.md §4, and produces a CSV of a
known-good CV so the driver's data path can be written against a real file.

    1  MULTI-CYCLE     is CommandParameters[4] really "number of stop crossings"?
                       4 should give two cycles: ScanNumber 1->2, points ~double.
    2  SAMPLER LIFECYCLE  run twice back-to-back without reconnecting — does run 2's
                       .Signals still contain run 1's points? If it does, the driver
                       MUST reset between segments or every segment after the first
                       is contaminated. This is the silent-corruption question.
    3  ABORT           mid-run Abort(): does IsMeasuring go False cleanly, do
                       .Signals hold a partial trace (spec-echem must discard it),
                       and is the instrument ready for the next Measure()?

Phase 0 (parameter map) touches nothing and runs with the cell off, so it is worth
doing even without a dummy cell. Phases 1-3 energize.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

The parameter index map below is from docs/autolab-run-api.md §1, established on
hardware 2026-08-31. Phase 0 re-prints the live values so a template change shows up
immediately rather than as mysterious data.

Usage:
    python bench_autolab_cv.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

# --- what to run -----------------------------------------------------------
NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox"

ENERGIZE_CELL = False     # False = phase 0 only (parameter map, nothing energized)
RUN_MULTICYCLE = True     # item 1
RUN_LIFECYCLE = True      # item 2  <- the important one
RUN_ABORT = True          # item 3
ABORT_AFTER_S = 5.0       # when to pull the plug in the abort test

# CV staircase (FHCyclicVoltammetry2) parameter indices — see autolab-run-api.md §1.
IDX_START, IDX_UPPER, IDX_LOWER = 0, 1, 2
IDX_STEP, IDX_CROSSINGS, IDX_STOP, IDX_SCANRATE = 3, 4, 5, 6

START_V, UPPER_V, LOWER_V, STOP_V = 0.0, 1.0, -1.0, 0.0
STEP_V = 0.00244          # step potential (V)
SCAN_RATE_VS = 0.1        # NOTE: V/s in the SDK; NOVA's UI shows mV/s (0.1 = 100 mV/s)
CROSSINGS_1CYCLE = 2      # 2 crossings = one full cycle
CROSSINGS_2CYCLE = 4

CV_ID = "FHCyclicVoltammetry2"
HERE = os.path.dirname(os.path.abspath(__file__))


def apply_settings(cv, crossings):
    say("")
    say(f"  applying CV settings (crossings={crossings}):")
    ok = True
    ok &= ac.set_param(cv, IDX_START, START_V, "start V")
    ok &= ac.set_param(cv, IDX_UPPER, UPPER_V, "upper vertex V")
    ok &= ac.set_param(cv, IDX_LOWER, LOWER_V, "lower vertex V")
    ok &= ac.set_param(cv, IDX_STEP, STEP_V, "step V")
    ok &= ac.set_param(cv, IDX_CROSSINGS, crossings, "crossings")
    ok &= ac.set_param(cv, IDX_SCANRATE, SCAN_RATE_VS, "scan rate V/s")
    if not ok:
        say("  WARNING: at least one parameter did not take — results below are suspect.")
    return ok


def summarize(sig, label):
    """The three numbers that answer items 1 and 2."""
    ac.report_signals(sig)
    n = len(sig.get("EI_0.CalcCurrent", []))
    scans = sig.get("ScanNumber", [])
    lo, hi = (min(scans), max(scans)) if scans else (None, None)
    t = sig.get("CalcTime", [])
    say(f"  >> {label}: {n} points, ScanNumber {lo}..{hi}, "
        f"CalcTime {t[0]:.3f}..{t[-1]:.3f} s" if t else f"  >> {label}: {n} points")
    return n, lo, hi


def main():
    rule("AUTOLAB — STANDARD CV BENCH")
    say(f"NOX           : {NOX}")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    if ENERGIZE_CELL:
        say("")
        say("*** The cell WILL be energized. 10 kOhm dummy resistor only. ***")

    inst = ac.connect()
    if inst is None:
        say("")
        say("Stopped: no connection. Nothing was energized.")
        return 0

    try:
        # --- phase 0: the parameter map (safe) ---------------------------
        rule("PHASE 0 — command list and live parameter values")
        proc = ac.load(inst, NOX)
        ac.list_commands(proc)
        cv, used = ac.command(proc, CV_ID, "CV staircase")
        if cv is None:
            say(f"  Could not find the CV staircase command ({CV_ID}).")
            return 1
        say(f"  CV staircase command: {used}")
        ac.dump_parameters(cv, used)
        say("")
        say("  Expected (autolab-run-api.md §1): [0] start [1] upper [2] lower")
        say("  [3] step [4] crossings(Int) [5] stop [6] scan rate (V/s).")
        say("  If the types/values above disagree, STOP — the map has moved.")

        if not ENERGIZE_CELL:
            say("")
            say("ENERGIZE_CELL is False — phases 1-3 skipped. Set it True with the")
            say("10 kOhm resistor in place to answer items 1, 2 and 3.")
            return 0

        # --- phase 1: one cycle, the reference run -----------------------
        rule("PHASE 1 — one cycle (reference run + CSV)")
        apply_settings(cv, CROSSINGS_1CYCLE)
        ac.switch_cell(inst, True)
        ac.run(proc, inst, live=True)
        ac.switch_cell(inst, False)
        sig1 = ac.read_signals(cv)
        n1, _, _ = summarize(sig1, "run 1 (one cycle)")
        ac.write_csv(sig1, os.path.join(HERE, "bench_autolab_cv_run1.csv"))

        # --- phase 2: the contamination question -------------------------
        if RUN_LIFECYCLE:
            rule("PHASE 2 — item 2: does a second run reuse the first run's buffer?")
            say("  Running AGAIN with no reconnect and no reload, then comparing counts.")
            ac.switch_cell(inst, True)
            elapsed2 = ac.run(proc, inst)
            ac.switch_cell(inst, False)
            sig2 = ac.read_signals(cv)
            n2, _, _ = summarize(sig2, "run 2 (same procedure object)")
            say("")
            if elapsed2 < 1.0:
                say("  >> INERT: a second Measure() on the SAME procedure object did not")
                say("     execute (returned instantly, IsMeasuring never True). The points")
                say("     above are run 1's, still in .Signals. Not 'clean' — the object")
                say("     simply cannot re-measure. The driver reloads per segment (below).")
                say("     (Matches the CA bench, 2026-09-03.)")
            elif n1 and n2:
                if abs(n2 - n1) <= max(2, n1 // 100):
                    say("  >> CLEAN: run 2 executed and has ~the same count as run 1.")
                    say("     .Signals is replaced per run; no reset needed between segments.")
                elif n2 >= n1 * 1.8:
                    say("  >> CUMULATIVE: run 2 carries run 1's points. The driver MUST")
                    say("     reset the sampler or reload the procedure between segments,")
                    say("     or every segment after the first is contaminated.")
                else:
                    say(f"  >> UNCLEAR: {n1} then {n2}. Look at the CSVs before trusting it.")
            ac.write_csv(sig2, os.path.join(HERE, "bench_autolab_cv_run2.csv"))

            say("")
            say("  Now the same question after an explicit reload:")
            proc = ac.load(inst, NOX)
            cv, _ = ac.command(proc, CV_ID, "CV staircase")
            apply_settings(cv, CROSSINGS_1CYCLE)
            ac.switch_cell(inst, True)
            ac.run(proc, inst)
            ac.switch_cell(inst, False)
            n3, _, _ = summarize(ac.read_signals(cv), "run 3 (after reload)")
            say(f"  >> reload gives {n3} points "
                f"({'same as run 1 — reload is a clean reset' if n1 and abs(n3 - n1) <= max(2, n1 // 100) else 'DIFFERENT from run 1 — investigate'})")

        # --- phase 3: multi-cycle ----------------------------------------
        if RUN_MULTICYCLE:
            rule("PHASE 3 — item 1: is [4] really the crossing count?")
            proc = ac.load(inst, NOX)
            cv, _ = ac.command(proc, CV_ID, "CV staircase")
            apply_settings(cv, CROSSINGS_2CYCLE)
            ac.switch_cell(inst, True)
            ac.run(proc, inst)
            ac.switch_cell(inst, False)
            sig4 = ac.read_signals(cv)
            n4, lo4, hi4 = summarize(sig4, f"crossings={CROSSINGS_2CYCLE}")
            say("")
            if n1 and n4:
                doubled = n4 >= n1 * 1.8
                scanned = (hi4 or 0) >= 2
                say(f"  points doubled: {doubled}   ScanNumber reached 2: {scanned}")
                if doubled and scanned:
                    say("  >> CONFIRMED: [4] is the crossing count; 2 per cycle. The driver")
                    say("     sets it to 2 * settings['cv_cycles'].")
                else:
                    say("  >> NOT confirmed — [4] means something else, or cycles are")
                    say("     reported differently. Do not guess; check the CSV.")
            ac.write_csv(sig4, os.path.join(HERE, "bench_autolab_cv_2cycle.csv"))

        # --- phase 4: abort ----------------------------------------------
        if RUN_ABORT:
            rule("PHASE 4 — item 3: what does Abort() leave behind?")
            proc = ac.load(inst, NOX)
            cv, _ = ac.command(proc, CV_ID, "CV staircase")
            apply_settings(cv, CROSSINGS_1CYCLE)
            ac.switch_cell(inst, True)
            proc.Measure()
            time.sleep(ABORT_AFTER_S)
            say(f"  aborting after {ABORT_AFTER_S:.1f} s "
                f"(IsMeasuring={safe(lambda: proc.IsMeasuring)})")
            safe(lambda: proc.Abort())
            t0 = time.time()
            while safe(lambda: proc.IsMeasuring, False) is True and time.time() - t0 < 30:
                time.sleep(0.25)
            say(f"  after Abort(): IsMeasuring={safe(lambda: proc.IsMeasuring)} "
                f"(took {time.time() - t0:.2f} s to settle)")
            ac.switch_cell(inst, False)
            na, _, _ = summarize(ac.read_signals(cv), "aborted run")
            say("")
            say(f"  >> partial trace present: {bool(na)} ({na} points vs {n1} for a full run)")
            say("     spec-echem DISCARDS an aborted segment, so this only matters for")
            say("     making sure a partial is never mistaken for a complete run.")

            say("")
            say("  Is the instrument ready for another run straight away?")
            proc = ac.load(inst, NOX)
            cv, _ = ac.command(proc, CV_ID, "CV staircase")
            apply_settings(cv, CROSSINGS_1CYCLE)
            ac.switch_cell(inst, True)
            ac.run(proc, inst)
            ac.switch_cell(inst, False)
            nr, _, _ = summarize(ac.read_signals(cv), "run after abort")
            say(f"  >> recovered: {bool(nr)} — {nr} points "
                f"({'matches a normal run' if n1 and abs(nr - n1) <= max(2, n1 // 100) else 'DIFFERENT from run 1'})")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)

    rule("NEXT")
    say("Commit the CSVs and the transcript, then update docs/autolab-run-api.md §4")
    say("with the answers to items 1, 2 and 3.")
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_autolab_cv_report.txt"))
    sys.exit(code)
