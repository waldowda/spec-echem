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
    `drift_fraction` of the step's whole change;
  - the current at the end is not still changing so fast that, over the step, the
    change would be more than `background_fraction` of the charge.

Uncertainties (added 2026-11-08). Each step's charge carries:
  - the background model: the steady current at the end of the step (the "leak")
    may have flowed for the whole step, or grown from zero. The corrected charge is
    the midpoint of the two and half their difference is its uncertainty. On a low
    step this dominates everything else (+-6% at the lowest potential of one run);
  - the integration check: every sample against every other sample;
  - noise: the scatter of the last `tail_s` seconds, on the integral and on the
    background mean (2 sigma).
They combine in quadrature. Half-ranges are bounds, not standard deviations, so the
result is a rough 95% range, not a formal confidence interval.

Kept OUT of that number, as separate values, because they move every step the same
way and an error bar would hide what they are:
  - the film size (one +-% for every density in a run);
  - the charge before the first current sample (`head_charge_max`), an upper bound.
"""
import re

import numpy as np

E_CHARGE_C = 1.602176634e-19

DEFAULT_TAIL_S = 5.0
DEFAULT_DRIFT_FRACTION = 0.02
DEFAULT_TAU_MULTIPLE = 5.0
DEFAULT_BACKGROUND_FRACTION = 0.05


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


def integrate_every_other(time, current):
    """The same integral from every other sample, ending on the last one. Its
    difference from integrate() is the integration check: large when the fast first
    second of a step is undersampled."""
    t = np.asarray(time, dtype=float)
    i = np.asarray(current, dtype=float)
    idx = np.arange(0, t.size, 2)
    if t.size and idx[-1] != t.size - 1:
        idx = np.append(idx, t.size - 1)
    return integrate(t[idx], i[idx])


def block_sem(values, n_blocks=5):
    """Standard error of a mean whose neighbouring samples are not independent.

    Spectra drift slowly (lamp, baseline), so the plain std / sqrt(n) over 50 samples
    is too small. The samples are cut into `n_blocks` runs and the scatter of the
    run means is used instead. None with fewer than two finite values."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return None
    n = min(n_blocks, v.size // 2)
    if n < 2:
        return float(np.std(v, ddof=1) / np.sqrt(v.size))
    means = [b.mean() for b in np.array_split(v, n)]
    return float(np.std(means, ddof=1) / np.sqrt(n))


def head_charge_max(time, current, delay_s, n_points=3):
    """Upper estimate of the charge that flowed before the first current sample.

    The cell switches on `delay_s` before the first sample, while the current is at
    its largest, and no sample covers that time. An exponential through the first
    `n_points` samples is run back over it. The real current just after switch-on is
    capped by the cell resistance, so this over-estimates: an upper bound. If the
    first samples are not a decay, the first sample times `delay_s` is used.
    None without a delay (no timing for that step in the run log).
    """
    t = np.asarray(time, dtype=float)
    i = np.asarray(current, dtype=float)
    if not delay_s or delay_s <= 0 or t.size < n_points:
        return None
    i0 = float(i[0])
    head = i[:n_points]
    if np.all(np.sign(head) == np.sign(i0)) and i0 != 0:
        k = np.polyfit(t[:n_points] - t[0], np.log(np.abs(head)), 1)[0]
        if k < 0:
            tau0 = -1.0 / k
            return i0 * tau0 * (np.exp(delay_s / tau0) - 1.0)
    return i0 * delay_s


def two_sig_fig_u(value):
    """Half a unit in the second significant figure: 100 -> 5, 2.4 -> 0.05. The
    uncertainty of a number known to two significant figures."""
    if not value or value <= 0:
        return 0.0
    return 0.5 * 10.0 ** (np.floor(np.log10(float(value))) - 1)


def film_rel_u(thickness_nm, area_cm2, u_thickness_nm=0.0, u_area_cm2=0.0):
    """Relative uncertainty of the film volume, (u_d/d) and (u_A/A) in quadrature.
    An uncertainty of 0 means "two significant figures". None without a film size."""
    if not thickness_nm or not area_cm2:
        return None
    u_d = u_thickness_nm or two_sig_fig_u(thickness_nm)
    u_a = u_area_cm2 or two_sig_fig_u(area_cm2)
    return float(np.hypot(u_d / thickness_nm, u_a / area_cm2))


def first_sample_delays(log_text):
    """{segment label: seconds from cell ON to the first current sample}, from a run
    log's timing lines. Only the Autolab Ei path logs this; other runs give {}."""
    out = {}
    # "<date> <time>  INFO    Doping 0 timing, from cell ON: ... first Ei sample +0.095 s."
    pattern = re.compile(r"\b[A-Z]+\s{2,}(.+?) timing, from cell ON:.*?"
                         r"first Ei sample \+([0-9.]+) s")
    for line in (log_text or "").splitlines():
        m = pattern.search(line)
        if m:
            out[m.group(1).strip()] = float(m.group(2))
    return out


def carrier_density(charge_c, area_cm2, thickness_nm):
    """|Q| / (e * area * thickness), in carriers per cm^3; None without a film size."""
    if not area_cm2 or not thickness_nm or charge_c is None:
        return None
    volume_cm3 = float(area_cm2) * float(thickness_nm) * 1e-7
    return abs(float(charge_c)) / (E_CHARGE_C * volume_cm3)


def charge_density(charge_c, area_cm2, thickness_nm):
    """|Q| / (area * thickness), in C per cm^3; None without a film size. The same
    number as carrier_density times e, in the units an electrochemist reports."""
    if not area_cm2 or not thickness_nm or charge_c is None:
        return None
    return abs(float(charge_c)) / (float(area_cm2) * float(thickness_nm) * 1e-7)


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
                 charge_density_raw=None, charge_density_corrected=None,
                 a_end=None, a0=None, bleach=None, at_equilibrium=True,
                 reasons=(), checked=(), q_u=None, u_background=None,
                 u_integration=None, u_noise=None, q_head_max=None, carriers_u=None,
                 charge_density_u=None, bleach_u=None):
        self.label = label
        self.potential = potential
        self.direction = direction
        self.duration = duration
        self.q_raw = q_raw
        self.q_corrected = q_corrected
        self.leak_current = leak_current
        self.carriers_raw = carriers_raw
        self.carriers_corrected = carriers_corrected
        self.charge_density_raw = charge_density_raw
        self.charge_density_corrected = charge_density_corrected
        self.a_end = a_end
        self.a0 = a0
        self.bleach = bleach
        self.at_equilibrium = at_equilibrium
        self.reasons = list(reasons)
        self.checked = list(checked)     # which checks could be made at all
        self.q_u = q_u                   # combined uncertainty of q_corrected
        self.u_background = u_background
        self.u_integration = u_integration
        self.u_noise = u_noise
        self.q_head_max = q_head_max     # before the first sample; NOT in q
        self.carriers_u = carriers_u     # from q_u only; film size is separate
        self.charge_density_u = charge_density_u
        self.bleach_u = bleach_u

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
                 tau_multiple=DEFAULT_TAU_MULTIPLE, a0_u=None, head_delay_s=None,
                 background_fraction=DEFAULT_BACKGROUND_FRACTION):
    """Charge, density, bleach and the equilibrium checks for one step.

    `mean_taus` is {trace: mean tau} from the Kinetics fits of this step; empty when
    it has not been fitted, and then only the drift check is made.

    Leakage: the mean current over the last `tail_s` seconds is taken as a side
    reaction. Whether it ran at that level for the whole step or grew from zero is
    not known, so the corrected charge is the midpoint of the two and half their
    difference goes into its uncertainty. The raw charge is kept beside it.

    `a0_u` is the uncertainty of `a0`; `head_delay_s` the time from cell ON to the
    first current sample (from the run log), for `head_charge_max`.
    """
    t = np.asarray(time, dtype=float)
    i = np.asarray(current, dtype=float)
    duration = float(t[-1] - t[0]) if t.size else 0.0
    q_raw = integrate(t, i)
    leak = tail_mean(t, i, tail_s) if t.size else 0.0
    # Constant leak removes leak * duration; one grown linearly from zero, half that.
    q_corrected = q_raw - 0.75 * leak * duration
    u_background = abs(leak) * duration / 4.0
    u_integration = abs(q_raw - integrate_every_other(t, i)) if t.size >= 3 else 0.0
    u_noise = 0.0
    tail_slope = None
    keep = _tail(t, tail_s) & np.isfinite(i) if t.size else np.zeros(0, bool)
    if keep.sum() >= 3:
        line = np.polyfit(t[keep], i[keep], 1)
        tail_slope = float(line[0])
        sigma = float(np.std(i[keep] - np.polyval(line, t[keep]), ddof=2))
        dt = float(np.median(np.diff(t)))
        on_integral = 2.0 * sigma * dt * np.sqrt(t.size)
        on_leak = 2.0 * sigma / np.sqrt(keep.sum()) * 0.75 * duration
        u_noise = float(np.hypot(on_integral, on_leak))
    q_u = float(np.sqrt(u_background ** 2 + u_integration ** 2 + u_noise ** 2))

    reasons, checked = [], []
    taus = {k: v for k, v in dict(mean_taus).items() if v is not None and np.isfinite(v)}
    if taus:
        checked.append(f"{tau_multiple:g} tau")
        for trace, tau in sorted(taus.items()):
            if duration < tau_multiple * tau:
                reasons.append(f"step {duration:.3g} s is under {tau_multiple:g} x "
                               f"{trace} tau ({tau:.3g} s)")

    # The current at the end still changing: carried back over the whole step at
    # that rate, how much charge would that be? Judged against Q, not the peak
    # current, which a 300 uA spike makes blind to a 1 uA background still moving.
    if tail_slope is not None and q_corrected:
        checked.append("tail current trend")
        effect = abs(tail_slope) * duration ** 2 / 2.0 / abs(q_corrected)
        if effect > background_fraction:
            reasons.append(f"current at the end still changing "
                           f"({1e9 * tail_slope:+.3g} nA/s): over the step that is "
                           f"{100 * effect:.0f}% of Q")

    # Current: from its largest excursion to where it ended up.
    if t.size >= 3:
        swing_i = float(np.nanmax(np.abs(i - leak)))
        drift_i = tail_drift(t, i, swing_i, tail_s)
        if drift_i is not None:
            checked.append("current drift")
            if drift_i > drift_fraction:
                reasons.append(f"current still moving: {100 * drift_i:.1f}% of its "
                               f"change in the last {tail_s:g} s")

    a_end = bleach = bleach_u = None
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
        sem = block_sem(a[_tail(ta, tail_s)])
        if bleach is not None and sem is not None:
            bleach_u = float(np.hypot(2.0 * sem / a0,
                                      a_end * (a0_u or 0.0) / a0 ** 2))

    return StepCharge(
        label, potential, direction, duration, q_raw, q_corrected, leak,
        carriers_raw=carrier_density(q_raw, area_cm2, thickness_nm),
        carriers_corrected=carrier_density(q_corrected, area_cm2, thickness_nm),
        charge_density_raw=charge_density(q_raw, area_cm2, thickness_nm),
        charge_density_corrected=charge_density(q_corrected, area_cm2, thickness_nm),
        a_end=a_end, a0=a0, bleach=bleach, at_equilibrium=not reasons,
        reasons=reasons, checked=checked, q_u=q_u, u_background=u_background,
        u_integration=u_integration, u_noise=u_noise,
        q_head_max=head_charge_max(t, i, head_delay_s),
        carriers_u=carrier_density(q_u, area_cm2, thickness_nm),
        charge_density_u=charge_density(q_u, area_cm2, thickness_nm),
        bleach_u=bleach_u)


def reversibility(doping, dedoping):
    """|Q out| / |Q in| for a doping step and the dedoping step after it, on the
    leak-corrected charge. 1 is fully reversible; under 1, charge stayed in the film
    (or went to a side reaction). None when either is missing."""
    if doping is None or dedoping is None or not doping.q_corrected:
        return None
    return abs(dedoping.q_corrected) / abs(doping.q_corrected)


def reversibility_u(doping, dedoping):
    """Uncertainty of reversibility() from the two charges' q_u, in quadrature.
    The head charge is not in it: it is larger on dedoping (faster decay), so the
    ratio reads low by roughly the difference of the two heads' shares."""
    r = reversibility(doping, dedoping)
    if r is None or not dedoping.q_corrected or doping.q_u is None \
            or dedoping.q_u is None:
        return None
    return r * float(np.hypot(doping.q_u / doping.q_corrected,
                              dedoping.q_u / dedoping.q_corrected))
