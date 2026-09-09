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

    A  WHAT DOES ONE READ COST?  Ei.Current is a scalar property, one USB round trip.
       If it costs 10 ms, a 100 ms grid is comfortable and a 10 ms grid is impossible.
       The ~10 ms figure in the docs was INFERRED from an in-run/free-run subtraction
       and has never been isolated. This times it directly.

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

Phase A needs no cell. Phases B and C do — set ENERGIZE_CELL = True with the dummy in.

Usage:
    python bench_ei_sampling.py
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
    """A: what does one scalar read cost? No cell — reading is not energizing."""
    rule("A — the cost of one Ei read (no cell)")
    say(f"  {READ_TRIALS} reads each, cell OFF. This sets the sampling floor: a")
    say("  Python-driven CA cannot sample faster than one read, and it needs both")
    say("  potential AND current per point.")
    say("")
    ei = inst.Ei
    costs = {}
    for label, getter in (("Ei.Current", lambda: float(ei.Current)),
                          ("Ei.Potential", lambda: float(ei.Potential)),
                          ("Ei.Cell", lambda: bool(ei.Cell)),
                          ("Ei.CurrentOverload", lambda: bool(ei.CurrentOverload))):
        costs[label] = _read_cost(inst, label, getter)

    pair = [costs.get("Ei.Current"), costs.get("Ei.Potential")]
    if all(c is not None for c in pair):
        point = sum(pair)
        say("")
        say(f"  One DATA POINT (current + potential) = {point * 1000:.2f} ms.")
        say(f"  Fastest achievable grid ~= {point * 1000:.0f} ms; a 100 ms grid uses "
            f"{point / 0.100 * 100:.0f}% of its slot.")
        if point < 0.010:
            say("  -> Comfortable. A 100 ms grid is easy and 10 ms is plausible.")
        elif point < 0.050:
            say("  -> A 100 ms grid works. Anything near 50 ms will be tight.")
        else:
            say("  -> A 100 ms grid is at risk. Ei sampling may not beat FHLevel.")
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
            rule("B/C — skipped")
            say("  Set ENERGIZE_CELL = True with the dummy resistor in to answer")
            say("  'how fast is the first sample' and 'does the startup offset follow'.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_ei_sampling_report.txt"))
    sys.exit(code)
