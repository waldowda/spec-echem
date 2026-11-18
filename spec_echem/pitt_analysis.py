"""
PITT staircase analysis: the charge of each step, dQ/dE, and the density of states.

Reads a `{folder}_pitt.h5` (docs/data-format.md §5) and never writes to it. The
numbers are derived, so they go to a CSV beside the run, not into the archive.

Per step (step k goes from E_{k-1} to E_k):
  - the hold window: the samples before the first gap longer than `gap_factor`
    sample periods. On a step swept for impedance the sweep is that gap, and the
    samples after it are not part of the step's charge;
  - E_k, the MEASURED potential over the tail, never the setpoint (the Reference
    600 sits about +1 mV off);
  - I_ss, the steady (leak) current over the tail, tail = min(tail_s, 0.2 x the hold);
  - dQ = the integral of the current less I_ss over the hold. The other bound is a
    background that rose linearly from the previous step's I_ss, and half the
    difference between the two is `u_background`;
  - the head: the charge before the first sample, extrapolated back as
    charge.head_charge_max does. It is a bias of one sign, so it is shown as its
    own number and NOT folded into the uncertainty;
  - C = dQ/dE at the midpoint of the step, given twice: from the sampled charge
    and with the head added;
  - C per film volume and g(E) = C/(e V) at the energy -E_mid (as in the CV's DOS).

The double layer is not in a PITT dQ: R_s * Cdl is about a millisecond, so its
charge flows before the first sample. A PITT C estimates the film's chemical
capacitance, to be compared with the impedance Cmu, not Cmu + Cdl.

Every number is returned; a step a check objects to carries the reason and the
status `needs_review`. The software raises concerns, the scientist decides.

Not yet here (they wait on a real film): the transmission-line fit of the step
current (a better head, the diffusion time and D), the impedance film models,
and the PITT / impedance / CV cross-check.
"""
import json

import numpy as np

from spec_echem import charge
from spec_echem.pitt import (END_ABORTED, END_ERROR, END_MAX_HOLD, END_STOPPED,
                             ROLE_DEDOPE, ROLE_FORWARD, ROLE_RETURN)

E_CHARGE_C = charge.E_CHARGE_C

DEFAULT_TAIL_S = 5.0
TAIL_FRACTION = 0.2          # a tail longer than the step averages the step itself
GAP_FACTOR = 5.0
TREND_FRACTION = 0.05        # the tail trend carried over the step, against dQ
TREND_SIGMAS = 2.0           # ...and only when the slope is beyond its own noise
HEAD_FRACTION = 0.10
BACKGROUND_FRACTION = 0.20
SETPOINT_OFF_V = 2.0e-3


class PittStepResult:
    """One step's numbers. Every field is filled where it can be, flagged or not."""

    def __init__(self, **kw):
        self.index = kw.get("index")
        self.role = kw.get("role")
        self.potential_set = kw.get("potential_set")
        self.end_reason = kw.get("end_reason")
        self.hold_s = kw.get("hold_s")                # the sampled duration used
        self.n_used = kw.get("n_used", 0)
        self.n_dropped = kw.get("n_dropped", 0)       # after a gap (the sweep)
        self.e = kw.get("e")                          # V, measured, tail mean
        self.e_u = kw.get("e_u")
        self.i_ss = kw.get("i_ss")                    # A
        self.q_raw = kw.get("q_raw")                  # C
        self.dq = kw.get("dq")                        # C, constant background
        self.dq_ramp = kw.get("dq_ramp")              # C, the other bound
        self.u_background = kw.get("u_background")
        self.u_integration = kw.get("u_integration")
        self.u_noise = kw.get("u_noise")
        self.q_u = kw.get("q_u")
        self.q_head = kw.get("q_head")                # C, NOT in dq or q_u
        self.de = kw.get("de")                        # V
        self.de_u = kw.get("de_u")
        self.e_mid = kw.get("e_mid")                  # V
        self.c = kw.get("c")                          # F, sampled dQ / dE
        self.c_head = kw.get("c_head")                # F, with the head
        self.c_u = kw.get("c_u")                      # F, from q_u and de_u
        self.c_chem = kw.get("c_chem")                # F cm^-3
        self.c_chem_head = kw.get("c_chem_head")
        self.g = kw.get("g")                          # states eV^-1 cm^-3
        self.g_head = kw.get("g_head")
        self.r_dc = kw.get("r_dc")                    # ohm, dE / d(I_ss)
        self.sweep = kw.get("sweep")                  # impedance.analyse_sweep, or None
        self.reasons = list(kw.get("reasons", ()))
        self.notes = list(kw.get("notes", ()))        # statements, not concerns

    @property
    def status(self):
        return "needs_review" if self.reasons else "ok"

    @property
    def energy_ev(self):
        return None if self.e_mid is None else -self.e_mid


