"""
Charge, carrier density and bleach for each potential step, with a check that the
step reached equilibrium.

Asked for 2026-11-06, beside the kinetics ladder: where the ladder says how FAST a
film switches at each potential, this says how MUCH -- the charge put in, the carrier
density that makes, and the fraction of the neutral absorption it bleached.

Each doping step in the usual sequence starts from the dedoped state, so a doping
step's charge is already the total charge to reach that potential; nothing is
summed across steps. A dedoping step's charge is the charge taken back out.

The numbers below are only equilibrium values if the step got there. Two checks,
and a step that fails either is FLAGGED, never dropped -- the software raises the
concern and the scientist decides:
  - the step lasted at least `tau_multiple` relaxation times (5 tau = 99.3%), from
    the Kinetics fits when there are any;
  - over the last `tail_s` seconds the absorbance and current moved by less than
    `drift_fraction` of the step's whole change.
"""
import numpy as np

E_CHARGE_C = 1.602176634e-19

DEFAULT_TAIL_S = 5.0
DEFAULT_DRIFT_FRACTION = 0.02
DEFAULT_TAU_MULTIPLE = 5.0


def integrate(time, current):
    """Trapezoid integral of current over time, in coulombs."""
    t = np.asarray(time, dtype=float)
    i = np.asarray(current, dtype=float)
    if t.size < 2:
        return 0.0
    return float(np.sum(np.diff(t) * (i[1:] + i[:-1]) / 2.0))


def _tail(time, tail_s):
    t = np.asarray(time, dtype=float)
    return t >= t[-1] - tail_s


def tail_mean(time, values, tail_s=DEFAULT_TAIL_S):
    """Mean over the last `tail_s` seconds: the step's end value, averaged so one
    noisy sample does not set it."""
    v = np.asarray(values, dtype=float)
    return float(np.nanmean(v[_tail(time, tail_s)]))


def tail_drift(time, values, swing, tail_s=DEFAULT_TAIL_S):
    """How far `values` still moved over the last `tail_s` seconds, as a fraction of
    `swing` (the step's whole change). From a straight-line fit rather than end
    minus start, so the noise on two single samples does not decide it.

    None when it cannot be judged: too few points, or no swing to compare with.
    """
    t = np.asarray(time, dtype=float)
    v = np.asarray(values, dtype=float)
    keep = _tail(t, tail_s) & np.isfinite(v)
    if keep.sum() < 3 or not swing or not np.isfinite(swing):
        return None
    slope = np.polyfit(t[keep], v[keep], 1)[0]
    span = float(t[keep][-1] - t[keep][0])
    return float(abs(slope * span) / abs(swing))


def carrier_density(charge_c, area_cm2, thickness_nm):
    """|Q| / (e * area * thickness), in carriers per cm^3; None without a film size."""
    if not area_cm2 or not thickness_nm or charge_c is None:
        return None
    volume_cm3 = float(area_cm2) * float(thickness_nm) * 1e-7
    return abs(float(charge_c)) / (E_CHARGE_C * volume_cm3)


def bleach_fraction(a_end, a0):
    """(A0 - A) / A0: the fraction of the neutral absorbance lost. Positive for a
    bleach, negative where the absorbance GREW (a polaron wavelength)."""
    if a0 is None or a_end is None or not np.isfinite(a0) or a0 == 0:
        return None
    return float((a0 - a_end) / a0)


class StepCharge:
    """One step's numbers. Every field is filled where it can be, flagged or not."""

    def __init__(self, label, potential, direction, duration, q_raw, q_corrected,
                 leak_current, carriers_raw=None, carriers_corrected=None,
                 a_end=None, a0=None, bleach=None, at_equilibrium=True,
                 reasons=(), checked=()):
        self.label = label
        self.potential = potential
        self.direction = direction
        self.duration = duration
        self.q_raw = q_raw
        self.q_corrected = q_corrected
        self.leak_current = leak_current
        self.carriers_raw = carriers_raw
        self.carriers_corrected = carriers_corrected
        self.a_end = a_end
        self.a0 = a0
        self.bleach = bleach
        self.at_equilibrium = at_equilibrium
        self.reasons = list(reasons)
        self.checked = list(checked)     # which checks could be made at all

    @property
    def reason(self):
        if self.reasons:
            return "; ".join(self.reasons)
        if not self.checked:
            return "not checked (no data to check)"
        return "at equilibrium (" + ", ".join(self.checked) + ")"


