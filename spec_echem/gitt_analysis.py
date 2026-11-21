"""
GITT analysis: per pulse the charge, the relaxed potential, the IR drop, the
capacitance, the leak and the Weppner-Huggins D.

Reads a `{folder}_gitt.h5` (docs/data-format.md §6) and never writes to it, as
pitt_analysis does for a PITT.

Per titration step (one pulse at I, then a relaxation at 0 A):
  - the pulse samples are those carrying more than half the set current;
  - q, the measured current integrated over the whole step. At 0 A the Autolab
    still passes its zero offset (gitt.AUTOLAB_ZERO_OFFSET_FRACTION), and that is
    charge the film received too; q_set = I x the applied pulse time is the check;
  - E_relaxed, the mean potential over the relaxation's tail, and its drift dE/dt
    over the last `drift_window_s`. A relaxation that has not levelled off makes
    E_relaxed a bound, not an equilibrium value;
  - R from the IR step when the current switches off: E at the end of the pulse
    (the last pulse sample carried forward along the pulse's own slope to the
    applied pulse time) minus the first relaxation sample, over I. Without that
    carry the charge passed between the last sample and the switch-off reads as a
    smaller R (2/3 of it on a 3 mF film at 100 ms sampling);
  - dE_s, E_relaxed less the previous step's, and C = q / dE_s;
  - the leak: at 0 A a film that keeps losing charge drifts, and
    i_leak = -C x dE/dt. q_leak = i_leak x the step's duration is the charge that
    went somewhere other than the film, IF the leak was steady at its end-of-step
    rate. It is an estimate, given beside C (as C_leak), never folded into it;
  - dE_t, the change in potential during the pulse with the IR step excluded, and
    the slope dE/dsqrt(t) with how linear the pulse is in sqrt(t);
  - D by Weppner-Huggins for a film of thickness L:
        D = (4 L^2 / (pi tau)) (dE_s / dE_t)^2
    valid only while tau << L^2 / D. The step says when that does not hold.

Every number is returned; a step a check objects to carries the reason and the
status `needs_review`. The software raises concerns, the scientist decides.

Not here yet: the spectra per step (bleach and polaron vs charge), and the GUI.
"""
import json
import math

import numpy as np

from spec_echem import charge
from spec_echem.gitt import END_E_LIMIT, ROLE_REST, ROLE_TITRATION

DEFAULT_TAIL_S = 5.0
TAIL_FRACTION = 0.2          # of the relaxation; a longer tail averages the decay
DEFAULT_DRIFT_WINDOW_S = 10.0
RELAX_SKIP_S = 1.0           # gitt.RELAX_SKIP_S: the IR step is not drift
PULSE_SKIP_S = 0.0           # the sqrt(t) fit uses the whole pulse after its first sample
# A relaxation whose drift, carried over the drift window, moves E by more than this
# fraction of the step's dE_s has not levelled off.
DRIFT_FRACTION = 0.10
# Weppner-Huggins wants tau << L^2/D; tau above this fraction of L^2/D is not "<<".
WH_TAU_FRACTION = 0.1
SQRT_T_R2_MIN = 0.99
Q_SET_FRACTION = 0.05        # measured vs set charge, before it is a concern


class GittStepResult:
    """One step's numbers. Every field is filled where it can be, flagged or not."""

    def __init__(self, **kw):
        self.index = kw.get("index")
        self.role = kw.get("role")
        self.current_set = kw.get("current_set")      # A
        self.end_reason = kw.get("end_reason")
        self.hold_s = kw.get("hold_s")
        self.pulse_s = kw.get("pulse_s")              # s, from the samples
        self.q = kw.get("q")                          # C, measured, whole step
        self.q_set = kw.get("q_set")                  # C, I x applied pulse
        self.q_total = kw.get("q_total")              # C, running sum of q
        self.e_start = kw.get("e_start")              # V, relaxed E before the pulse
        self.e_pulse_first = kw.get("e_pulse_first")  # V, first sample under current
        self.e_pulse_end = kw.get("e_pulse_end")      # V, last sample under current
        self.e_relaxed = kw.get("e_relaxed")          # V, tail mean
        self.drift = kw.get("drift")                  # V/s at the step's end
        self.r_off = kw.get("r_off")                  # ohm, from the switch-off step
        self.de_s = kw.get("de_s")                    # V
        self.de_t = kw.get("de_t")                    # V, IR excluded
        self.c = kw.get("c")                          # F
        self.i_leak = kw.get("i_leak")                # A
        self.q_leak = kw.get("q_leak")                # C
        self.c_leak = kw.get("c_leak")                # F, with q_leak taken out
        self.sqrt_slope = kw.get("sqrt_slope")        # V / s^0.5
        self.sqrt_r2 = kw.get("sqrt_r2")
        self.d = kw.get("d")                          # cm^2/s
        self.tau_limit_s = kw.get("tau_limit_s")      # L^2/D
        self.reasons = list(kw.get("reasons", ()))
        self.notes = list(kw.get("notes", ()))

    @property
    def status(self):
        return "needs_review" if self.reasons else "ok"


