"""
bench_live_cv.py — measure the live-CV straddle, and whether .Signals fills as it goes.

Two questions, one CV, five minutes on a dummy resistor. Both come out of the live-CV
glitch reported from 20260916_test1 (TODO.md, "Live CV plot shows points the recorded
data does not"): the trace runs along the line, jumps to a point OFF it, and comes back.

    A  STRADDLE RATE   Ei.Potential and Ei.Current are two reads of ONE latch. If the
                       procedure's recorder refreshes that latch between them, the pair
                       describes no instant: sample N's potential against sample N+1's
                       current. Reading the potential AGAIN afterwards detects it — an
                       unchanged value means nothing moved underneath.

                       spec_echem/potentiostat.py::_read_ei_pair now guards against this
                       and re-takes the sample. The guard is correct whatever the rate,
                       so this script is not needed to justify it — it says how often the
                       thing actually happens, which is the part only the rig knows.

    B  .Signals MID-RUN  Does the recorder's array fill as the run proceeds, or only at
                       the end? This decides whether the live plot could be drawn from
                       the recorder instead of the latch, which would remove the race by
                       construction. Evidence so far says NO: Abort() five seconds in
                       leaves .Signals completely empty (2026-09-03), which is what a
                       buffer that materialises at completion would do — but an abort
                       that discards its buffer looks identical from outside. A count
                       that CLIMBS here settles it; a count stuck at 0 closes the option.

Phase 0 connects, loads the procedure and prints what it found. It energizes nothing and
needs no cell, so run it first.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

A film would work too, but nothing here is about the sample: the questions are about the
SDK, and a resistor cannot be damaged by a sweep.

Usage:
    python bench_live_cv.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# --- what to run -----------------------------------------------------------
NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox"

ENERGIZE_CELL = False     # False = phase 0 only: connect, load, report, energize nothing

CV_ID = "FHCyclicVoltammetry2"

# CV staircase parameter indices — docs/autolab-run-api.md §1, hardware 2026-08-31.
IDX_START, IDX_UPPER, IDX_LOWER = 0, 1, 2
IDX_STOP, IDX_CROSSINGS, IDX_STEP, IDX_RATE = 3, 4, 5, 6

# A sweep the glitch actually appeared on: 20260916_test1 ran 100 mV/s with 10 mV steps.
# Slower or coarser changes how often the recorder refreshes, which is the thing being
# measured — so leave these alone unless you are deliberately varying them.
START_V, UPPER_V, LOWER_V, STOP_V = 0.0, 0.0, -0.5, 0.0
CROSSINGS = 2             # one cycle
STEP_V = 0.010
RATE_V_S = 0.100

# The driver samples once per spectrum, in a 100 ms slot. Match it: a slower poll would
# under-report straddles simply by looking less often.
POLL_S = 0.100


def apply_settings(cv):
    ac.set_param(cv, IDX_START, START_V, "start V")
    ac.set_param(cv, IDX_UPPER, UPPER_V, "upper V")
    ac.set_param(cv, IDX_LOWER, LOWER_V, "lower V")
    ac.set_param(cv, IDX_STOP, STOP_V, "stop V")
    ac.set_param(cv, IDX_CROSSINGS, CROSSINGS, "stop crossings")
    ac.set_param(cv, IDX_STEP, STEP_V, "step V")
    ac.set_param(cv, IDX_RATE, RATE_V_S, "scan rate V/s")


def signal_count(cmd):
    """How many points the recorder's first signal holds RIGHT NOW.

    Deliberately cheap and deliberately forgiving: this runs inside the poll loop of a
    live measurement, and a read that raises mid-run is itself an answer (it would mean
    the array is locked while measuring), so it is reported rather than propagated.
    """
    sigs = safe(lambda: cmd.Signals)
    if sigs is None:
        return None
    try:
        first = list(sigs)[0]
        return len(list(first.ValueAsObject))
    except Exception as exc:   # noqa: BLE001 — "it raised" IS a result here
        return f"raised: {type(exc).__name__}"


def main():
    rule("bench_live_cv — straddle rate, and whether .Signals fills as it goes")
    say(f"ENERGIZE_CELL = {ENERGIZE_CELL}")
    say(f"poll {POLL_S * 1000:.0f} ms, matching the driver's once-per-spectrum sampling")

    inst = ac.connect()
    if inst is None:
        return 1

    try:
        proc = ac.load(inst, NOX)
        if proc is None:
            return 1
        cv, _ = ac.command(proc, CV_ID, "CV staircase")
        if cv is None:
            say("Could not find the CV command — is this the standard procedure?")
            return 1

        rule("PHASE 0 — what is there (nothing is energized)")
        ac.dump_parameters(cv, "CV staircase, as loaded")
        say("")
        say(f"  .Signals before any run: {signal_count(cv)} points")
        say("  (0 or None is expected — nothing has measured yet.)")

        if not ENERGIZE_CELL:
            say("")
            say("ENERGIZE_CELL is False, so stopping here. Connect the 10 kOhm dummy,")
            say("set it True, and run again.")
            return 0

        rule("PHASE 1 — one CV, watched")
        apply_settings(cv)

        straddles = 0
        pairs = 0
        failures = 0          # Sample() itself refused
        counts = []           # (elapsed, points) as the run proceeds
        worst = 0.0           # biggest potential move seen across one pair

        def watch(_inst, _proc, elapsed):
            nonlocal straddles, pairs, failures, worst
            # Exactly what pump() does, in the same order.
            try:
                _inst.Ei.Sampler.Sample()
            except Exception:  # noqa: BLE001
                failures += 1
                return
            before = safe(lambda: float(_inst.Ei.Potential))
            current = safe(lambda: float(_inst.Ei.Current))
            after = safe(lambda: float(_inst.Ei.Potential))
            if before is None or after is None or current is None:
                failures += 1
                return
            pairs += 1
            if before != after:
                straddles += 1
                worst = max(worst, abs(after - before))
                if straddles <= 5:      # a few examples, not a flood
                    say(f"    straddle at t={elapsed:5.2f}s: "
                        f"E {before:+.4f} -> {after:+.4f} V "
                        f"({(after - before) * 1000:+.1f} mV) with I={current:.3e} A")
            counts.append((elapsed, signal_count(cv)))

        ac.switch_cell(inst, True)
        ac.run(proc, inst, poll=POLL_S, watch=watch)
        ac.switch_cell(inst, False)

        rule("A — STRADDLE RATE")
        if not pairs:
            say("No pairs sampled — the watcher never ran. Check POLL_S against the run.")
        else:
            pct = 100.0 * straddles / pairs
            say(f"  {straddles} straddled of {pairs} pairs sampled ({pct:.1f}%)")
            say(f"  biggest potential move across one pair: {worst * 1000:.1f} mV")
            say(f"  Sample()/read failures: {failures}")
            say("")
            if straddles:
                say("  >> CONFIRMED on hardware: the pair really can span a refresh, so")
                say("     _read_ei_pair's re-take is doing something real. At one step of")
                say(f"     {STEP_V * 1000:.0f} mV, a straddled point sits that far off the")
                say("     trace — which is the wedge.")
            else:
                say("  >> Not seen in this run. That does NOT clear the mechanism: the")
                say("     screenshot showed it happening, and a 10 kOhm dummy at one scan")
                say("     rate is one set of conditions. The guard costs a single extra")
                say("     property read, so it stays either way.")

        rule("B — DOES .Signals FILL DURING THE RUN?")
        seen = [c for _, c in counts if isinstance(c, int)]
        if not counts:
            say("  never sampled")
        elif not seen:
            say(f"  every mid-run read failed: {counts[0][1]}")
            say("  >> If it raised while measuring, the array is unavailable mid-run —")
            say("     the live plot cannot come from the recorder, and the latch stays")
            say("     the only source.")
        else:
            say(f"  first {counts[0][0]:.2f}s: {counts[0][1]}    "
                f"last {counts[-1][0]:.2f}s: {counts[-1][1]}")
            say(f"  min {min(seen)}, max {max(seen)}, distinct values {len(set(seen))}")
            after_run = signal_count(cv)
            say(f"  after the run finished: {after_run}")
            say("")
            if len(set(seen)) > 1 and max(seen) > min(seen):
                say("  >> IT CLIMBS. The recorder's array is readable as it fills, so the")
                say("     live plot COULD be drawn from it instead of the latch — one")
                say("     source of truth, no race by construction. Worth designing.")
            elif seen and isinstance(after_run, int) and after_run > max(seen):
                say("  >> FLAT during the run, full afterwards. The array materialises at")
                say("     completion, so it cannot feed a live plot. The latch sampling in")
                say("     pump() stays, and the straddle guard is the whole fix.")
                say("     Close the 'not building the live CV trace from the latch' item.")
            else:
                say("  >> Inconclusive — compare against the run length and step count.")

        rule("NEXT")
        say("Commit this transcript. Then, in TODO.md under the live-CV section:")
        say("  - record the straddle rate beside the guard;")
        say("  - answer or close the '.Signals as a live source' bullet with section B.")
        say("Also worth doing while the rig is open: watch a live CV in the GUI and see")
        say("whether the wedge is gone. That is the actual acceptance test for the guard.")
        return 0
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_live_cv_report.txt"))
    sys.exit(code)
