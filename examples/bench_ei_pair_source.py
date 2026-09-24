"""
bench_ei_pair_source.py — why does the live CV plot points the recorded data does not?

spec_echem/potentiostat.py::_read_ei_pair rests on a stated premise: that Ei.Potential
and Ei.Current are "two separate reads of a single latch", so re-reading the potential
after the current detects any refresh landing between them. 20260924_test1 showed nine
glitches on the live plot with ZERO dropped samples in the log, and a CV.txt (from the
recorder, echem_from_signals) with zero points off the line out of 480 on a 10 kOhm
dummy. So the guard never fires, the glitch happens anyway, and only the latch stream
carries it.

A resistor is what makes this measurable: the true CV is a straight line, so a
mismatched (E, I) pair is unambiguous, and — the part that matters — its SIZE converts
straight back into how stale the potential was:

        E_implied = (I - b) * R        what the current says the potential was
        staleness = E_implied - E      in volts, and in staircase steps

A stale-E/fresh-I pair displaced by a whole number of steps is the wedge, and it names
its own cause. Anything displaced by a fraction of a step is noise.

Two earlier mistakes this version fixes:

  * The outlier threshold was 10x the median residual. On this dummy one step of stale
    potential is 10 mV / 9873 ohm = 1.01 uA against a 0.35 uA median residual — only
    2.9x — so a genuine single-step wedge sat BELOW the cutoff and was never counted.
    The threshold is now scaled to the physics (fractions of a step-equivalent), not to
    the noise.
  * "Only I changed on this poll" was read as proof of two independent latches. It is
    not: if Ei.Potential reports the commanded staircase level it is quantized, so it is
    bit-identical across refreshes within one step while the measured current wanders by
    noise — a single latch produces exactly that. Section 1 now TESTS the quantization
    instead of inferring, and asks the sharper question: while E is frozen, does I move
    by a step-equivalent (stale E) or only by noise (one latch, behaving)?

Every sample is written to CSV so the analysis can be redone without re-energizing.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

Usage:
    python bench_ei_pair_source.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "bench_ei_pair_source_samples.csv")

NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox"

ENERGIZE_CELL = True      # dummy resistor only

CV_ID = "FHCyclicVoltammetry2"

# STEP IS [3], STOP IS [5] — swapped relative to the NOVA manual's printed order.
# docs/autolab-run-api.md lines 101/103. Getting this wrong writes step=0.0, which runs
# a degenerate staircase that records nothing while still looking like a clean run.
IDX_START, IDX_UPPER, IDX_LOWER = 0, 1, 2
IDX_STEP, IDX_CROSSINGS, IDX_STOP, IDX_RATE = 3, 4, 5, 6

# The window 20260924_test1 actually ran, so the glitch count here is comparable.
START_V, UPPER_V, LOWER_V, STOP_V = 0.0, 0.7, -0.5, 0.0
CROSSINGS = 4             # two cycles, ~48 s of sweep, ~480 polls: the same order as
                          # the 481-point GUI segment that showed nine glitches.
STEP_V = 0.010
RATE_V_S = 0.100

# pump()'s cadence. This MUST match the driver: the question is not how often the latch
# refreshes in the abstract, it is what the driver sees at its own poll rate.
POLL_S = 0.100

# The staircase does not start when Measure() returns — the template walks its setup
# commands first (MEASURED 1.151 s in 20260924_test1's recorder, and the six startup
# artifacts of the previous run were all before 1.05 s). Samples before this are a
# separate phenomenon from a mid-sweep wedge, so they are reported apart and kept out
# of the fit rather than allowed to drag it.
SWEEP_STARTS_S = 1.30

# A pair displaced by at least this fraction of one staircase step is not noise.
WEDGE_STEPS = 0.5


def main():
    rule("bench_ei_pair_source - how stale is the potential in a live (E, I) pair?")
    say(f"ENERGIZE_CELL = {ENERGIZE_CELL}   poll {POLL_S * 1000:.0f} ms (pump's cadence)")
    say(f"window {LOWER_V:+.2f} to {UPPER_V:+.2f} V, {STEP_V * 1000:.0f} mV steps, "
        f"{RATE_V_S * 1000:.0f} mV/s, {CROSSINGS} crossings")

    inst = ac.connect()
    if inst is None:
        return 1
    try:
        proc = ac.load(inst, NOX)
        if proc is None:
            return 1
        cv, _ = ac.command(proc, CV_ID, "CV staircase")
        if cv is None:
            say("Could not find the CV command.")
            return 1
        if not ENERGIZE_CELL:
            say("ENERGIZE_CELL is False - nothing energized, stopping.")
            return 0

        ac.set_param(cv, IDX_START, START_V, "start V")
        ac.set_param(cv, IDX_UPPER, UPPER_V, "upper V")
        ac.set_param(cv, IDX_LOWER, LOWER_V, "lower V")
        ac.set_param(cv, IDX_STOP, STOP_V, "stop V")
        ac.set_param(cv, IDX_CROSSINGS, CROSSINGS, "stop crossings")
        ac.set_param(cv, IDX_STEP, STEP_V, "step V")
        ac.set_param(cv, IDX_RATE, RATE_V_S, "scan rate V/s")

        samples = []          # (t, e_before, i, e_after)

        def watch(_inst, _proc, elapsed):
            # pump()'s exact order: Sample(), then the overload flags (which cost real
            # time and sit BETWEEN the refresh and the pair), then the pair.
            try:
                _inst.Ei.Sampler.Sample()
            except Exception:  # noqa: BLE001
                return
            safe(lambda: bool(_inst.Ei.PotentialOverload))
            safe(lambda: bool(_inst.Ei.CurrentOverload))
            e0 = safe(lambda: float(_inst.Ei.Potential))
            i0 = safe(lambda: float(_inst.Ei.Current))
            e1 = safe(lambda: float(_inst.Ei.Potential))
            if e0 is None or i0 is None or e1 is None:
                return
            samples.append((elapsed, e0, i0, e1))

        ac.switch_cell(inst, True)
        ac.run(proc, inst, poll=POLL_S, watch=watch)
        ac.switch_cell(inst, False)

        # --- raw first, so nothing below can cost us the data -------------------
        try:
            with open(CSV_PATH, "w", encoding="utf-8") as fh:
                fh.write("t_s,E_before_V,I_A,E_after_V\n")
                for t, e0, i0, e1 in samples:
                    fh.write(f"{t:.6f},{e0:.9g},{i0:.9g},{e1:.9g}\n")
            say(f"\n  {len(samples)} samples written to {os.path.basename(CSV_PATH)}")
        except Exception as exc:  # noqa: BLE001
            say(f"\n  could not write the CSV: {exc}")

        if len(samples) < 30:
            say("  too few samples to analyse.")
            return 1

        sweep = [s for s in samples if s[0] >= SWEEP_STARTS_S]
        early = [s for s in samples if s[0] < SWEEP_STARTS_S]

        rule("0 - THE STARTUP POINTS (before the staircase runs)")
        say(f"  {len(early)} samples before t={SWEEP_STARTS_S:.2f}s, "
            f"{len(sweep)} during the sweep")
        if early:
            say(f"  E over those: {min(s[1] for s in early):+.4f} to "
                f"{max(s[1] for s in early):+.4f} V")
            say(f"  I over those: {min(s[2] for s in early):.4e} to "
                f"{max(s[2] for s in early):.4e} A")
            say("  >> The latch holds a pre-sweep value while the plot has already")
            say("     started. This is the point near the origin, and it is a")
            say("     DIFFERENT bug from a mid-sweep wedge: the cure is not plotting")
            say("     until the staircase is actually running.")

        # --- fit on the sweep only ---------------------------------------------
        es = [s[1] for s in sweep]
        iss = [s[2] for s in sweep]
        n = len(es)
        sx, sy = sum(es), sum(iss)
        sxx = sum(e * e for e in es)
        sxy = sum(e * i for e, i in zip(es, iss))
        den = n * sxx - sx * sx
        if abs(den) < 1e-30:
            say("  potential never varied - cannot fit.")
            return 1
        m = (n * sxy - sx * sy) / den
        b = (sy - m * sx) / n
        R = 1.0 / m
        step_equiv = STEP_V / R        # the current one whole step of staleness makes

        rule("1 - IS Ei.Potential QUANTIZED TO THE STAIRCASE?")
        # If it is the commanded level it lands on multiples of STEP_V, and identical
        # consecutive reads are EXPECTED — which is why "only I changed" proves nothing.
        offs = sorted(abs(((e / STEP_V) - round(e / STEP_V)) * STEP_V) for e in es)
        say(f"  distance from the nearest {STEP_V * 1000:.0f} mV multiple:")
        say(f"    median {offs[len(offs) // 2] * 1000:.4f} mV   "
            f"p95 {offs[int(0.95 * (len(offs) - 1))] * 1000:.4f} mV   "
            f"max {offs[-1] * 1000:.4f} mV")
        say(f"  distinct E values during the sweep: {len(set(es))} of {n} samples")
        quantized = offs[int(0.95 * (len(offs) - 1))] < 0.1 * STEP_V
        say("")
        if quantized:
            say("  >> QUANTIZED. Ei.Potential is the commanded staircase level, so two")
            say("     consecutive reads being bit-identical is normal and says nothing")
            say("     about how many latches there are. Section 2 is the real test.")
        else:
            say("  >> NOT quantized - it is a measured potential with its own noise, so")
            say("     bit-identical consecutive reads are themselves informative.")

        rule("2 - HOW STALE IS THE POTENTIAL IN EACH PAIR?")
        say(f"  fit over the sweep: R = {R:.1f} ohm, b = {b:.3e} A")
        say(f"  one {STEP_V * 1000:.0f} mV step of staleness = {step_equiv * 1e6:.3f} uA "
            f"of displacement")
        stale = []            # (t, E, I, staleness_V, steps)
        for t, e, i, _ in sweep:
            e_implied = (i - b) * R
            d = e_implied - e
            stale.append((t, e, i, d, d / STEP_V))
        mags = sorted(abs(s[4]) for s in stale)
        say(f"  |staleness| in steps: median {mags[len(mags) // 2]:.3f}   "
            f"p95 {mags[int(0.95 * (len(mags) - 1))]:.3f}   max {mags[-1]:.3f}")
        wedges = [s for s in stale if abs(s[4]) >= WEDGE_STEPS]
        say(f"  pairs displaced by >= {WEDGE_STEPS} step: {len(wedges)} of {n} "
            f"({100.0 * len(wedges) / n:.1f}%)")
        say("")
        for t, e, i, d, k in wedges[:15]:
            say(f"    t={t:6.2f}s  E={e:+.4f} V  I={i:+.4e} A  "
                f"stale by {d * 1000:+7.2f} mV = {k:+6.2f} steps")
        say("")
        if wedges:
            near_int = sum(1 for s in wedges if abs(s[4] - round(s[4])) < 0.25)
            say(f"  of those, {near_int} sit within a quarter-step of a WHOLE number of")
            say("  steps — the signature of a potential that is an exact number of")
            say("  staircase levels behind its current.")
            say("  >> These are the wedges. The displacement is horizontal and its size")
            say("     is the staleness, so the live plot is pairing a potential from")
            say("     one instant with a current from another.")
        else:
            say("  >> No pair is displaced by as much as half a step. The mid-sweep")
            say("     wedge did NOT reproduce under this script's timing, even though")
            say("     it reproduces in the GUI - so something about the GUI's timing")
            say("     (the spectrometer sharing the loop) is part of the cause.")

        rule("3 - DID THE GUARD SEE ANYTHING?")
        caught = sum(1 for s in samples if s[1] != s[3])
        say(f"  pairs where the potential moved between the two reads: {caught}")
        say("  (this is what _read_ei_pair drops; the GUI run logged 0 drops)")
        if wedges and not caught:
            say("")
            say("  >> The guard saw NOTHING while pairs were displaced off the line.")
            say("     It does not address this glitch.")
        return 0
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_ei_pair_source_report.txt"))
    sys.exit(code)