def _fit_line(x, y):
    """(slope, r2) of a straight line, or (None, None) with fewer than 3 points."""
    if x.size < 3 or np.ptp(x) <= 0:
        return None, None
    slope, icept = np.polyfit(x, y, 1)
    resid = y - (slope * x + icept)
    ss = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid ** 2)) / ss if ss > 0 else None
    return float(slope), r2


def analyse_gitt_step(row, time, current, potential, prev=None, thickness_nm=None,
                      tail_s=DEFAULT_TAIL_S, drift_window_s=DEFAULT_DRIFT_WINDOW_S):
    """The numbers for one step. `row` is the step's entry in `gitt_steps_json`;
    `prev` the GittStepResult of the step before it in run order, or None."""
    t = np.asarray(time, dtype=float)
    i = np.asarray(current, dtype=float)
    v = np.asarray(potential, dtype=float)
    ok = np.isfinite(t) & np.isfinite(i) & np.isfinite(v)
    t, i, v = t[ok], i[ok], v[ok]
    current_set = float(row.get("current_set_A") or 0.0)
    out = dict(index=int(row["index"]), role=row.get("role"), current_set=current_set,
               end_reason=row.get("end_reason"))
    reasons, notes = [], []
    if row.get("end_reason") == END_E_LIMIT:
        reasons.append("the potential left the window: the run stopped in this step")
    elif row.get("end_reason") not in (None, "fixed", "relaxed"):
        reasons.append("the step ended %s" % row.get("end_reason"))
    if t.size < 3:
        reasons.append("fewer than 3 samples")
        return GittStepResult(reasons=reasons, notes=notes, **out)

    # Time is the step's own clock (the pulse starts at 0); not re-zeroed to the
    # first sample, which lands ~50 ms in.
    duration = float(t[-1] - t[0])
    out["hold_s"] = duration
    q = charge.integrate(t, i)
    out["q"] = q
    out["q_total"] = q + (prev.q_total if prev is not None and prev.q_total else 0.0)

    on = (np.abs(i) > 0.5 * abs(current_set)) if current_set else np.zeros(t.size, bool)
    relax = np.ones(t.size, dtype=bool)
    if on.any():
        first, last = int(np.argmax(on)), int(np.flatnonzero(on)[-1])
        relax[: last + 1] = False
        t_off = float(t[last])
        out["pulse_s"] = t_off - float(t[first])
        applied = row.get("pulse_s_applied") or row.get("pulse_s")
        if applied:
            out["q_set"] = current_set * float(applied)
            if abs(q - out["q_set"]) > Q_SET_FRACTION * abs(out["q_set"]):
                reasons.append("measured charge %.3g C is not the set %.3g C"
                               % (q, out["q_set"]))
        out["e_pulse_first"] = float(v[first])
        out["e_pulse_end"] = float(v[last])
        after = np.flatnonzero(relax)[:1]
        if after.size:
            e_end = float(v[last])
            t_end = float(applied) if applied else t_off
            near = on & (t >= t_off - 1.0)
            slope_end, _ = _fit_line(t[near], v[near])
            if slope_end is not None and t_off < t_end <= float(t[after[0]]):
                e_end += slope_end * (t_end - t_off)
            out["r_off"] = (e_end - float(v[after[0]])) / current_set
        # The pulse in sqrt(time from its start): the step's clock starts with it.
        tp = t[first: last + 1]
        sel = tp >= PULSE_SKIP_S
        out["sqrt_slope"], out["sqrt_r2"] = _fit_line(np.sqrt(np.clip(tp[sel], 0, None)),
                                                      v[first: last + 1][sel])
    else:
        t_off = 0.0
        if current_set:
            reasons.append("no sample carried the set current")

    # The relaxation's tail and its drift.
    tr, vr = t[relax], v[relax]
    if tr.size >= 2:
        span = float(tr[-1] - tr[0])
        tail = tr >= tr[-1] - min(tail_s, TAIL_FRACTION * span if span > 0 else tail_s)
        if tail.sum() < 2:
            tail = np.zeros(tr.size, dtype=bool)
            tail[-2:] = True
        out["e_relaxed"] = float(np.mean(vr[tail]))
        win = (tr >= max(tr[-1] - drift_window_s, t_off + RELAX_SKIP_S))
        slope, _r2 = _fit_line(tr[win], vr[win])
        out["drift"] = slope
    else:
        reasons.append("no relaxation samples")

    e_start = prev.e_relaxed if prev is not None else None
    out["e_start"] = e_start
    if out.get("e_pulse_first") is not None and out.get("e_pulse_end") is not None:
        out["de_t"] = out["e_pulse_end"] - out["e_pulse_first"]

    if row.get("role") == ROLE_TITRATION and e_start is not None and \
            out.get("e_relaxed") is not None:
        de_s = out["e_relaxed"] - e_start
        out["de_s"] = de_s
        if de_s != 0:
            out["c"] = q / de_s
        drift = out.get("drift")
        if drift is not None and de_s:
            if abs(drift) * drift_window_s > DRIFT_FRACTION * abs(de_s):
                reasons.append(
                    "not relaxed: E still moving %+.0f uV/s, %.0f%% of dE_s over %.0f s;"
                    " E_relaxed is a bound" % (drift * 1e6,
                                               100 * abs(drift) * drift_window_s / abs(de_s),
                                               drift_window_s))
            if out.get("c") is not None:
                out["i_leak"] = -out["c"] * drift
                out["q_leak"] = out["i_leak"] * duration
                if q - out["q_leak"] != 0:
                    out["c_leak"] = (q - out["q_leak"]) / de_s
                notes.append("q_leak assumes the end-of-step leak held for the whole "
                             "step")
        if out.get("sqrt_r2") is not None and out["sqrt_r2"] < SQRT_T_R2_MIN:
            reasons.append("the pulse is not linear in sqrt(t) (R2 %.3f)" % out["sqrt_r2"])
        de_t = out.get("de_t")
        tau = out.get("pulse_s")
        if thickness_nm and de_t and tau and tau > 0:
            length_cm = float(thickness_nm) * 1e-7
            d = 4.0 * length_cm ** 2 / (math.pi * tau) * (de_s / de_t) ** 2
            out["d"] = d
            out["tau_limit_s"] = length_cm ** 2 / d
            if tau > WH_TAU_FRACTION * out["tau_limit_s"]:
                reasons.append("tau %.3g s is not << L^2/D = %.3g s: D is not valid"
                               % (tau, out["tau_limit_s"]))
    return GittStepResult(reasons=reasons, notes=notes, **out)


