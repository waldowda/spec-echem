"""
probe_gamry_pitt.py -- the Gamry PITT end to end, on a dummy, before the GUI.

Runs the REAL PITT loop (spec_echem.acquisition.acquire_pitt) on the REAL Gamry
driver (ToolkitPotentiostat: one curve per step, the cell held on, our step-end rule
fed live from each curve's points) -- the exact path a GUI PITT takes, with a stand-
in spectrometer (FakeSpectrometer) so no Avantes is needed. DIGOUT0 still goes high.

How the design was arrived at, all on the Reference 600 (2026-10-05):
  * measure_v / measure_i take ~176 ms each -- too slow to sample a step with
  * a curve samples at exactly 0.100 s on the instrument's own clock
  * neither built-in staircase can end a step early, so: one curve per step
  * between curves the cell stays ON at the previous potential; ~80 ms to run()

    >> A DUMMY CELL ONLY: the UDC4 EIS side (3210 Ohm DC) or a 2 kOhm. <<

A dummy's current does not decay at these rates, so every step should end at its
MAXIMUM hold -- that is the correct answer here. Per step it reports how it ended,
how long it held, the instrument's own point count and sample period, when the first
point landed, the gap to the next step, and the DC resistance from all of it.

    python examples/probe_gamry_pitt.py --energize

Writes examples/probe_gamry_pitt_report.txt.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import autolab_common as ac        # noqa: E402  (only its say/rule/transcript helpers)
from spec_echem import potentiostat                                # noqa: E402
from spec_echem.acquisition import acquire_pitt                    # noqa: E402
from spec_echem.bench import load_bench_defaults                   # noqa: E402
from spec_echem.fakes import FakeSpectrometer                      # noqa: E402
from spec_echem.pitt import pitt_plan                              # noqa: E402
from spec_echem.settings import DEFAULT_SETTINGS                   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="actually switch the cell on (DUMMY CELL ONLY)")
    ap.add_argument("--range", type=float, default=None,
                    help="Gamry current range for this probe only, in amperes")
    args = ap.parse_args()
    ac.rule("THE GAMRY PITT, END TO END, ON A DUMMY")
    if not args.energize:
        ac.say("Refusing to energize: re-run with --energize, with a DUMMY cell in.")
        return 1
    if not potentiostat.TOOLKITPY_AVAILABLE:
        ac.say("toolkitpy is not importable here. Use the 32-bit SpecEchem32 env.")
        return 1

    s = dict(DEFAULT_SETTINGS)
    s.update(load_bench_defaults()[0])
    if args.range:
        s["gamry_current_range"] = args.range
    s.update(pitt_start_v=0.0, pitt_stop_v=0.15, pitt_step_mv=50.0, pitt_return=True,
             pitt_cutoff_pct=1.0, pitt_min_hold_s=1.0, pitt_max_hold_s=3.0,
             pitt_fast_s=1.0, pitt_slow_interval_s=0.5, pitt_end_dedope=False,
             chrono_delta_time=0.1)
    plan = pitt_plan(s)
    ac.say("*** %d steps, 0 -> +0.15 V -> 0 in 50 mV, 1-3 s holds. Dummy only. ***"
           % len(plan))

    p = potentiostat.ToolkitPotentiostat(s)
    record, err = None, None
    try:
        record = acquire_pitt(FakeSpectrometer(), p, plan, s)
    except Exception as exc:  # noqa: BLE001 -- acquire_pitt's finally has the cell off
        err = "%s: %s" % (type(exc).__name__, exc)
    ac.say("Cell switched OFF, session closed (acquire_pitt's finally).")
    if err:
        ac.say("\n*** STOPPED: %s" % err)

    pts = p.pitt_hardware_echem()
    ac.rule("PER STEP  (the instrument's own points)")
    ac.say("  step  set (V)   ended      hold (s)  points  period (s)  first pt (s)"
           "   E mean (V)   I mean (A)")
    fit = []
    for st in (record.steps if record else []):
        k = st["index"]
        mine = [q for q in pts if q[0] == k]
        if not mine:
            ac.say("  %4d  %+.3f   %-9s  (no points)" % (k, st["potential_set"],
                                                        st["end_reason"]))
            continue
        t = [q[1] for q in mine]
        dts = sorted(b - a for a, b in zip(t, t[1:]))
        e = sum(q[3] for q in mine) / len(mine)
        i = sum(q[4] for q in mine) / len(mine)
        fit.append((st["potential_set"], i))
        ac.say("  %4d  %+.3f   %-9s  %8.2f  %6d  %10.4f  %12.3f   %+.6f   %+.4e"
               % (k, st["potential_set"], st["end_reason"], st["hold_s"], len(mine),
                  dts[len(dts) // 2] if dts else float("nan"), t[0], e, i))

    ac.rule("BETWEEN STEPS  (last point of one to the first of the next)")
    for k in range(1, len(record.steps) if record else 0):
        a = [q for q in pts if q[0] == k - 1]
        b = [q for q in pts if q[0] == k]
        if a and b:
            ac.say("  %d -> %d: %.0f ms" % (k - 1, k, (b[0][2] - a[-1][2]) * 1000))

    ac.rule("THE DC PATH")
    if len(set(v for v, _ in fit)) >= 2:
        n = len(fit)
        mv = sum(v for v, _ in fit) / n
        mi = sum(i for _, i in fit) / n
        slope = (sum((v - mv) * (i - mi) for v, i in fit)
                 / sum((v - mv) ** 2 for v, _ in fit))
        ac.say("  slope %+.4e A/V -> %s Ohm"
               % (slope, format(1 / slope, ",.0f") if slope else "inf"))

    ac.rule("VERDICT")
    ok = (record is not None and record.completed and err is None
          and len(record.steps) == len(plan) and len(pts) > 0)
    if ok:
        ac.say("  PASS: every step ran, on the instrument's clock, cell held on.")
        ac.say("  Expected on a dummy: every step 'max_hold' (its current does not")
        ac.say("  decay at 0.1 s). Next: a short PITT from the GUI, Python mode.")
    else:
        ac.say("  NOT YET -- send this report back.")
    ac.write_transcript(os.path.join(HERE, "probe_gamry_pitt_report.txt"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