def hold_window(time, gap_factor=GAP_FACTOR):
    """Mask of the samples before the first gap longer than `gap_factor` times the
    median sample period. All True when there is no such gap."""
    t = np.asarray(time, dtype=float)
    keep = np.ones(t.size, dtype=bool)
    if t.size < 3:
        return keep
    dt = np.diff(t)
    period = float(np.median(dt))
    if period <= 0:
        return keep
    gaps = np.flatnonzero(dt > gap_factor * period)
    if gaps.size:
        keep[gaps[0] + 1:] = False
    return keep


def _tail_mask(t, tail_s):
    length = min(tail_s, TAIL_FRACTION * float(t[-1] - t[0]))
    return t >= t[-1] - length


def film_volume_cm3(area_cm2, thickness_nm):
    if not area_cm2 or not thickness_nm:
        return None
    return float(area_cm2) * float(thickness_nm) * 1e-7


def analyse_pitt_step(row, time, current, potential, prev=None, area_cm2=None,
                      thickness_nm=None, tail_s=DEFAULT_TAIL_S, gap_factor=GAP_FACTOR):
    """The numbers for one step. `row` is the step's entry in `pitt_steps_json`;
    `prev` the PittStepResult of the step before it in run order, or None."""
    t_all = np.asarray(time, dtype=float)
    i_all = np.asarray(current, dtype=float)
    v_all = np.asarray(potential, dtype=float)
    finite = np.isfinite(t_all) & np.isfinite(i_all) & np.isfinite(v_all)
    t_all, i_all, v_all = t_all[finite], i_all[finite], v_all[finite]
    keep = hold_window(t_all, gap_factor)
    t, i, v = t_all[keep], i_all[keep], v_all[keep]

    out = dict(index=int(row["index"]), role=row.get("role"),
               potential_set=row.get("potential_set"),
               end_reason=row.get("end_reason"), n_used=int(t.size),
               n_dropped=int(t_all.size - t.size))
    reasons, notes = [], []
    if t.size < 3:
        reasons.append("fewer than 3 current samples")
        return PittStepResult(reasons=reasons, notes=notes, **out)

    duration = float(t[-1] - t[0])
    tail = _tail_mask(t, tail_s)
    if tail.sum() < 2:
        tail = np.zeros(t.size, dtype=bool)
        tail[-2:] = True
    i_ss = float(np.mean(i[tail]))
    e = float(np.mean(v[tail]))
    e_u = charge.block_sem(v[tail])
    q_raw = charge.integrate(t, i)
    dq = q_raw - i_ss * duration
    base = prev.i_ss if (prev is not None and prev.i_ss is not None) else 0.0
    dq_ramp = q_raw - 0.5 * (base + i_ss) * duration
    u_background = abs(dq - dq_ramp) / 2.0
    u_integration = abs(q_raw - charge.integrate_every_other(t, i)) if t.size >= 3 else 0.0

    # Noise: each sample's weight in dq is its trapezoid weight, less duration/n_tail
    # if the leak mean uses it (charge.analyse_step explains why this, not two terms).
    u_noise, slope, slope_sd = 0.0, None, None
    if tail.sum() >= 4:
        line, cov = np.polyfit(t[tail], i[tail], 1, cov=True)
        slope, slope_sd = float(line[0]), float(np.sqrt(abs(cov[0, 0])))
        sigma = float(np.std(i[tail] - np.polyval(line, t[tail]), ddof=2))
        w = np.zeros(t.size)
        half = np.diff(t) / 2.0
        w[:-1] += half
        w[1:] += half
        w[tail] -= duration / tail.sum()
        u_noise = float(2.0 * sigma * np.sqrt(np.sum(w ** 2)))
    q_u = float(np.sqrt(u_background ** 2 + u_integration ** 2 + u_noise ** 2))

    # A first sample at the setpoint change misses nothing. Otherwise the head is
    # extrapolated; None when it cannot be (too few samples).
    q_head = (charge.head_charge_max(t, i - i_ss, float(t[0])) if t[0] > 0 else 0.0)

    out.update(hold_s=duration, e=e, e_u=e_u, i_ss=i_ss, q_raw=q_raw, dq=dq,
               dq_ramp=dq_ramp, u_background=u_background,
               u_integration=u_integration, u_noise=u_noise, q_u=q_u, q_head=q_head)

    # --- concerns that do not need the step before -------------------------
    reason = row.get("end_reason")
    if reason == END_MAX_HOLD:
        reasons.append("reached the maximum hold: not at equilibrium")
    elif reason in (END_STOPPED, END_ABORTED, END_ERROR):
        reasons.append("cut short (%s): dQ is a lower bound" % reason)
    if row.get("cutoff_unresolved"):
        reasons.append("cutoff below one count of the current range: never judged")
    if row.get("settle_fallback"):
        reasons.append("the potential never settled at the setpoint")
    setpoint = row.get("potential_set")
    if setpoint is not None and abs(e - float(setpoint)) > SETPOINT_OFF_V:
        reasons.append("measured E is %.1f mV from the setpoint"
                       % (1e3 * (e - float(setpoint))))
    if dq and slope is not None and slope_sd is not None:
        effect = abs(slope) * duration ** 2 / 2.0 / abs(dq)
        if effect > TREND_FRACTION and abs(slope) > TREND_SIGMAS * slope_sd:
            reasons.append("current at the end still changing (%+.3g nA/s): over the "
                           "step that is %.0f%% of dQ" % (1e9 * slope, 100 * effect))
    if dq and u_background > BACKGROUND_FRACTION * abs(dq):
        reasons.append("the background is %.0f%% of dQ" % (100 * u_background / abs(dq)))
    if dq and q_head is not None and abs(q_head) > HEAD_FRACTION * abs(dq):
        reasons.append("%.0f%% of the charge is before the first sample (estimated)"
                       % (100 * abs(q_head) / abs(dq)))
    if out["n_dropped"]:
        notes.append("%d samples after the impedance sweep not in dQ" % out["n_dropped"])

    # --- dQ/dE --------------------------------------------------------------
    if row.get("role") == ROLE_DEDOPE:
        notes.append("end dedope: a reset, not part of the staircase; no dQ/dE")
    elif prev is None or prev.e is None:
        notes.append("first step: the potential before it is not known; no dQ/dE")
    else:
        de = e - prev.e
        de_u = (float(np.hypot(e_u or 0.0, prev.e_u or 0.0))
                if (e_u is not None or prev.e_u is not None) else None)
        out.update(de=de, de_u=de_u, e_mid=0.5 * (e + prev.e))
        if de != 0:
            c = dq / de
            rel = (q_u / dq) ** 2 if dq else 0.0
            rel += ((de_u or 0.0) / de) ** 2
            out.update(c=c, c_u=abs(c) * float(np.sqrt(rel)),
                       c_head=(dq + q_head) / de if q_head is not None else None)
            if np.sign(dq) != np.sign(de):
                reasons.append("dQ has the opposite sign to the step")
            if prev.i_ss is not None and i_ss != prev.i_ss:
                out["r_dc"] = de / (i_ss - prev.i_ss)
            volume = film_volume_cm3(area_cm2, thickness_nm)
            if volume:
                out["c_chem"] = c / volume
                out["g"] = c / (E_CHARGE_C * volume)
                if out["c_head"] is not None:
                    out["c_chem_head"] = out["c_head"] / volume
                    out["g_head"] = out["c_head"] / (E_CHARGE_C * volume)
    return PittStepResult(reasons=reasons, notes=notes, **out)


