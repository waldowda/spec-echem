"""
bench_ei_sampling.py — can Python sample the Autolab fast enough to replace FHLevel?

This is open item 2d of docs/autolab-driver-finishing.md, and after 2026-09-09 it is
the question that decides the CA architecture. What that day measured:

    cell ON -> the recorder's first sample   ~0.93 s   (CalcTime[0], 20260909_test6/7)
    of which one command costs               ~0.23 s   (CV's FHPreCurrentRangingCV)
    UseFastOptions = True changed it by      ~0        (20260909_test8)

So the ~0.93 s is the procedure walking FHGetSetValues -> FHSetSetpointPotential ->
FHSwitchCell before it ever reaches FHLevel, and no parameter shortens it. Dean's
requirement is cell-on to data inside ONE delta_time (100 ms), which the .nox route
cannot meet. The alternative is the shape the Gamry driver already has:

    set_cell(True); set_digital_out(...); curve.run(True)     # three statements

For the Autolab that means Ei directly: Python applies the setpoint, pulses the
trigger, and reads Ei.Current itself. Before writing that, three things have to be
measured rather than assumed.

    A  WHAT DOES ONE POINT COST?  Not one read: probe_ei_live.py showed Ei.Current is
       a LATCH, so the bare properties are cheap precisely because they do not talk to
       the instrument. The real cost is Sampler.Sample() + two latch reads, which is
       what pump() does per spectrum. That number bounds any grid faster than 100 ms,
       and is the first suspect for 20260909_test12's cadence outliers.

    B  HOW FAST CAN PYTHON GET TO THE FIRST SAMPLE?  The number that would replace
       0.93 s. Measured from cell-on, the same origin the driver's timing line uses,
       so it is directly comparable to CalcTime[0].

    C  IS IT NOISIER THAN THE RECORDER, AND DOES THE STARTUP ARTIFACT FOLLOW?
       Every chrono segment on 2026-09-09 had its first FIVE samples ~110 nA low,
       then went bit-flat. That tracks the RECORDER starting, not the cell switching
       on — it survived cell-on moving five seconds closer. If the same offset shows
       up here, it is the current amplifier and Ei will not fix it; if it does not,
       it belongs to FHLevel's arming and Ei removes it. Either answer is worth
       having before the rewrite, not after.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

Phase A needs no cell and is the only part still unmeasured. B and C were answered by
20260909_test12 from a real run (first Ei sample 85-125 ms; the ~110 nA artifact did
not follow), so they are kept only as a cross-check.

Usage:
    python bench_ei_sampling.py          # phase A, nothing energized
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# --- what to run -----------------------------------------------------------
ENERGIZE_CELL = False     # False = phase A only (read cost), nothing energized
HOLD_V = 0.100            # the same potential the doping steps used on 2026-09-09
HOLD_S = 5.0              # short: this is a timing probe, not an experiment
INTERVAL_S = 0.100        # target grid, matching chrono_delta_time
READ_TRIALS = 500         # phase A sample size


def _read_cost(inst, label, getter):
    """Time READ_TRIALS reads of one Ei scalar. perf_counter throughout: monotonic,
    and 58 ns/call on this box, so the clock is not what we are measuring."""
    try:
        getter()                      # warm up; the first touch can be slower
    except Exception as exc:          # noqa: BLE001
        say(f"    {label}: unreadable ({exc})")
        return None
    per = []
    for _ in range(READ_TRIALS):
        t0 = time.perf_counter()
        getter()
        per.append(time.perf_counter() - t0)
    per.sort()
    med = per[len(per) // 2]
    say(f"    {label:22} median {med * 1000:7.3f} ms   "
        f"min {per[0] * 1000:6.3f}   p95 {per[int(len(per) * 0.95)] * 1000:7.3f}   "
        f"max {per[-1] * 1000:7.3f}")
    return med


def phase_a(inst):
    """A: what does ONE POINT cost, the way pump() actually takes it?

    Rewritten 2026-09-09 after probe_ei_live.py showed Ei.Current is a LATCH. Timing
    the bare properties measures nothing useful — they are cheap precisely because
    they do not talk to the instrument. The real per-point cost is:

        Ei.Sampler.Sample()      one round trip, refreshes every signal
        + Ei.Potential           latch read
        + Ei.Current             latch read

    which is exactly the sequence in AutolabPotentiostat.pump(). That is the number
    that bounds a faster grid, and the one that would explain 20260909_test12's
    cadence outliers (a 249.8 ms interval against a 100 ms target) if Sample() turns
    out to be expensive or occasionally slow.

    No cell: sampling is not energizing.
    """
    rule("A — what one data point costs (no cell)")
    say(f"  {READ_TRIALS} trials. pump() does Sample() + two latch reads per spectrum,")
    say("  and acquisition budgets SPECTRUM_OVERHEAD_S = 30 ms for everything that is")
    say("  not exposure. If a point costs more than that, pump() is now the overhead.")
    say("")
    ei = inst.Ei

    def one_point():
        ei.Sampler.Sample()
        return float(ei.Potential), float(ei.Current)

    costs = {}
    for label, fn in (("Sampler.Sample() alone", lambda: ei.Sampler.Sample()),
                      ("Ei.Potential (latch)", lambda: float(ei.Potential)),
                      ("Ei.Current (latch)", lambda: float(ei.Current)),
                      ("FULL POINT (what pump does)", one_point)):
        costs[label] = _read_cost(inst, label, fn)

    point = costs.get("FULL POINT (what pump does)")
    if point is None:
        return costs
    say("")
    say(f"  One point = {point * 1000:.2f} ms median.")
    say(f"  Against the 30 ms overhead budget: {point / 0.030 * 100:.0f}% of it.")
    say(f"  Against a 100 ms grid: {point / 0.100 * 100:.1f}% of the slot.")
    say("")
    if point < 0.010:
        say("  -> Cheap. It does not explain test12's cadence outliers; look at the")
        say("     spectrometer or host scheduling instead. A 20-50 ms grid is plausible.")
    elif point < 0.030:
        say("  -> Fits inside the existing overhead budget, but it is a real share of")
        say("     it. A grid below ~50 ms would need rechecking.")
    else:
        say("  -> pump() is now a significant cost and SPECTRUM_OVERHEAD_S understates")
        say("     the per-spectrum overhead. THIS is the cadence-outlier suspect.")
    return costs


def phase_bc(inst):
    """B and C: cell on, sample from Python, and look at the first samples."""
    rule("B/C — Python-driven hold: time to first sample, and the startup artifact")
    from EcoChemie.Autolab.Sdk import EI

    ei = inst.Ei
    # Configure BEFORE the cell goes on, the way the procedure's FHGetSetValues does
    # — this is the work whose ~0.7 s we are trying to avoid paying inside a run.
    t_cfg = time.perf_counter()
    safe(lambda: setattr(ei, "Mode", EI.EIMode.Potentiostatic))
    safe(lambda: setattr(ei, "CurrentRange", EI.EICurrentRange.CR10_1mA))
    safe(lambda: setattr(ei, "Setpoint", float(HOLD_V)))
    cfg_cost = time.perf_counter() - t_cfg
    say(f"  Ei configured (Mode, CurrentRange, Setpoint) in {cfg_cost * 1000:.1f} ms.")

    samples = []          # (t_from_cell_on, potential, current)
    t_cell_on = None
    try:
        ac.switch_cell(inst, True)
        t_cell_on = time.perf_counter()
        deadline = t_cell_on
        while True:
            now = time.perf_counter()
            if now - t_cell_on >= HOLD_S:
                break
            if now < deadline:
                time.sleep(min(0.5e-3, deadline - now))
                continue
            E = safe(lambda: float(ei.Potential))
            I = safe(lambda: float(ei.Current))
            samples.append((time.perf_counter() - t_cell_on, E, I))
            deadline += INTERVAL_S
    finally:
        ac.cell_off_quietly(inst)

    if not samples:
        say("  No samples taken.")
        return

    first_t = samples[0][0]
    say("")
    say(f"  B: cell ON -> FIRST SAMPLE = {first_t * 1000:.1f} ms   "
        f"(n={len(samples)}, target grid {INTERVAL_S * 1000:.0f} ms)")
    say(f"     the .nox route's CalcTime[0] for the same thing: ~930 ms")
    if first_t < INTERVAL_S:
        say("     -> INSIDE one delta_time. This is the requirement, met.")
    else:
        say("     -> still longer than one delta_time; Ei alone does not fix it.")

    dts = [samples[i + 1][0] - samples[i][0] for i in range(len(samples) - 1)]
    if dts:
        say(f"     grid: median {statistics.median(dts) * 1000:.1f} ms, "
            f"min {min(dts) * 1000:.1f}, max {max(dts) * 1000:.1f}")

    currents = [s[2] for s in samples if s[2] is not None]
    if len(currents) > 12:
        head = currents[:5]
        tail = currents[10:]
        settled = statistics.median(tail)
        offset = (sum(head) / len(head) - settled) * 1e9
        say("")
        say(f"  C: first 5 samples vs settled: {offset:+.1f} nA")
        say(f"     the FHLevel route showed -110 nA over exactly 5 samples")
        if abs(offset) < 20:
            say("     -> NOT present on the Ei path. It belongs to FHLevel arming,")
            say("        and a Python-driven CA removes it.")
        else:
            say("     -> ALSO present here. It is the current amplifier settling, so")
            say("        Ei will not fix it and a dead period is needed either way.")
        say(f"     settled {settled:.5e} A   sd {statistics.pstdev(tail):.2e}")
        say(f"     R = {HOLD_V / settled / 1000:.2f} kOhm (expect ~9.9 for the dummy)")
        say("     first 8 samples (t ms, I nA):")
        for t, _E, I in samples[:8]:
            say(f"       {t * 1000:8.1f}   {I * 1e9:+12.2f}")


def main():
    rule("CAN PYTHON SAMPLE THE AUTOLAB FAST ENOUGH TO REPLACE FHLevel?")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    if ENERGIZE_CELL:
        say("*** The cell WILL be switched on. 10 kOhm dummy only — never a sample. ***")
    else:
        say("Phase A only (read cost). Nothing is energized.")

    inst = ac.connect()
    if inst is None:
        return 1
    try:
        phase_a(inst)
        if ENERGIZE_CELL:
            phase_bc(inst)
        else:
            rule("B/C — skipped (and largely superseded)")
            say("  20260909_test12 already answered both from a real run: cell ON to")
            say("  the first Ei sample was 85-125 ms, and the ~110 nA five-sample")
            say("  artifact did not follow (+7.9/+3.7/-1.8 nA, inside the noise).")
            say("  Phase A above is the part that is still unmeasured.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_ei_sampling_report.txt"))
    sys.exit(code)
