"""
probe_gamry_pitt.py -- can the Gamry step its potential with the cell ON?

The Gamry twin of probe_autolab_pitt.py. It drives the REAL ToolkitPotentiostat PITT
methods (pitt_prepare / fire / pitt_sample / pitt_set_potential / pitt_end), which
use toolkitpy's direct set_voltage / measure_v / measure_i -- no curve -- so this
script's verdict is the driver's verdict. No spectrometer: fire() still raises
DIGOUT0, and nothing catches it.

    >> A DUMMY CELL ONLY, never a film: the UDC4 on its Randles position
       (200 Ohm + 3.01 kOhm || 1 uF -> 3210 Ohm DC), or a plain resistor. <<

A small staircase, 0 -> +0.15 V -> 0 in 50 mV steps, 3 s a step, sampled every
0.1 s. The cell is switched on ONCE and off ONCE. Per step it checks:

  * each STEP is within 2 mV of the step asked (a constant offset is
    reported, not failed)
  * the current, as a DC resistance MEASURED from the slope (no resistance assumed)
  * how long the potential took to reach the new setpoint
  * the cell, as the instrument reports it (Pstat.cell()), after every change

and two things only this instrument can answer:

  * how long one measure_v + measure_i pair takes -- the PITT samples on a 0.1 s
    tick, and this says whether that holds
  * the apparent size of ONE current count on this range, from the spacing of the
    readings (the Autolab's CR10_1mA turned out to read in 3.05 nA counts)

Run in the 32-bit SpecEchem32 env, from the repo folder on the Gamry PC:

    python examples/probe_gamry_pitt.py --energize                  # bench.ini range
    python examples/probe_gamry_pitt.py --energize --range 6e-5     # a finer range, A

Writes examples/probe_gamry_pitt_report.txt and probe_gamry_pitt_samples.csv.
"""
import argparse
import csv
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import autolab_common as ac        # noqa: E402  (only its say/rule/transcript helpers)
from spec_echem import potentiostat                                # noqa: E402
from spec_echem.bench import load_bench_defaults                   # noqa: E402
from spec_echem.settings import DEFAULT_SETTINGS                   # noqa: E402

STEPS_V = (0.0, 0.05, 0.10, 0.15, 0.10, 0.05, 0.0)
HOLD_S = 3.0
TICK_S = 0.1
SETTLED_TAIL_S = 1.0
E_TOL_V = 0.002


def cell_reads_on(p):
    pstat = p._pitt_pstat
    return ac.safe(lambda: bool(pstat.cell()))