def read_steps_json(path):
    """(steps, completed, error) from a PITT h5's root attributes."""
    import h5py
    with h5py.File(path, "r") as f:
        steps = json.loads(f.attrs["pitt_steps_json"])
        completed = bool(f.attrs.get("pitt_completed", False))
        error = f.attrs.get("pitt_error")
    return steps, completed, (str(error) if error is not None else None)


def analyse_staircase(path, area_cm2=None, thickness_nm=None, tail_s=DEFAULT_TAIL_S,
                      impedance=True):
    """Every step of a PITT h5, in run order. -> list of PittStepResult.

    A step with no group (Abort between its setpoint and its first spectrum) has
    no echem to analyse and is left out; the next step then takes its dQ/dE from
    the step before the missing one, which is still a measured potential.
    """
    from spec_echem.data import read_impedance_h5, read_segment_echem_h5
    from spec_echem.gamry_data import CURRENT_COL, POTENTIAL_COL
    steps, _completed, _error = read_steps_json(path)
    results, prev = [], None
    for row in steps:
        k = int(row["index"])
        frame = read_segment_echem_h5(path, k)
        if frame is None or len(frame) == 0:
            continue
        res = analyse_pitt_step(row, frame["Time (s)"].to_numpy(),
                                frame[CURRENT_COL].to_numpy(),
                                frame[POTENTIAL_COL].to_numpy(), prev=prev,
                                area_cm2=area_cm2, thickness_nm=thickness_nm,
                                tail_s=tail_s)
        if impedance:
            z = read_impedance_h5(path, k)
            if z is not None:
                from spec_echem.impedance import analyse_sweep
                res.sweep = analyse_sweep(z)
                if row.get("sweep_end") not in (None, "complete"):
                    res.reasons.append("impedance sweep ended %s" % row.get("sweep_end"))
        results.append(res)
        prev = res
    return results


