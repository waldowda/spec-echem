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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# --- what to run -----------------------------------------------------------
NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox"

ENERGIZE_CELL = True      # False = phase 0 only: connect, load, report, energize nothing

CV_ID = "FHCyclicVoltammetry2"

# CV staircase parameter indices — docs/autolab-run-api.md §1, hardware 2026-08-31.
# STEP IS [3] AND STOP IS [5]. That is swapped relative to the order the NOVA manual
# prints them in, and this script had the manual's order until 2026-09-24: it therefore
# wrote step=0.0, and a zero-step staircase records 0 points and stops early while still
# looking like a successful run. The as-loaded defaults are the tell — [3] is 0.00244
# (a step), [5] is 0.0 (a stop potential).
IDX_START, IDX_UPPER, IDX_LOWER = 0, 1, 2
IDX_STEP, IDX_CROSSINGS, IDX_STOP, IDX_RATE = 3, 4, 5, 6

# A sweep the glitch actually appeared on: 20260916_test1 ran 100 mV/s with 10 mV steps.
# Slower or coarser changes how often the recorder refreshes, which is the thing being
# measured — so leave the step and rate alone unless deliberately varying them.
# The WINDOW is the OMIEC working window, -0.5 to +0.8 V: the point is to provoke the
# glitch under the conditions it was reported under. On a 10 kOhm dummy +0.8 V is 80 uA.
# A FILM must not go past +0.7 V.
START_V, UPPER_V, LOWER_V, STOP_V = 0.0, 0.8, -0.5, 0.0
CROSSINGS = 8             # four cycles. Path is 0.8 + 1.3 + 0.5 = 2.6 V per cycle,
                          # so ~26 s each at 100 mV/s, ~110 s in total with the pre-wait.
STEP_V = 0.010
RATE_V_S = 0.100

# The driver samples once per spectrum, in a 100 ms slot, but the per-pair straddle
# probability does not depend on how often we LOOK. It is set by the gap between the two
# reads against the latch's refresh interval, and that gap is a property of the SDK call:
# MEASURED at ~5.0 ms per latch read (bench_ei_sampling_report.txt, 500 trials). So
# polling flat out buys statistics without biasing the per-pair rate, and the driver's
# 100 ms behaviour follows from the per-pair number.
POLL_S = 0.0

# .Signals costs a full list() of the array, so sampling it every poll would dominate the
# loop. Every Nth poll is plenty to see whether it climbs.
SIGNAL_EVERY = 25


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
        traj = []             # (elapsed, E, I) actually sampled
        moved = 0             # polls where E differed from the PREVIOUS poll
        gaps = []             # seconds from the first E read to the second

        def watch(_inst, _proc, elapsed):
            nonlocal straddles, pairs, failures, worst, moved
            # Exactly what pump() does, in the same order.
            try:
                _inst.Ei.Sampler.Sample()
            except Exception:  # noqa: BLE001
                failures += 1
                return
            t_before = time.time()
            before = safe(lambda: float(_inst.Ei.Potential))
            current = safe(lambda: float(_inst.Ei.Current))
            after = safe(lambda: float(_inst.Ei.Potential))
            t_after = time.time()
            if before is None or after is None or current is None:
                failures += 1
                return
            pairs += 1
            gaps.append(t_after - t_before)
            # Did the latch move between POLLS? If it never does, a zero straddle count
            # is structurally guaranteed and says nothing about the mechanism.
            if traj and before != traj[-1][1]:
                moved += 1
            traj.append((elapsed, before, current))
            if before != after:
                straddles += 1
                worst = max(worst, abs(after - before))
                if straddles <= 5:      # a few examples, not a flood
                    say(f"    straddle at t={elapsed:5.2f}s: "
                        f"E {before:+.4f} -> {after:+.4f} V "
                        f"({(after - before) * 1000:+.1f} mV) with I={current:.3e} A")
            if pairs % SIGNAL_EVERY == 0:
                counts.append((elapsed, signal_count(cv)))

        ac.switch_cell(inst, True)
        ac.run(proc, inst, poll=POLL_S, watch=watch)
        ac.switch_cell(inst, False)

        rule("A0 — WAS THE LATCH EVEN LIVE?")
        if not traj:
            say("  nothing sampled.")
        else:
            es = [e for _, e, _ in traj]
            iss = [i for _, _, i in traj]
            say(f"  E  first {es[0]:+.4f} V   last {es[-1]:+.4f} V   "
                f"min {min(es):+.4f}   max {max(es):+.4f}")
            say(f"  I  first {iss[0]:.3e} A   min {min(iss):.3e}   max {max(iss):.3e}")
            say(f"  distinct E values: {len(set(es))} of {len(es)} samples")
            say(f"  E changed from the previous poll on {moved} of {len(traj) - 1} polls")
            if gaps:
                gs = sorted(gaps)
                say(f"  read gap (E read -> E read), the straddle window: "
                    f"median {gs[len(gs) // 2] * 1000:.2f} ms   "
                    f"min {gs[0] * 1000:.2f}   max {gs[-1] * 1000:.2f}")
            say("")
            if len(set(es)) <= 1:
                say("  >> THE LATCH NEVER MOVED. Section A below is then meaningless:")
                say("     no refresh can land between two reads if nothing refreshes.")
                say("     Investigate this FIRST, before reading anything into the rate.")
            else:
                say("  >> The latch tracked the sweep, so a refresh could land between")
                say("     the two reads. Section A's rate is interpretable.")

        rule("A — STRADDLE RATE")
        if not pairs:
            say("No pairs sampled — the watcher never ran. Check POLL_S against the run.")
        else:
            pct = 100.0 * straddles / pairs
            say(f"  {straddles} straddled of {pairs} pairs sampled ({pct:.1f}%)")
            say(f"  biggest potential move across one pair: {worst * 1000:.1f} mV")
            say(f"  Sample()/read failures: {failures}")
            say("")
            # A count alone is not the finding. A wedge is a point displaced by about
            # one STEP; a sub-millivolt move is the latch's own noise and would be
            # invisible on the plot. Gate the conclusion on the MAGNITUDE.
            wedge_scale = worst >= 0.5 * STEP_V
            if straddles and wedge_scale:
                say("  >> CONFIRMED on hardware: the pair really can span a refresh, and")
                say(f"     the move is wedge-scale against the {STEP_V * 1000:.0f} mV step,")
                say("     so _read_ei_pair's re-take is doing something real.")
            elif straddles:
                say(f"  >> Straddles seen, but the biggest is {worst * 1000:.3f} mV against a")
                say(f"     {STEP_V * 1000:.0f} mV step — that is latch noise, not a wedge. It")
                say("     would be invisible on the plot. This does NOT confirm the")
                say("     mechanism behind the reported glitch.")
            else:
                say("  >> Not seen in this run. That does NOT clear the mechanism: the")
                say("     screenshot showed it happening, and a 10 kOhm dummy at one scan")
                say("     rate is one set of conditions. The guard costs a single extra")
                say("     property read, so it stays either way.")

        rule("B — DOES .Signals FILL DURING THE RUN?")
        # signal_count() only ever looked at signal[0]. Name every one of them: an empty
        # index 0 beside a full index 3 looks identical to a run that recorded nothing.
        after_all = ac.read_signals(cv)
        if after_all:
            say("  every signal AFTER the run, by name:")
            for name, vals in after_all.items():
                say(f"    {name:<28} {len(vals)} points")
        else:
            say("  read_signals() returned nothing at all after the run.")
        say("")
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
