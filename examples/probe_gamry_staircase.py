"""
probe_gamry_staircase.py -- can the Gamry run a PITT staircase on its OWN clock?

The direct-control Gamry PITT (probe_gamry_pitt.py) works, but every measure_v or
measure_i takes ~176 ms (Reference 600, 2026-10-05), so the current could only be
read at ~5.6 Hz. The toolkit can instead run the staircase as a curve, hardware-
timed, the way a normal chrono segment runs:

  * signal_array2_new(...)   any list of potentials, each repeated N samples --
                             one waveform, each step its own hold
  * set_stop_x_min(...)      "once the StopAt criterion is met, data acquisition
                             SKIPS TO THE NEXT SIGNAL SECTION"
  * set_stop_at_delay_x_min  the criterion must hold for N CONSECUTIVE points

Three things only the instrument can answer, one run each:

  A  baseline   the staircase on the internal clock, no StopAt: the real sample
                period, each step's length, and the potential at each step
  B  StopAt     the same with StopAt set ABOVE the dummy's DC current (100 uA vs
                at most ~47 uA on the UDC4 EIS side), delay 5 points. If 'next
                section' means NEXT STEP, every step ends after ~5 points instead
                of its full 3 s hold.
  C  fast       ONLY with --fast: a 0.05 s sample period (the manual recommends
                >= 0.1 s). On 2026-10-05 it timed the instrument out -- possibly
                the period, possibly the probe bug above it; not yet separated.

and, in every run, whether Python can follow WHICH STEP is running while the curve
runs (curve.count() / last_data_point()), so spectra can be tagged live.

    >> A DUMMY CELL ONLY: the UDC4 EIS side (3210 Ohm DC) or a 2 kOhm. <<

Run in SpecEchem32 from the repo folder:

    python examples/probe_gamry_staircase.py --energize

Writes examples/probe_gamry_staircase_report.txt and one CSV per run.
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

STEPS_V = [0.0, 0.05, 0.10, 0.15]
HOLD_S = 3.0
STOP_ABOVE_A = 1e-4        # above the dummy's DC current, so StopAt MUST fire
STOP_DELAY_PTS = 5
POLL_S = 0.05


def make_curve(tkp, pstat, max_points):
    """ChronoACurve per the manual; ChronoCurve is what our driver runs today."""
    for name in ("ChronoACurve", "ChronoCurve"):
        cls = getattr(tkp, name, None)
        if cls is not None:
            return cls(pstat, max_points), name
    raise RuntimeError("toolkitpy has neither ChronoACurve nor ChronoCurve")


def field(data, *names):
    """A column as floats. last_data_point() returns ONE row, whose fields are
    scalars -- iterating one raised TypeError on the Reference 600 (2026-10-05)
    and, worse, ended the run with its curve still going on the instrument."""
    import numpy as np
    for n in names:
        if data is not None and data.dtype.names and n in data.dtype.names:
            return [float(x) for x in np.atleast_1d(data[n])]
    return None


def nearest_step(v):
    return min(range(len(STEPS_V)), key=lambda k: abs(STEPS_V[k] - v))


def run_once(tkp, pstat, label, period, stop_at):
    """One staircase. Returns (data, live, curve_class, error)."""
    repeats = [int(round(HOLD_S / period))] * len(STEPS_V)
    keep = []                                   # the signal MUST outlive the run
    curve = None
    live = []
    try:
        bias_none = getattr(tkp, "BIAS_NONE", 0)
        signal = pstat.signal_array2_new(0.0, 1, float(period), list(STEPS_V),
                                         repeats, bias_none, tkp.PSTATMODE)
        keep.append(signal)
        pstat.set_signal_array2(signal)
        pstat.init_signal()
        curve, cls = make_curve(tkp, pstat, sum(repeats) + 100)
        if stop_at:
            curve.set_stop_x_min(True, STOP_ABOVE_A)
            curve.set_stop_at_delay_x_min(STOP_DELAY_PTS)
        pstat.set_cell(True)
        t0 = time.perf_counter()
        curve.run(True)
        while curve.running():
            # NOTHING in here may end the run: an exception would leave the curve
            # running on the instrument with Python gone (2026-10-05).
            n = ac.safe(lambda: int(curve.count()), -1)
            vf = ac.safe(lambda: field(curve.last_data_point(), "Vf", "vf"), None)
            live.append((time.perf_counter() - t0, n, vf[-1] if vf else float("nan")))
            time.sleep(POLL_S)
        data = curve.acq_data()
        return data, live, cls, None
    except Exception as exc:  # noqa: BLE001
        return None, live, None, "%s: %s" % (type(exc).__name__, exc)
    finally:
        # Stop the curve and WAIT for it before the cell goes off and before the
        # next run loads a new signal. The first version only switched the cell
        # off, and the next run's init_signal landed on a curve still running.
        if curve is not None:
            if ac.safe(lambda: curve.running(), False):
                ac.safe(curve.stop)
                deadline = time.perf_counter() + 5.0
                while ac.safe(lambda: curve.running(), False) \
                        and time.perf_counter() < deadline:
                    time.sleep(0.05)
        ac.safe(lambda: pstat.set_cell(False))


def report(label, period, stop_at, data, live, cls, err):
    ac.rule("RUN %s  (sample period %.3f s%s)"
            % (label, period, ", StopAt %.0e A after %d points" % (STOP_ABOVE_A,
                                                                   STOP_DELAY_PTS)
               if stop_at else ""))
    if err:
        ac.say("  FAILED: %s" % err)
        return None
    names = data.dtype.names if data is not None else None
    ac.say("  curve class: %s;  data fields: %s" % (cls, names))
    t = field(data, "T", "time", "Time")
    vf = field(data, "Vf", "vf")
    im = field(data, "Im", "im")
    if not t or not vf:
        ac.say("  no time/potential columns -- cannot analyse")
        return None
    with open(os.path.join(HERE, "probe_gamry_staircase_%s.csv" % label), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "vf_V", "im_A"])
        w.writerows(zip(t, vf, im or [float("nan")] * len(t)))
    dts = sorted(b - a for a, b in zip(t, t[1:]))
    ac.say("  %d points; sample period median %.4f s (asked %.3f), min %.4f, max %.4f"
           % (len(t), dts[len(dts) // 2], period, dts[0], dts[-1]))
    # Steps from the potential itself -- the device's own record of what it applied.
    steps = []
    for k_t, k_v in zip(t, vf):
        k = nearest_step(k_v)
        if not steps or steps[-1][0] != k:
            steps.append([k, k_t, k_t, 0])
        steps[-1][2] = k_t
        steps[-1][3] += 1
    ac.say("  step  set (V)   points   length (s)")
    for k, ta, tb, n in steps:
        ac.say("  %4d  %+.3f   %6d   %8.2f" % (k, STEPS_V[k], n, tb - ta + period))
    if live:
        moves = [(lt, n, v) for (lt, n, v) in live if not math.isnan(v)]
        changes = sum(1 for a, b in zip(moves, moves[1:])
                      if nearest_step(a[2]) != nearest_step(b[2]))
        ac.say("  live: %d polls; the running step was visible in %d of them, and %d "
               "step changes were seen live" % (len(live), len(moves), changes))
    return steps


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="actually switch the cell on (DUMMY CELL ONLY)")
    ap.add_argument("--fast", action="store_true",
                    help="also try a 0.05 s sample period, below the manual's "
                         "recommended 0.1 s. Run C timed the instrument out on "
                         "2026-10-05 (cause not yet separated from a probe bug).")
    args = ap.parse_args()
    ac.rule("CAN THE GAMRY RUN A PITT STAIRCASE ON ITS OWN CLOCK?")
    if not args.energize:
        ac.say("Refusing to energize: re-run with --energize, with a DUMMY cell in.")
        return 1
    if not potentiostat.TOOLKITPY_AVAILABLE:
        ac.say("toolkitpy is not importable here. Use the 32-bit SpecEchem32 env.")
        return 1
    tkp = potentiostat.tkp
    settings = dict(DEFAULT_SETTINGS)
    bench, _w = load_bench_defaults()
    settings.update(bench)
    ac.say("*** Dummy only. Staircase %s V, %.0f s a step. ***" % (STEPS_V, HOLD_S))

    results = {}
    tkp.toolkitpy_init("spec-echem-staircase-probe")
    try:
        pstat = tkp.Pstat("PSTAT")
        pstat.set_ctrl_mode(tkp.PSTATMODE)
        how, _full = potentiostat.initialize_pstat(
            pstat, settings.get("gamry_current_range", 6.0e-3))
        ac.say("  current range: %s" % how)
        runs = [("A_baseline", 0.1, False), ("B_stopat", 0.1, True)]
        if args.fast:
            runs.append(("C_fast", 0.05, False))
        for label, period, stop_at in runs:
            data, live, cls, err = run_once(tkp, pstat, label, period, stop_at)
            results[label] = report(label, period, stop_at, data, live, cls, err)
            time.sleep(0.5)
    finally:
        ac.safe(lambda: pstat.set_cell(False))
        ac.safe(tkp.toolkitpy_close)
        ac.say("\nCell switched OFF, session closed.")

    ac.rule("VERDICT")
    a, b = results.get("A_baseline"), results.get("B_stopat")
    if a:
        full = [s for s in a if s[3] >= int(HOLD_S / 0.1) - 2]
        ac.say("  A: %d of %d steps ran their full hold on the internal clock."
               % (len(full), len(STEPS_V)))
    if b:
        short = [s for s in b if s[3] <= STOP_DELAY_PTS + 3]
        ac.say("  B: %d of %d steps ended after ~%d points." % (len(short), len(b),
                                                             STOP_DELAY_PTS))
        if len(b) == len(STEPS_V) and len(short) >= len(STEPS_V) - 1:
            ac.say("     -> StopAt SKIPS TO THE NEXT STEP: the firmware can end a PITT")
            ac.say("        step early, with the consecutive-point delay built in.")
        elif len(b) < len(STEPS_V):
            ac.say("     -> StopAt ended the WHOLE CURVE, not one step: early step-ending")
            ac.say("        would need one curve per step.")
    if args.fast:
        ac.say("  C: see its sample period above.")
    ac.say("  Send the report and the three CSVs back.")
    ac.write_transcript(os.path.join(HERE, "probe_gamry_staircase_report.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