def running_charge(results):
    """{role: (E, Q)} for the forward and return legs: Q is the running sum of dQ
    from 0 at the first step (whose own dQ came from wherever the film was before
    the staircase), and the return leg carries on from the forward leg's total."""
    out = {}
    fwd = [r for r in results if r.role == ROLE_FORWARD and r.e is not None]
    if fwd:
        q = np.r_[0.0, np.cumsum([r.dq for r in fwd[1:]])]
        out[ROLE_FORWARD] = (np.array([r.e for r in fwd]), q)
        ret = [r for r in results if r.role == ROLE_RETURN and r.e is not None]
        if ret:
            q_ret = q[-1] + np.cumsum([r.dq for r in ret])
            out[ROLE_RETURN] = (np.array([r.e for r in ret]), q_ret)
    return out


def staircase_hysteresis(results):
    """How far the way back down departs from the way up.

    -> dict: q_gap_fraction (the largest gap between the two Q(E) curves at the
    same setpoint, over the forward total), closure (charge left in the film at the
    end of the return leg, over the forward total; 0 = all of it came back),
    centre_shift_v (Gaussian centre of the return leg's C less the forward's; from
    analysis.fit_gaussian_dos, None when either fit fails). None without a return leg.
    """
    legs = running_charge(results)
    if ROLE_RETURN not in legs:
        return None
    fwd = [r for r in results if r.role == ROLE_FORWARD and r.e is not None]
    ret = [r for r in results if r.role == ROLE_RETURN and r.e is not None]
    q_f = dict(zip((round(r.potential_set, 6) for r in fwd), legs[ROLE_FORWARD][1]))
    q_r = dict(zip((round(r.potential_set, 6) for r in ret), legs[ROLE_RETURN][1]))
    total = legs[ROLE_FORWARD][1][-1]
    shared = sorted(set(q_f) & set(q_r))
    gap = (max(abs(q_f[p] - q_r[p]) for p in shared) / abs(total)
           if shared and total else None)
    closure = legs[ROLE_RETURN][1][-1] / total if total else None
    return {"q_gap_fraction": gap, "closure": closure,
            "centre_shift_v": _centre_shift(fwd, ret)}