def analyse_step(label, potential, direction, time, current, absorbance_time=None,
                 absorbance=None, a0=None, area_cm2=None, thickness_nm=None,
                 mean_taus=(), tail_s=DEFAULT_TAIL_S,
                 drift_fraction=DEFAULT_DRIFT_FRACTION,
                 tau_multiple=DEFAULT_TAU_MULTIPLE):
    """Charge, density, bleach and the equilibrium checks for one step.

    `mean_taus` is {trace: mean tau} from the Kinetics fits of this step; empty when
    it has not been fitted, and then only the drift check is made.

    Leakage: the mean current over the last `tail_s` seconds is taken as a steady
    side reaction and subtracted over the whole step. Both values are kept, so the
    size of the correction is visible rather than assumed.
    """
    t = np.asarray(time, dtype=float)
    i = np.asarray(current, dtype=float)
    duration = float(t[-1] - t[0]) if t.size else 0.0
    q_raw = integrate(t, i)
    leak = tail_mean(t, i, tail_s) if t.size else 0.0
    q_corrected = q_raw - leak * duration

    reasons, checked = [], []
    taus = {k: v for k, v in dict(mean_taus).items() if v is not None and np.isfinite(v)}
    if taus:
        checked.append(f"{tau_multiple:g} tau")
        for trace, tau in sorted(taus.items()):
            if duration < tau_multiple * tau:
                reasons.append(f"step {duration:.3g} s is under {tau_multiple:g} x "
                               f"{trace} tau ({tau:.3g} s)")

    # Current: from its largest excursion to where it ended up.
    if t.size >= 3:
        swing_i = float(np.nanmax(np.abs(i - leak)))
        drift_i = tail_drift(t, i, swing_i, tail_s)
        if drift_i is not None:
            checked.append("current drift")
            if drift_i > drift_fraction:
                reasons.append(f"current still moving: {100 * drift_i:.1f}% of its "
                               f"change in the last {tail_s:g} s")

    a_end = bleach = None
    if absorbance is not None and absorbance_time is not None and len(absorbance) >= 3:
        ta = np.asarray(absorbance_time, dtype=float)
        a = np.asarray(absorbance, dtype=float)
        a_end = tail_mean(ta, a, tail_s)
        swing_a = a_end - float(np.nanmean(a[:3]))
        drift_a = tail_drift(ta, a, swing_a, tail_s)
        if drift_a is not None:
            checked.append("absorbance drift")
            if drift_a > drift_fraction:
                reasons.append(f"absorbance still moving: {100 * drift_a:.1f}% of its "
                               f"change in the last {tail_s:g} s")
        bleach = bleach_fraction(a_end, a0)

    return StepCharge(
        label, potential, direction, duration, q_raw, q_corrected, leak,
        carriers_raw=carrier_density(q_raw, area_cm2, thickness_nm),
        carriers_corrected=carrier_density(q_corrected, area_cm2, thickness_nm),
        a_end=a_end, a0=a0, bleach=bleach, at_equilibrium=not reasons,
        reasons=reasons, checked=checked)


def reversibility(doping, dedoping):
    """|Q out| / |Q in| for a doping step and the dedoping step after it, on the
    leak-corrected charge. 1 is fully reversible; under 1, charge stayed in the film
    (or went to a side reaction). None when either is missing."""
    if doping is None or dedoping is None or not doping.q_corrected:
        return None
    return abs(dedoping.q_corrected) / abs(doping.q_corrected)
