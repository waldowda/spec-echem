"""
probe_gamry_curve_per_step.py -- a PITT on the Gamry as ONE CURVE PER STEP?

What the staircase probe established on the Reference 600 (2026-10-05):
  * a hardware staircase runs at exactly 0.100 s on the internal clock (run A)
  * StopAt ends an array2 curve after N consecutive points -- the WHOLE curve,
    because a list waveform is one section (run B)
  * StopAt is ignored on an m_step staircase (run D)

So early step-ending at 10 Hz would need one curve per step, each ended by its own
StopAt, with the cell left ON in between. Three unknowns, and this answers them:

  1. Is the cell still ON between curves (Pstat.cell())?
  2. What potential is applied in the gap -- the last step's, 0 V, or nothing?
     (pass 1 reads it with one measure_v, which itself takes ~177 ms)
  3. How long is the gap from one curve's last point to the next one's first?
     (pass 2 does nothing in between, to see the minimum)

Each step's curve is the single-value array2 waveform already proven in run A, with
StopAt set ABOVE the dummy's DC current so it must fire after ~6 points.

    >> A DUMMY CELL ONLY: the UDC4 EIS side (3210 Ohm DC) or a 2 kOhm. <<

    python examples/probe_gamry_curve_per_step.py --energize

Writes examples/probe_gamry_curve_per_step_report.txt.
"""
import argparse
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

STEPS_V = [0.0, 0.05, 0.10, 0.15]
PERIOD = 0.1
MAX_POINTS = 30                    # the max hold, 3 s, if StopAt never fires
STOP_ABOVE_A = 1e-4                # above the dummy's DC current: StopAt MUST fire
STOP_DELAY_PTS = 5


def column(data, *names):
    import numpy as np
    for n in names:
        if data is not None and data.dtype.names and n in data.dtype.names:
            return [float(x) for x in np.atleast_1d(data[n])]
    return []


def make_curve(tkp, pstat, n):
    for name in ("ChronoCurve", "ChronoACurve"):
        cls = getattr(tkp, name, None)
        if cls is not None:
            return cls(pstat, n)
    raise RuntimeError("no chrono curve class in toolkitpy")