def _centre_shift(fwd, ret):
    from spec_echem.analysis import fit_gaussian_dos

    def centre(rows):
        rows = [r for r in rows if r.c is not None and r.e_mid is not None]
        if len(rows) < 5:
            return None
        fit = fit_gaussian_dos([-r.e_mid for r in rows], [r.c for r in rows])
        return -fit["center_ev"] if fit.get("ok") else None

    a, b = centre(fwd), centre(ret)
    return None if a is None or b is None else b - a


def dos_fits(results, use_head=False):
    """{role: analysis.fit_gaussian_dos result} on g(E) (or C without a film size)
    for each leg with at least 5 steps of dQ/dE."""
    from spec_echem.analysis import fit_gaussian_dos
    out = {}
    for role in (ROLE_FORWARD, ROLE_RETURN):
        rows = [r for r in results if r.role == role and r.e_mid is not None]
        ys = []
        for r in rows:
            y = (r.g_head if use_head else r.g) if r.g is not None else \
                (r.c_head if use_head else r.c)
            ys.append(np.nan if y is None else y)
        if sum(np.isfinite(ys)) >= 5:
            out[role] = fit_gaussian_dos([-r.e_mid for r in rows], ys)
    return out


TABLE_COLUMNS = [
    ("step", ""), ("role", ""), ("E_set_V", "V"), ("E_V", "V"), ("dE_mV", "mV"),
    ("E_mid_V", "V"), ("hold_s", "s"), ("end", ""), ("I_ss_A", "A"),
    ("dQ_C", "C"), ("dQ_u_C", "C"), ("u_background_C", "C"),
    ("u_integration_C", "C"), ("u_noise_C", "C"), ("dQ_ramp_C", "C"),
    ("Q_head_C", "C"), ("C_F", "F"), ("C_u_F", "F"), ("C_head_F", "F"),
    ("C_chem_F_cm3", "F cm-3"), ("C_chem_head_F_cm3", "F cm-3"),
    ("g_per_eV_cm3", "eV-1 cm-3"), ("g_head_per_eV_cm3", "eV-1 cm-3"),
    ("R_dc_ohm", "ohm"), ("Rs_ohm", "ohm"), ("Cdl_F", "F"), ("C_lf_F", "F"),
    ("C_lf_reached", ""), ("status", ""), ("reasons", ""), ("Z_concerns", ""),
    ("notes", ""),
]


def pitt_table_rows(results):
    """One dict per step, keys as TABLE_COLUMNS. A flagged row carries its reasons;
    a missing value is None. `status` and `reasons` are the step's charge; an
    impedance sweep's concerns are in `Z_concerns`, since they say nothing about dQ."""
    rows = []
    for r in results:
        sweep = r.sweep or {}
        low, high = sweep.get("low") or {}, sweep.get("high") or {}
        z_concerns = list(sweep.get("concerns") or []) + list(low.get("concerns") or [])
        rows.append({
            "step": r.index, "role": r.role, "E_set_V": r.potential_set, "E_V": r.e,
            "dE_mV": None if r.de is None else 1e3 * r.de, "E_mid_V": r.e_mid,
            "hold_s": r.hold_s, "end": r.end_reason, "I_ss_A": r.i_ss,
            "dQ_C": r.dq, "dQ_u_C": r.q_u, "u_background_C": r.u_background,
            "u_integration_C": r.u_integration, "u_noise_C": r.u_noise,
            "dQ_ramp_C": r.dq_ramp, "Q_head_C": r.q_head, "C_F": r.c,
            "C_u_F": r.c_u, "C_head_F": r.c_head, "C_chem_F_cm3": r.c_chem,
            "C_chem_head_F_cm3": r.c_chem_head, "g_per_eV_cm3": r.g,
            "g_head_per_eV_cm3": r.g_head, "R_dc_ohm": r.r_dc,
            "Rs_ohm": sweep.get("rs"), "Cdl_F": high.get("cdl"),
            "C_lf_F": low.get("c_lf"),
            "C_lf_reached": low.get("reached") if low else None,
            "status": r.status, "reasons": "; ".join(r.reasons),
            "Z_concerns": "; ".join(z_concerns), "notes": "; ".join(r.notes),
        })
    return rows
