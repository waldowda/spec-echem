"""
probe_autolab_pitt.py -- can the Autolab change its Ei setpoint with the cell ON?

The one hardware question a PITT staircase rests on, answered by driving the REAL
AutolabPotentiostat PITT methods (pitt_prepare / fire / pitt_sample /
pitt_set_potential / pitt_end) -- so this script's verdict is the driver's verdict,
not a copy's. No spectrometer: fire() still pulses P1.A, and nothing catches it.

    >> A DUMMY CELL ONLY, never a film: the UDC4 on its Randles position
       (200 Ohm + 3.01 kOhm || 1 uF -> 3210 Ohm DC), or a plain resistor. <<

A small staircase, 0 -> +0.15 V -> 0 in 50 mV steps, 3 s a step, sampled every
0.1 s. The cell is switched on ONCE and off ONCE; nothing in between touches it.
Per step it checks:

  * the settled potential is within 2 mV of the setpoint
  * the settled current is within 3% of V / R            (R = --ohms, default 3210)
  * how long the potential took to reach the new setpoint after the change
  * how many reads straddled a change (returned NaN), and any overload
  * the cell still reads ON after each setpoint change

Usage, from the repo folder on the Autolab PC:

    python examples/probe_autolab_pitt.py --energize              # UDC4 Randles
    python examples/probe_autolab_pitt.py --energize --ohms 2000  # UDC4 calibration side

Writes examples/probe_autolab_pitt_report.txt and probe_autolab_pitt_samples.csv.
The current range is this rig's autolab_current_range (config/bench.ini); at 0.15 V
the Randles DC current is ~47 uA, comfortably inside CR10_1mA.
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

import autolab_common as ac                                        # noqa: E402
from spec_echem import potentiostat                                # noqa: E402
from spec_echem.bench import load_bench_defaults                   # noqa: E402
from spec_echem.settings import DEFAULT_SETTINGS                   # noqa: E402

STEPS_V = (0.0, 0.05, 0.10, 0.15, 0.10, 0.05, 0.0)
HOLD_S = 3.0
TICK_S = 0.1
SETTLED_TAIL_S = 1.0       # the last second of a step is "settled"
E_TOL_V = 0.002            # Ei.Setpoint is a DAC: ~55 uV steps, so 2 mV is generous
I_TOL = 0.03


def cell_reads_on(inst):
    """What the instrument says, independent of what the driver believes."""
    return ac.safe(lambda: bool(inst.Ei.Cell))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="actually switch the cell on (DUMMY CELL ONLY)")
    ap.add_argument("--ohms", type=float, default=3210.0,
                    help="DC resistance of the dummy (UDC4 Randles = 3210)")
    args = ap.parse_args()

    ac.rule("CAN THE AUTOLAB STEP ITS Ei SETPOINT WITH THE CELL ON?  (PITT probe)")
    if not args.energize:
        ac.say("Refusing to energize: re-run with --energize, with a DUMMY cell in.")
        return 1
    ac.say(f"*** The cell WILL be switched on and stepped through {STEPS_V} V. ***")
    ac.say(f"*** Dummy only: expecting {args.ohms:.0f} Ohm DC. Never a film. ***")

    settings = dict(DEFAULT_SETTINGS)
    bench, warnings = load_bench_defaults()
    settings.update(bench)
    for w in warnings:
        ac.say(f"  bench.ini: {w}")
    ac.say(f"  current range: {settings.get('autolab_current_range') or '(as the instrument has it)'}")
    ac.say(f"  DIO mask:      {settings.get('autolab_dio_mask')}")

    p = potentiostat.AutolabPotentiostat(settings)
    rows, changes, cell_after = [], [], []
    try:
        p.open()
        p.pitt_prepare(STEPS_V[0])
        p.fire()                                        # cell ON, the only time
        cell_after.append(("after fire", cell_reads_on(p._inst)))
        for k, v in enumerate(STEPS_V):
            t_change = time.perf_counter()
            if k:
                p.pitt_set_potential(v)                 # cell LEFT on
                cell_after.append((f"after step {k} -> {v:+.3f} V",
                                   cell_reads_on(p._inst)))
            changes.append(t_change)
            next_tick = t_change
            while time.perf_counter() - t_change < HOLD_S:
                t, e, i = p.pitt_sample()
                rows.append((k, v, time.perf_counter() - t_change, t, e, i))
                next_tick += TICK_S
                time.sleep(max(0.0, next_tick - time.perf_counter()))
    except Exception as exc:  # noqa: BLE001 -- say it, then the finally makes it safe
        ac.say(f"\n*** STOPPED: {type(exc).__name__}: {exc}")
    finally:
        try:
            p.pitt_end()                                # cell OFF, the only time
            ac.say("Cell switched OFF.")
        finally:
            p.close()

    with open(os.path.join(HERE, "probe_autolab_pitt_samples.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["step", "setpoint_V", "t_in_step_s", "t_since_cell_on_s",
                    "potential_V", "current_A"])
        w.writerows(rows)

    ac.rule("PER STEP")
    ac.say("  step  set (V)   E settled (V)   dE (mV)   I settled (A)   I vs V/R   "
           "reached in   NaN reads")
    passes = []
    for k, v in enumerate(STEPS_V):
        mine = [r for r in rows if r[0] == k]
        if not mine:
            ac.say(f"  {k:>4}  {v:+.3f}   (no samples -- the run stopped before this step)")
            passes.append(False)
            continue
        good = [r for r in mine if not math.isnan(r[4])]
        nan_reads = len(mine) - len(good)
        tail = [r for r in good if r[2] >= HOLD_S - SETTLED_TAIL_S]
        if not tail:
            ac.say(f"  {k:>4}  {v:+.3f}   (no settled samples)")
            passes.append(False)
            continue
        e_set = sorted(r[4] for r in tail)[len(tail) // 2]
        i_set = sorted(r[5] for r in tail)[len(tail) // 2]
        reached = next((r[2] for r in good if abs(r[4] - v) <= E_TOL_V), None)
        expect_i = v / args.ohms
        if abs(expect_i) > 1e-7:
            i_err = abs(i_set - expect_i) / abs(expect_i)
            i_ok = i_err <= I_TOL
            i_txt = f"{i_err * 100:5.1f}%"
        else:                                  # 0 V: expect ~0; judge it loosely
            i_ok = abs(i_set) < 2e-6
            i_txt = "  ~0 "
        e_ok = abs(e_set - v) <= E_TOL_V
        ok = e_ok and i_ok and reached is not None
        passes.append(ok)
        ac.say(f"  {k:>4}  {v:+.3f}   {e_set:+.6f}      {(e_set - v) * 1000:+6.2f}    "
               f"{i_set:+.4e}     {i_txt}    "
               f"{(f'{reached * 1000:6.0f} ms') if reached is not None else '   never '}   "
               f"{nan_reads:>3}   {'OK' if ok else 'FAIL'}")

    ac.rule("THE CELL, AS THE INSTRUMENT REPORTED IT")
    for label, on in cell_after:
        ac.say(f"  {label:32} {'ON' if on else ('OFF' if on is not None else 'unreadable')}")
    cell_ok = all(on is True for _l, on in cell_after)

    ac.rule("VERDICT")
    if all(passes) and cell_ok and len(passes) == len(STEPS_V):
        ac.say("  PASS: the setpoint steps cleanly with the cell held on.")
        ac.say("  The PITT can run on this Autolab. Next: a short PITT from the GUI on")
        ac.say("  the same dummy (Python/Autolab mode, a few steps, short holds).")
    else:
        ac.say("  NOT YET: see the FAIL rows and the cell lines above.")
        if not cell_ok:
            ac.say("  The cell did not read ON after every change -- the staircase would")
            ac.say("  sit at open circuit between steps. Do NOT run a PITT on a film.")
        ac.say("  Send the report and the CSV back before going further.")
    ac.write_transcript(os.path.join(HERE, "probe_autolab_pitt_report.txt"))
    return 0 if all(passes) and cell_ok else 2


if __name__ == "__main__":
    sys.exit(main())
