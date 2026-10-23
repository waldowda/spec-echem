"""
probe_gamry_gitt_eis.py -- what would GITT and ER-EIS need from the Gamry?

Both are planned as siblings of the PITT staircase (see TODO.md). Before any of it is
built, this answers the instrument questions the plan rests on, on a DUMMY cell.

Part 1 (always; read-only, the cell is never switched on):
  * the control modes, GstatRatio, impedance frequency limits, and which curve and
    impedance classes this toolkitpy has (ReadZ, ZCurve, CiivCurve, ChronoCurve)
  * which I/E range test_ie_range() picks for nA-uA pulse currents

Part 2, --gitt (galvanostatic, as GITT would run it):
  A. one array2 curve in GSTATMODE: 0, +I, 0, -I, 0 A, 3 s each. Does E follow
     I x R on the dummy, and does the applied current match the request (the docs say
     array2 values are in AMPS in galvanostat mode, GstatRatio applied for us)?
  B/C. a hardware E limit, set BELOW the expected I x R, on two curves: the PITT's
     ChronoCurve (StopAt "x" -- documented as the current for chronoamperometry) and
     CiivCurve, Gamry's galvanostatic curve (StopAt on vf, documented as volts). Can
     the instrument itself stop a GITT pulse before a film is driven to compliance?

Part 3, --eis (one ER-EIS step, potentiostatic):
  at --vdc (0.1 V): hold 2 s (the PITT curve) -> impedance at a few frequencies
  (ReadZ) -> hold 2 s, with the cell left ON throughout. It reports:
  * Z against the dummy's expected Z at each frequency, and the time each point took
    (the number an ER-EIS duration estimate needs)
  * one 10 kHz point measured with the PITT's channel filters (1 kHz at 10 points/s)
    and again with filters opened for the sweep: are they cutting the high frequencies?
  * whether the potential and current after the sweep match those before it

ReadZ's amplitude units are not in the toolkitpy help (Gamry's example comment says V
but its values look like mV). --vac 0.01 is safe under either reading (10 mV or 10 uV
rms); the measured AC amplitudes printed below settle which it is.

    >> A DUMMY CELL ONLY, never a film: the UDC4 Randles side, 200 Ohm + (3.01 kOhm
       || 1 uF), 3210 Ohm DC. A plain resistor works for --gitt (give --rs 0 --rp R). <<

    conda activate SpecEchem32
    python examples/probe_gamry_gitt_eis.py                    # part 1 only
    python examples/probe_gamry_gitt_eis.py --energize --gitt
    python examples/probe_gamry_gitt_eis.py --energize --eis
    python examples/probe_gamry_gitt_eis.py --energize --gitt --eis

Writes examples/probe_gamry_gitt_eis_report.txt.
"""
import argparse
import cmath
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

PERIOD = 0.1
LEVEL_PTS = 30                       # 3 s per level at 0.1 s
HOLD_PTS = 20                        # 2 s holds around the impedance sweep
FREQS_HZ = (10000.0, 1000.0, 100.0, 10.0, 1.0)
STOP_DELAY_PTS = 3


def column(data, *names):
    import numpy as np
    for n in names:
        if data is not None and data.dtype.names and n in data.dtype.names:
            return [float(x) for x in np.atleast_1d(data[n])]
    return []