def read_steps_json(path):
    """(steps, completed, error) from a GITT h5's root attributes."""
    import h5py
    with h5py.File(path, "r") as f:
        steps = json.loads(f.attrs["gitt_steps_json"])
        completed = bool(f.attrs.get("gitt_completed", False))
        error = f.attrs.get("gitt_error")
    return steps, completed, (str(error) if error is not None else None)


def analyse_gitt(path, thickness_nm=None, tail_s=DEFAULT_TAIL_S,
                 drift_window_s=DEFAULT_DRIFT_WINDOW_S):
    """Every step of a GITT h5, in run order. -> list of GittStepResult."""
    from spec_echem.data import read_segment_echem_h5
    from spec_echem.gamry_data import CURRENT_COL, POTENTIAL_COL
    steps, _completed, _error = read_steps_json(path)
    results, prev = [], None
    for row in steps:
        frame = read_segment_echem_h5(path, int(row["index"]))
        if frame is None or len(frame) == 0:
            continue
        res = analyse_gitt_step(row, frame["Time (s)"].to_numpy(),
                                frame[CURRENT_COL].to_numpy(),
                                frame[POTENTIAL_COL].to_numpy(), prev=prev,
                                thickness_nm=thickness_nm, tail_s=tail_s,
                                drift_window_s=drift_window_s)
        results.append(res)
        prev = res
    return results


# (name, unit), as pitt_analysis.TABLE_COLUMNS.
TABLE_COLUMNS = [
    ("step", ""), ("role", ""), ("I_set_A", "A"), ("end", ""), ("hold_s", "s"),
    ("pulse_s", "s"), ("q_C", "C"), ("q_set_C", "C"), ("Q_total_C", "C"),
    ("E_start_V", "V"), ("E_relaxed_V", "V"), ("drift_V_s", "V/s"),
    ("R_off_ohm", "ohm"), ("dE_s_V", "V"), ("dE_t_V", "V"), ("C_F", "F"),
    ("i_leak_A", "A"), ("q_leak_C", "C"), ("C_leak_F", "F"),
    ("dE_dsqrt_t", "V s^-0.5"), ("sqrt_t_R2", ""), ("D_cm2_s", "cm^2/s"),
    ("L2_over_D_s", "s"), ("status", ""), ("reasons", ""), ("notes", ""),
]


def gitt_table_rows(results):
    """One dict per step, keys as TABLE_COLUMNS; a missing value is None."""
    return [{
        "step": r.index, "role": r.role, "I_set_A": r.current_set, "end": r.end_reason,
        "hold_s": r.hold_s, "pulse_s": r.pulse_s, "q_C": r.q, "q_set_C": r.q_set,
        "Q_total_C": r.q_total, "E_start_V": r.e_start, "E_relaxed_V": r.e_relaxed,
        "drift_V_s": r.drift, "R_off_ohm": r.r_off, "dE_s_V": r.de_s,
        "dE_t_V": r.de_t, "C_F": r.c, "i_leak_A": r.i_leak, "q_leak_C": r.q_leak,
        "C_leak_F": r.c_leak, "dE_dsqrt_t": r.sqrt_slope, "sqrt_t_R2": r.sqrt_r2,
        "D_cm2_s": r.d, "L2_over_D_s": r.tau_limit_s, "status": r.status,
        "reasons": "; ".join(r.reasons), "notes": "; ".join(r.notes),
    } for r in results]