def median(values):
    v = sorted(values)
    return v[len(v) // 2] if v else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="actually switch the cell on (DUMMY CELL ONLY)")
    ap.add_argument("--range", type=float, default=None,
                    help="Gamry current range for this probe only, in amperes "
                         "(full scale, e.g. 6e-3, 6e-4, 6e-5); overrides bench.ini")
    args = ap.parse_args()

    ac.rule("CAN THE GAMRY STEP ITS POTENTIAL WITH THE CELL ON?  (PITT probe)")
    if not args.energize:
        ac.say("Refusing to energize: re-run with --energize, with a DUMMY cell in.")
        return 1
    if not potentiostat.TOOLKITPY_AVAILABLE:
        ac.say("toolkitpy is not importable here. Use the 32-bit SpecEchem32 env.")
        return 1
    ac.say("*** The cell WILL be switched on and stepped through %s V. ***" % (STEPS_V,))
    ac.say("*** Dummy only. Never a film. ***")

    settings = dict(DEFAULT_SETTINGS)
    bench, warnings = load_bench_defaults()
    settings.update(bench)
    for w in warnings:
        ac.say("  bench.ini: %s" % w)
    if args.range:
        settings["gamry_current_range"] = args.range
    ac.say("  current range: %s A" % settings.get("gamry_current_range"))

    p = potentiostat.ToolkitPotentiostat(settings)
    rows, cell_after, costs, timing = [], [], [], {}
    try:
        p.pitt_prepare(STEPS_V[0])
        p.fire()                                        # cell ON, the only time
        cell_after.append(("after fire", cell_reads_on(p)))
        # Where the time goes. The first run measured 355 ms for one measure_v +
        # measure_i pair -- 3.5x the PITT's 0.1 s tick -- so time each call alone, and
        # ask the instrument what it thinks a measurement takes.
        pstat = p._pitt_pstat
        for name in ("measure_v", "measure_i"):
            fn = getattr(pstat, name)
            ts = []
            for _ in range(5):
                t0 = time.perf_counter()
                fn()
                ts.append(time.perf_counter() - t0)
            timing[name] = ts
        timing["measure_time()"] = ac.safe(lambda: float(pstat.measure_time()))
        for k, v in enumerate(STEPS_V):
            t_change = time.perf_counter()
            if k:
                p.pitt_set_potential(v)                 # cell LEFT on
                cell_after.append(("after step %d -> %+.3f V" % (k, v),
                                   cell_reads_on(p)))
            next_tick = t_change
            while time.perf_counter() - t_change < HOLD_S:
                t0 = time.perf_counter()
                t, e, i = p.pitt_sample()
                costs.append(time.perf_counter() - t0)
                rows.append((k, v, time.perf_counter() - t_change, t, e, i))
                next_tick += TICK_S
                time.sleep(max(0.0, next_tick - time.perf_counter()))
    except Exception as exc:  # noqa: BLE001 -- say it, then the finally makes it safe
        ac.say("\n*** STOPPED: %s: %s" % (type(exc).__name__, exc))
    finally:
        p.pitt_end()                                    # cell OFF, session closed
        ac.say("Cell switched OFF, session closed.")

    with open(os.path.join(HERE, "probe_gamry_pitt_samples.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["step", "setpoint_V", "t_in_step_s", "t_since_cell_on_s",
                    "potential_V", "current_A"])
        w.writerows(rows)

    ac.rule("PER STEP")
    ac.say("  step  set (V)   E settled (V)   dE (mV)   I settled (A)   step size")
    passes, pts, settled = [], [], []
    for k, v in enumerate(STEPS_V):
        mine = [r for r in rows if r[0] == k and not math.isnan(r[4])]
        tail = [r for r in mine if r[2] >= HOLD_S - SETTLED_TAIL_S]
        if not tail:
            ac.say("  %4d  %+.3f   (no settled samples)" % (k, v))
            passes.append(False)
            continue
        e_set = median([r[4] for r in tail])
        i_set = median([r[5] for r in tail])
        pts.append((v, i_set))
        settled.append((v, e_set))
        # The STEP is what a PITT needs right; a constant offset in the readback is
        # reported below, not failed. First run on the Reference 600 (2026-10-05):
        # every reading sat +2.5..+2.7 mV high, including at 0 V, while the steps
        # themselves were exact to ~0.1 mV -- an absolute 2 mV test failed them all.
        ok = True
        dstep = None
        if len(settled) > 1:
            dstep = (e_set - settled[-2][1]) - (v - settled[-2][0])
            ok = abs(dstep) <= E_TOL_V
        passes.append(ok)
        ac.say("  %4d  %+.3f   %+.6f      %+6.2f    %+.4e     %s   %s"
               % (k, v, e_set, (e_set - v) * 1000, i_set,
                  ("step off by %+.2f mV" % (dstep * 1000)) if dstep is not None
                  else "(first step)       ", "OK" if ok else "FAIL"))

    if settled:
        offsets = [(e - v) * 1000 for v, e in settled]
        ac.say("")
        ac.say("  readback offset: %+.2f mV on average (range %+.2f to %+.2f) -- a"
               % (sum(offsets) / len(offsets), min(offsets), max(offsets)))
        ac.say("  CONSTANT offset is the voltage readout or the applied potential; it")
        ac.say("  does not affect the steps a PITT takes.")

    ac.rule("THE DC PATH, MEASURED")
    if len(set(v for v, _ in pts)) >= 2:
        n = len(pts)
        mv = sum(v for v, _ in pts) / n
        mi = sum(i for _, i in pts) / n
        slope = (sum((v - mv) * (i - mi) for v, i in pts)
                 / sum((v - mv) ** 2 for v, _ in pts))
        offset = mi - slope * mv
        resid = math.sqrt(sum((i - (offset + slope * v)) ** 2 for v, i in pts) / n)
        change = abs(slope) * (max(v for v, _ in pts) - min(v for v, _ in pts))
        ac.say("  slope %+.4e A/V, intercept %+.4e A, fit scatter %.2e A"
               % (slope, offset, resid))
        if slope and change > max(10 * resid, 5e-9):
            ac.say("  -> DC resistance %s Ohm" % format(1 / slope, ",.0f"))
        else:
            ac.say("  -> no DC current this range resolves; try a finer --range")

    ac.rule("WHAT ONE SAMPLE COSTS, AND ONE COUNT")
    for name in ("measure_v", "measure_i"):
        if timing.get(name):
            ac.say("  %s alone: median %.1f ms (5 calls)" % (name, median(timing[name]) * 1000))
    if "measure_time()" in timing:
        ac.say("  the instrument's measure_time(): %s" % (
            "%.4f s" % timing["measure_time()"] if timing["measure_time()"] is not None
            else "unavailable"))
    if costs:
        cs = sorted(costs)
        ac.say("  measure_v + measure_i: median %.1f ms, max %.1f ms over %d samples"
               % (median(cs) * 1000, cs[-1] * 1000, len(cs)))
        ac.say("  (the PITT ticks every 0.1 s; a pair much over ~50 ms crowds it)")
    # One count shows as reading-to-reading jitter WITHIN a step, so it is taken from
    # there -- the gaps between all distinct readings would mostly be the gaps between
    # step levels, which says nothing about the converter.
    jitter = []
    for k in range(len(STEPS_V)):
        tail = [r[5] for r in rows if r[0] == k and not math.isnan(r[5])
                and r[2] >= HOLD_S - SETTLED_TAIL_S]
        jitter += [abs(b - a) for a, b in zip(tail, tail[1:]) if b != a]
    if jitter:
        count = min(jitter)
        multiples = [j / count for j in jitter]
        whole = sum(1 for m in multiples if abs(m - round(m)) < 0.02) / len(multiples)
        ac.say("  smallest reading-to-reading change within a step: %.3e A" % count)
        ac.say("  %.0f%% of the changes are whole multiples of it%s"
               % (whole * 100, " -- that looks like one count of this range"
                  if whole > 0.9 else " -- not clearly quantised"))
    else:
        ac.say("  readings did not change within any step: no count estimate")

    ac.rule("THE CELL, AS THE INSTRUMENT REPORTED IT")
    for label, on in cell_after:
        ac.say("  %-32s %s" % (label, "ON" if on else ("OFF" if on is not None
                                                        else "unreadable")))
    cell_ok = bool(cell_after) and all(on is True for _l, on in cell_after)

    ac.rule("VERDICT")
    if all(passes) and cell_ok and len(passes) == len(STEPS_V):
        ac.say("  PASS: the potential steps cleanly with the cell held on.")
        ac.say("  Next: a short PITT from the GUI on the same dummy (Python mode).")
    else:
        ac.say("  NOT YET: see the FAIL rows and the cell lines above.")
        if not cell_ok:
            ac.say("  The cell did not read ON after every change. Do NOT run a PITT on")
            ac.say("  a film until that is understood.")
        ac.say("  Send the report and the CSV back.")
    ac.write_transcript(os.path.join(HERE, "probe_gamry_pitt_report.txt"))
    return 0 if all(passes) and cell_ok else 2


if __name__ == "__main__":
    sys.exit(main())