def median(xs):
    xs = sorted(x for x in xs if not math.isnan(x))
    return xs[len(xs) // 2] if xs else float("nan")


def expected_z(f, rs, rp, c):
    """Randles: rs + (rp || c). With c = 0 it is a plain resistor rs + rp."""
    if c <= 0:
        return complex(rs + rp, 0.0)
    w = 2 * math.pi * f
    return rs + rp / complex(1.0, w * rp * c)


def run_curve(tkp, pstat, mode, values, repeats, guard_v=None, stop_max=None,
              curve_cls="ChronoCurve"):
    """One array2 curve, run to its end (or stopped). Never returns with it running.

    guard_v: a Python-side |E| limit checked on every harvest -- the belt to the
    instrument's braces, as a GITT would need. stop_max: the instrument's own StopAt,
    on x for a ChronoCurve (documented as the CURRENT for ChronoACurve) or on vf for
    a CiivCurve (documented as the potential), to test which can bound a GITT pulse.
    """
    keep, curve = [], None
    n = sum(repeats)
    out = {"err": None, "guard_tripped": False, "points": 0}
    try:
        signal = pstat.signal_array2_new(0.0, 1, PERIOD, [float(v) for v in values],
                                         [int(r) for r in repeats],
                                         getattr(tkp, "BIAS_NONE", 0), mode)
        keep.append(signal)                         # must outlive the run
        pstat.set_signal_array2(signal)
        pstat.init_signal()
        curve = getattr(tkp, curve_cls)(pstat, n + 50)
        if stop_max is not None:
            if curve_cls == "CiivCurve":
                curve.set_stop_v_max(True, float(stop_max))
                curve.set_stop_at_delay_v_max(STOP_DELAY_PTS)
            else:
                curve.set_stop_x_max(True, float(stop_max))
                curve.set_stop_at_delay_x_max(STOP_DELAY_PTS)
        t0 = time.perf_counter()
        curve.run(True)
        while curve.running():
            if guard_v is not None:
                vf = column(curve.acq_data(), "vf")
                if vf and max(abs(x) for x in vf) > guard_v:
                    out["guard_tripped"] = True
                    break
            time.sleep(0.05)
        out["elapsed_s"] = time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        out["err"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if curve is not None and ac.safe(lambda: curve.running(), False):
            ac.safe(curve.stop)
            deadline = time.perf_counter() + 5.0
            while ac.safe(lambda: curve.running(), False) \
                    and time.perf_counter() < deadline:
                time.sleep(0.05)
    if curve is not None:
        data = ac.safe(curve.acq_data)
        out["t"] = column(data, "time")
        out["vf"] = column(data, "vf")
        out["im"] = column(data, "im")
        out["points"] = len(out["t"])
    return out


# --- part 1 -----------------------------------------------------------------

def part1(tkp, pstat):
    ac.rule("PART 1 -- WHAT THIS INSTRUMENT AND TOOLKIT OFFER (read-only)")
    ac.say("  instrument:          %s" % ac.safe(pstat.label, "?"))
    ac.say("  control modes:       %s" % ac.safe(pstat.ctrl_mode_label_list, "?"))
    ac.say("  gstat_ratio:         %s" % ac.safe(pstat.gstat_ratio, "?"))
    ac.say("  impedance f range:   %s .. %s Hz" % (ac.safe(pstat.freq_limit_lower, "?"),
                                                    ac.safe(pstat.freq_limit_upper, "?")))
    for name in ("ReadZ", "ZCurve", "FraCurve", "CiivCurve", "ChronoCurve",
                 "check_eis_points", "GSTATMODE", "PSTATMODE"):
        ac.say("  tkp.%-18s %s" % (name + ":", "yes" if hasattr(tkp, name) else "NO"))
    if hasattr(tkp, "ReadZ"):
        names = [n for n in dir(tkp.ReadZ) if not n.startswith("_")]
        ac.say("  ReadZ methods:       %s" % ", ".join(names))
    if hasattr(tkp, "CiivCurve"):
        names = [n for n in dir(tkp.CiivCurve) if n.startswith("set_stop")]
        ac.say("  CiivCurve stops:     %s" % ", ".join(names))
    ac.say("")
    ac.say("  test_ie_range(I) for pulse-sized currents (the rung it would pick):")
    for amps in (1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4):
        idx = ac.safe(lambda a=amps: pstat.test_ie_range(a), "?")
        full = potentiostat.gamry_range_full_scale(idx) if isinstance(idx, int) else None
        ac.say("    %.0e A -> IERange %s%s" % (amps, idx,
                                              (" (%.3g A full scale)" % full) if full else ""))


# --- part 2 -----------------------------------------------------------------

def part2(tkp, pstat, settings, args):
    r_dc = args.rs + args.rp
    amps = args.amps if args.amps else 0.1 / r_dc
    e_expect = amps * r_dc
    ac.rule("PART 2 -- GALVANOSTATIC (GITT) on %.0f Ohm DC, I = %+.3e A -> %+.4f V expected"
            % (r_dc, amps, e_expect))
    if abs(e_expect) > 0.5 * args.e_limit:
        ac.say("  REFUSED: I x R = %.3f V is more than half the --e-limit %.3f V."
               % (e_expect, args.e_limit))
        return
    pstat.set_ctrl_mode(tkp.GSTATMODE)
    how, _full = potentiostat.initialize_pstat(pstat, 3.0 * abs(amps),
                                               sample_period_s=PERIOD)
    ac.say("  range: %s" % how)
    ac.say("  ctrl mode now: %s" % ac.safe(pstat.ctrl_mode, "?"))
    pstat.set_cell(True)
    try:
        levels = [0.0, amps, 0.0, -amps, 0.0]
        a = run_curve(tkp, pstat, tkp.GSTATMODE, levels, [LEVEL_PTS] * len(levels),
                      guard_v=args.e_limit)
        ac.say("")
        ac.say("  A. pulse train 0, +I, 0, -I, 0 (3 s each):")
        if a["err"]:
            ac.say("     FAILED: %s" % a["err"])
        else:
            if a["guard_tripped"]:
                ac.say("     *** Python |E| guard tripped at %.3f V -- stopped. ***" % args.e_limit)
            ac.say("     level   I set (A)     I meas (A)    E meas (V)    E / (I x R)")
            for k, lev in enumerate(levels):
                lo, hi = k * LEVEL_PTS, (k + 1) * LEVEL_PTS
                tail = slice(lo + LEVEL_PTS // 2, hi)          # the settled half
                i_m = median(a["im"][tail])
                e_m = median(a["vf"][tail])
                ratio = (e_m / (lev * r_dc)) if lev else float("nan")
                ac.say("     %5d   %+.3e    %+.3e    %+.5f      %s"
                       % (k, lev, i_m, e_m, ("%.3f" % ratio) if lev else "  --"))
            ac.say("     points %d of %d, %.1f s" % (a["points"], len(levels) * LEVEL_PTS,
                                                    a.get("elapsed_s", float("nan"))))

        # A positive pulse, so a "max" stop is the one that bounds it.
        stop_at = 0.5 * abs(e_expect)
        tests = [("B", "ChronoCurve", "StopAt x_max"),
                 ("C", "CiivCurve", "StopAt v_max")]
        for tag, cls, what in tests:
            ac.say("")
            ac.say("  %s. %s: +I for up to 3 s, %s = %+.4f (half of I x R in volts):"
                   % (tag, cls, what, stop_at))
            if not hasattr(tkp, cls):
                ac.say("     this toolkitpy has no %s." % cls)
                continue
            b = run_curve(tkp, pstat, tkp.GSTATMODE, [abs(amps)], [LEVEL_PTS],
                          guard_v=args.e_limit, stop_max=stop_at, curve_cls=cls)
            if b["err"]:
                ac.say("     FAILED: %s" % b["err"])
                continue
            ac.say("     %d of %d points, median E %+.5f V, median I %+.3e A"
                   % (b["points"], LEVEL_PTS, median(b.get("vf", [])),
                      median(b.get("im", []))))
            if b["points"] and b["points"] <= STOP_DELAY_PTS + 4:
                ac.say("     -> the stop ENDED it: this curve can bound a GITT pulse on E")
                ac.say("        in the instrument itself.")
            else:
                ac.say("     -> the stop did NOT end it: on this curve the stop is not the")
                ac.say("        potential (or is ignored), so it cannot bound a pulse.")
    finally:
        ac.safe(lambda: pstat.set_cell(False))
        ac.safe(lambda: pstat.set_ctrl_mode(tkp.PSTATMODE))
        ac.say("  cell OFF, back to PSTATMODE.")


# --- part 3 -----------------------------------------------------------------

def open_filters_for(pstat, f_max):
    """Channel filters wide enough for f_max; returns what they were, to restore."""
    before = (ac.safe(pstat.ich_filter), ac.safe(pstat.vch_filter))
    pstat.set_ich_filter(pstat.test_ich_filter(f_max))
    pstat.set_vch_filter(pstat.test_vch_filter(f_max))
    return before


def one_z(tkp, pstat, readz, zcurve, f, vac, vdc):
    t0 = time.perf_counter()
    ok = ac.safe(lambda: bool(readz.measure(f, vac, vdc)), False)
    dt = time.perf_counter() - t0
    if ok:
        zcurve.add_point(readz, ac.safe(pstat.measure_temp, 0.0))
    return ok, dt


def part3(tkp, pstat, settings, args):
    vdc = args.vdc
    ac.rule("PART 3 -- ONE ER-EIS STEP: hold, impedance, hold (cell ON throughout)")
    if not (hasattr(tkp, "ReadZ") and hasattr(tkp, "ZCurve")):
        ac.say("  this toolkitpy has no ReadZ/ZCurve; nothing to test.")
        return
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    how, _full = potentiostat.initialize_pstat(
        pstat, settings.get("gamry_current_range", 6.0e-3), sample_period_s=PERIOD)
    ac.say("  range for the holds: %s" % how)
    pstat.set_voltage(vdc)
    pstat.set_cell(True)
    try:
        h1 = run_curve(tkp, pstat, tkp.PSTATMODE, [vdc], [HOLD_PTS])
        idc = median(h1.get("im", []))
        r_dc = args.rs + args.rp
        est_z = abs(expected_z(FREQS_HZ[-1], args.rs, args.rp, args.c)) or r_dc
        # The sweep's range, as Gamry's own EIS example sets it: the DC current plus
        # the AC peak at the smallest |Z| expected. Kept for the second hold too.
        z_min = min(abs(expected_z(f, args.rs, args.rp, args.c)) for f in FREQS_HZ)
        i_peak = (0.0 if math.isnan(idc) else abs(idc)) + 1.414 * abs(args.vac) / z_min
        ie = ac.safe(lambda: pstat.test_ie_range(i_peak))
        if ie is not None:
            ac.safe(lambda: pstat.set_ie_range(ie))
        ac.say("  range for the sweep: IERange %s (for a %.3g A peak)" % (ie, i_peak))
        readz = tkp.ReadZ(pstat)
        for setter, value in (("set_gain", 1.0), ("set_inoise", 0.0), ("set_vnoise", 0.0),
                              ("set_ienoise", 0.0), ("set_zmod", est_z),
                              ("set_vdc", vdc), ("set_speed", 1),
                              ("set_drift_cor", False),
                              ("set_idc", 0.0 if math.isnan(idc) else idc)):
            ac.safe(lambda s=setter, v=value: getattr(readz, s)(v))
        zcurve = tkp.ZCurve(len(FREQS_HZ) + 4)

        # 10 kHz with the PITT's filters, before opening them.
        ok_f, dt_f = one_z(tkp, pstat, readz, zcurve, FREQS_HZ[0], args.vac, vdc)
        filters_before = open_filters_for(pstat, max(FREQS_HZ))
        timings = []
        for f in FREQS_HZ:
            timings.append((f,) + one_z(tkp, pstat, readz, zcurve, f, args.vac, vdc))
        ac.safe(lambda: pstat.set_ich_filter(filters_before[0]))
        ac.safe(lambda: pstat.set_vch_filter(filters_before[1]))
        h2 = run_curve(tkp, pstat, tkp.PSTATMODE, [vdc], [HOLD_PTS])
    finally:
        ac.safe(lambda: pstat.set_cell(False))
        ac.say("  cell OFF.")

    data = ac.safe(zcurve.acq_data)
    names = list(data.dtype.names) if data is not None and data.dtype.names else []
    ac.say("  ZCurve fields: %s" % ", ".join(names))
    zf, zr, zi = column(data, "zfreq"), column(data, "zreal"), column(data, "zimag")
    ac.say("")
    ac.say("  first row = 10 kHz with the hold's filters; then the sweep with them opened")
    ac.say("      f (Hz)    Z' meas    Z'' meas     |Z| expected  |Z| err   phase err   time (s)")
    rows_t = [(FREQS_HZ[0], ok_f, dt_f)] + timings
    j = 0
    for f, ok, dt in rows_t:
        if not ok or j >= len(zr):
            ac.say("    %8.1f    (bad value)                                              %6.2f" % (f, dt))
            continue
        z = complex(zr[j], zi[j])
        ze = expected_z(f, args.rs, args.rp, args.c)
        err = (abs(z) - abs(ze)) / abs(ze) * 100
        dph = math.degrees(cmath.phase(z) - cmath.phase(ze))
        ac.say("    %8.1f  %9.1f  %9.1f    %9.1f    %+6.1f%%   %+6.1f deg    %6.2f"
               % (zf[j] if j < len(zf) else f, z.real, z.imag, abs(ze), err, dph, dt))
        j += 1
    for name in names:
        if name.lower() in ("vac", "iac", "vacm", "iacm", "vacmeas", "iacmeas"):
            ac.say("  %s: %s" % (name, ", ".join("%.4g" % x for x in column(data, name))))
    ac.say("")
    ac.say("  holds around the sweep (median of each 2 s hold):")
    for label, h in (("before", h1), ("after", h2)):
        if h.get("err"):
            ac.say("    %-6s FAILED: %s" % (label, h["err"]))
        else:
            ac.say("    %-6s E %+.5f V   I %+.4e A   (%d points)"
                   % (label, median(h["vf"]), median(h["im"]), h["points"]))
    ac.say("  If 'after' matches 'before', a hold -> sweep -> hold step is glitch-free.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="allow parts 2/3 to switch the cell on (DUMMY CELL ONLY)")
    ap.add_argument("--gitt", action="store_true", help="part 2: galvanostatic pulses")
    ap.add_argument("--eis", action="store_true", help="part 3: one ER-EIS step")
    ap.add_argument("--rs", type=float, default=200.0, help="dummy series R (Ohm)")
    ap.add_argument("--rp", type=float, default=3010.0, help="dummy parallel R (Ohm)")
    ap.add_argument("--c", type=float, default=1e-6, help="dummy parallel C (F); 0 = none")
    ap.add_argument("--amps", type=float, default=0.0,
                    help="GITT pulse current (A); default gives I x R = 0.1 V")
    ap.add_argument("--e-limit", type=float, default=0.5,
                    help="|E| at which part 2 stops, Python-side (V)")
    ap.add_argument("--vdc", type=float, default=0.1,
                    help="DC potential of the ER-EIS step (V); not 0, so the holds"
                         " around the sweep carry a current to compare")
    ap.add_argument("--vac", type=float, default=0.01,
                    help="ReadZ AC amplitude, units unconfirmed (see the docstring)")
    args = ap.parse_args()

    ac.rule("GITT / ER-EIS ON THE GAMRY -- what the plan needs to know")
    if not potentiostat.TOOLKITPY_AVAILABLE:
        ac.say("toolkitpy is not importable here. Use the 32-bit SpecEchem32 env.")
        return 1
    if (args.gitt or args.eis) and not args.energize:
        ac.say("Parts 2/3 need --energize, with a DUMMY cell in. Running part 1 only.")
    tkp = potentiostat.tkp
    settings = dict(DEFAULT_SETTINGS)
    settings.update(load_bench_defaults()[0])
    tkp.toolkitpy_init("spec-echem-gitt-eis-probe")
    pstat = None
    try:
        pstat = potentiostat.open_gamry_pstat(settings.get("gamry_section") or None)
        part1(tkp, pstat)
        if args.energize and args.gitt:
            ac.say("\n*** Dummy only: %.0f + (%.0f || %.2g F). ***"
                   % (args.rs, args.rp, args.c))
            part2(tkp, pstat, settings, args)
        if args.energize and args.eis:
            part3(tkp, pstat, settings, args)
    except Exception as exc:  # noqa: BLE001
        ac.say("\n*** STOPPED: %s: %s" % (type(exc).__name__, exc))
    finally:
        if pstat is not None:
            ac.safe(lambda: pstat.set_cell(False))
        ac.safe(tkp.toolkitpy_close)
        ac.say("\nCell OFF, session closed. Send this report back.")
    ac.write_transcript(os.path.join(HERE, "probe_gamry_gitt_eis_report.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