def one_step(tkp, pstat, v):
    """One curve at one potential, ended by StopAt. Returns a dict, never raises
    with the curve still running."""
    keep, curve = [], None
    out = {"v": v, "points": 0, "err": None}
    try:
        signal = pstat.signal_array2_new(0.0, 1, PERIOD, [float(v)], [MAX_POINTS],
                                         getattr(tkp, "BIAS_NONE", 0), tkp.PSTATMODE)
        keep.append(signal)                     # must outlive the run
        pstat.set_signal_array2(signal)
        pstat.init_signal()
        curve = make_curve(tkp, pstat, MAX_POINTS + 50)
        curve.set_stop_x_min(True, STOP_ABOVE_A)
        curve.set_stop_at_delay_x_min(STOP_DELAY_PTS)
        out["t_run"] = time.perf_counter()
        curve.run(True)
        first_seen = None
        while curve.running():
            if first_seen is None and ac.safe(lambda: int(curve.count()), 0) > 0:
                first_seen = time.perf_counter()
            time.sleep(0.01)
        out["t_done"] = time.perf_counter()
        out["t_first_seen"] = first_seen
        data = curve.acq_data()
        out["points"] = len(column(data, "time"))
        out["vsig"] = column(data, "vsig")
        out["vf"] = column(data, "vf")
        out["im"] = column(data, "im")
    except Exception as exc:  # noqa: BLE001
        out["err"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if curve is not None and ac.safe(lambda: curve.running(), False):
            ac.safe(curve.stop)
            deadline = time.perf_counter() + 5.0
            while ac.safe(lambda: curve.running(), False) \
                    and time.perf_counter() < deadline:
                time.sleep(0.05)
    return out


def staircase(tkp, pstat, read_gap):
    steps, gaps = [], []
    for k, v in enumerate(STEPS_V):
        if k:
            gap = {"cell": ac.safe(lambda: bool(pstat.cell()))}
            if read_gap:
                gap["v"] = ac.safe(lambda: float(pstat.measure_v()))
            gaps.append(gap)
        steps.append(one_step(tkp, pstat, v))
        if steps[-1]["err"]:
            break
    return steps, gaps


def show(title, steps, gaps, read_gap):
    ac.rule(title)
    ac.say("  step  set (V)  points  first vsig   E mean (V)   I mean (A)")
    for k, s in enumerate(steps):
        if s["err"]:
            ac.say("  %4d  %+.3f   FAILED: %s" % (k, s["v"], s["err"]))
            continue
        e = sum(s["vf"]) / len(s["vf"]) if s["vf"] else float("nan")
        i = sum(s["im"]) / len(s["im"]) if s["im"] else float("nan")
        ac.say("  %4d  %+.3f   %5d   %+.4f     %+.6f   %+.4e"
               % (k, s["v"], s["points"], s["vsig"][0] if s["vsig"] else float("nan"),
                  e, i))
    ac.say("")
    for k in range(1, len(steps)):
        a, b = steps[k - 1], steps[k]
        if a["err"] or b["err"] or "t_done" not in a or "t_run" not in b:
            continue
        g = gaps[k - 1] if k - 1 < len(gaps) else {}
        line = "  gap %d->%d: %.0f ms from curve end to next run()" % (
            k - 1, k, (b["t_run"] - a["t_done"]) * 1000)
        if b.get("t_first_seen"):
            line += ", %.0f ms to its first point" % ((b["t_first_seen"] - a["t_done"]) * 1000)
        line += ";  cell %s" % ("ON" if g.get("cell") else
                                ("OFF" if g.get("cell") is not None else "unreadable"))
        if read_gap and g.get("v") is not None:
            line += ";  applied in the gap %+.4f V (previous step %+.3f)" % (g["v"], a["v"])
        ac.say(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="actually switch the cell on (DUMMY CELL ONLY)")
    args = ap.parse_args()
    ac.rule("A PITT ON THE GAMRY AS ONE CURVE PER STEP?")
    if not args.energize:
        ac.say("Refusing to energize: re-run with --energize, with a DUMMY cell in.")
        return 1
    if not potentiostat.TOOLKITPY_AVAILABLE:
        ac.say("toolkitpy is not importable here. Use the 32-bit SpecEchem32 env.")
        return 1
    tkp = potentiostat.tkp
    settings = dict(DEFAULT_SETTINGS)
    settings.update(load_bench_defaults()[0])
    ac.say("*** Dummy only. %s V, one curve per step, StopAt %.0e A after %d points. ***"
           % (STEPS_V, STOP_ABOVE_A, STOP_DELAY_PTS))
    tkp.toolkitpy_init("spec-echem-curve-per-step-probe")
    pstat = None
    s1 = g1 = s2 = g2 = []
    try:
        pstat = tkp.Pstat("PSTAT")
        pstat.set_ctrl_mode(tkp.PSTATMODE)
        how, _ = potentiostat.initialize_pstat(
            pstat, settings.get("gamry_current_range", 6.0e-3))
        ac.say("  current range: %s" % how)
        pstat.set_voltage(STEPS_V[0])
        pstat.set_cell(True)                       # ON once, for both passes
        s1, g1 = staircase(tkp, pstat, read_gap=True)
        show("PASS 1 -- reading the potential in each gap (adds ~177 ms)", s1, g1, True)
        s2, g2 = staircase(tkp, pstat, read_gap=False)
        show("PASS 2 -- nothing in the gap: the minimum", s2, g2, False)
    finally:
        if pstat is not None:
            ac.safe(lambda: pstat.set_cell(False))
        ac.safe(tkp.toolkitpy_close)
        ac.say("\nCell switched OFF, session closed.")

    ac.rule("VERDICT")
    ok_steps = [s for s in s2 if not s["err"]]
    short = [s for s in ok_steps if s["points"] <= STOP_DELAY_PTS + 3]
    cells = [g.get("cell") for g in g1 + g2]
    ac.say("  StopAt ended %d of %d curves early." % (len(short), len(ok_steps)))
    ac.say("  The cell read ON in %d of %d gaps." % (sum(1 for c in cells if c), len(cells)))
    ac.say("  If every gap is ON, the applied potential in pass 1 is the previous")
    ac.say("  step's, and pass 2's gaps are small, a curve-per-step PITT works.")
    ac.say("  Send this report back.")
    ac.write_transcript(os.path.join(HERE, "probe_gamry_curve_per_step_report.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
