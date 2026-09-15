"""
Analysis maths for the GUI's Results and Analysis tabs.

Design: docs/analysis-design.md.

No Qt. No hardware. No plotting. Everything here takes arrays and returns numbers, so
it can be tested against synthetic data where the answer is known — which matters
because a fit that is quietly wrong looks exactly like a fit that is right.
"""
import logging

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import gamma

logger = logging.getLogger(__name__)

# A fit whose parameter uncertainty is this fraction of the parameter is not a
# measurement. curve_fit does not raise on a bad fit — it returns a huge covariance —
# so without a check the table would show a confident-looking number.
FIT_SD_REJECT_FRACTION = 0.5


# --- the models -------------------------------------------------------------
# Same three Raj's banded_fits offers, so a fit done here and one done in Jupyter
# mean the same thing.

def model_exp(t, a, b, tau):
    """A + B·exp(−t/τ)"""
    return a + b * np.exp(-t / tau)


def model_biexp(t, a, b1, tau1, b2, tau2):
    """A + B₁·exp(−t/τ₁) + B₂·exp(−t/τ₂) — a fast component and a slow one, which on a
    potential step is capacitance plus ion motion."""
    return a + b1 * np.exp(-t / tau1) + b2 * np.exp(-t / tau2)


def model_stretched(t, a, b, tau, beta):
    """A + B·exp(−(t/τ)^β)"""
    return a + b * np.exp(-((t / tau) ** beta))


MODELS = {
    "exp": (model_exp, ("A", "B", "tau")),
    "biexp": (model_biexp, ("A", "B1", "tau1", "B2", "tau2")),
    "stretched": (model_stretched, ("A", "B", "tau", "beta")),
}

# A time constant is positive by definition, and a stretched exponential requires
# 0 < beta <= 1 -- above 1 it is a COMPRESSED exponential, a different physical
# claim that this model is not offering. Bounding them keeps the optimizer out of
# regions that were only ever rejected after the fact.
#
# BETA_MIN is not 0: <tau> = (tau/beta)*gamma(1/beta), and gamma overflows past
# 1/beta ~ 170. 0.05 is far below any physically meaningful stretch (polymer work
# lives around 0.3-0.9) while keeping the mean relaxation time computable.
TAU_MIN = 1e-9
BETA_MIN = 0.05
BETA_MAX = 1.0

_INF = np.inf
MODEL_BOUNDS = {
    #             A      B       tau
    "exp": ([-_INF, -_INF, TAU_MIN],
            [_INF, _INF, _INF]),
    #             A      B1      tau1     B2      tau2
    "biexp": ([-_INF, -_INF, TAU_MIN, -_INF, TAU_MIN],
              [_INF, _INF, _INF, _INF, _INF]),
    #             A      B       tau      beta
    "stretched": ([-_INF, -_INF, TAU_MIN, BETA_MIN],
                  [_INF, _INF, _INF, BETA_MAX]),
}


def mean_relaxation_time(model, params):
    """⟨τ⟩ — the physically comparable timescale, which is NOT the raw τ.

    For a stretched exponential τ alone is not the relaxation time, so a ratio of raw
    τ between two datasets would be wrong in a way that looks entirely plausible. The
    ratio view must use this.

        exp        <τ> = τ
        biexp      <τ> = (B₁τ₁ + B₂τ₂) / (B₁ + B₂)     amplitude-weighted
        stretched  <τ> = (τ/β)·Γ(1/β)
    """
    if model == "exp":
        return float(params[2])
    if model == "biexp":
        _, b1, tau1, b2, tau2 = params
        denom = b1 + b2
        if denom == 0:
            return float("nan")
        return float((b1 * tau1 + b2 * tau2) / denom)
    if model == "stretched":
        _, _, tau, beta = params
        if beta <= 0:
            return float("nan")
        return float((tau / beta) * gamma(1.0 / beta))
    raise ValueError(f"unknown model {model!r}")


# --- wavelength selection ----------------------------------------------------

# Below this the lamp and optics deliver nothing on these rigs -- MEASURED 416 counts
# at 381 nm on 20260709_P3HT_01, against 41250 at 780 nm. Dean: "there should be no
# data below 410 nm or so." Pixels outside the window never win the selection.
ANALYSIS_WL_MIN = 410.0
ANALYSIS_WL_MAX = None


def auto_wavelengths(absorbance, wavelengths, early=0, late=-1,
                     wl_min=ANALYSIS_WL_MIN, wl_max=ANALYSIS_WL_MAX):
    """(λ_grows, λ_bleaches) from the SIGNED change in absorbance.

    NOTE the return is "grows, bleaches", NOT "polaron, pi". Which band is which
    depends on the segment: DOPING grows the polaron and bleaches pi-pi*, while
    DEDOPING does the reverse -- the polaron decays and pi-pi* recovers. Callers
    must map by data type; see gui/tabs/analysis_tab.py::_probe_wavelength.

    On doping, π→π* BLEACHES while the polaron band GROWS. Taking |ΔA| would return
    whichever is larger — often the bleach — and silently hand back the π band when the
    caller believed they were watching the polaron. The signed difference separates
    them, and costs one subtraction:

        ΔA = A(late) − A(early)
        λ_polaron = argmax(ΔA)        the growth
        λ_π       = argmin(ΔA)        the bleach

    `absorbance` is (n_wavelengths, n_times) — the shape compute_absorbance returns.
    """
    a = np.asarray(absorbance, dtype=float)
    wl = np.asarray(wavelengths, dtype=float)
    if a.ndim != 2 or a.shape[0] != len(wl):
        raise ValueError(
            f"absorbance must be (n_wavelengths, n_times) matching {len(wl)} "
            f"wavelengths; got {a.shape}")
    if a.shape[1] < 2:
        return None, None

    delta = a[:, late] - a[:, early]
    if not np.any(np.isfinite(delta)):
        return None, None
    delta = np.where(np.isfinite(delta), delta, 0.0)

    # Outside the usable optical window nothing is a measurement, whatever its SNR.
    keep = np.ones(len(wl), dtype=bool)
    if wl_min is not None:
        keep &= wl >= wl_min
    if wl_max is not None:
        keep &= wl <= wl_max

    # Reject pixels whose change is not significant against their own noise, or the
    # blue edge wins on noise alone. MEASURED on 20260709_P3HT_01: at 381 nm the lamp
    # delivers 416 counts -- the dark floor -- and absorbance there swings +0.143
    # with SNR 1.8, beating the real polaron band at 780 nm (dA +0.093, SNR 682).
    # Raw argmax picked 381 nm for every segment of that run.
    #
    # Rejecting rather than dividing by the noise keeps clean data behaving exactly
    # as before: when every pixel is significant, this is still argmax of dA.
    significant = _significant(a, delta)
    if significant is not None:
        keep &= significant
    if not keep.any():
        keep = np.ones(len(wl), dtype=bool)

    # Excluded pixels go to -inf for the max and +inf for the min. Zeroing them
    # instead would let a rejected pixel WIN whenever every surviving delta has the
    # same sign -- 0.0 beats any positive value at argmin.
    grows = np.where(keep, delta, -np.inf)
    bleaches = np.where(keep, delta, np.inf)
    return float(wl[int(np.argmax(grows))]), float(wl[int(np.argmin(bleaches))])


# A pixel whose change is under this many times its own noise is not a measurement
# of anything. 1100 nm on the P3HT run scores 7.8 -- silicon running out of quantum
# efficiency -- while the real bands score in the hundreds.
WAVELENGTH_SNR_MIN = 10.0


def _significant(a, delta):
    """Boolean mask of pixels whose |dA| clears WAVELENGTH_SNR_MIN times their noise.

    Noise is the SD of the SECOND difference along time over sqrt(6) -- the standard
    trend-free estimator. The FIRST difference would measure how fast the signal is
    CHANGING rather than how noisy it is, and on smooth data that is mostly signal.

    None when it cannot be estimated (fewer than 3 time points), so the caller keeps
    every pixel rather than discarding the lot.
    """
    if a.shape[1] < 3:
        return None
    with np.errstate(invalid="ignore"):
        noise = np.nanstd(np.diff(a, 2, axis=1), axis=1) / np.sqrt(6.0)
    finite = np.isfinite(noise) & (noise > 0)
    if not finite.any():
        return None
    snr = np.divide(np.abs(delta), noise, out=np.full_like(delta, np.inf),
                    where=finite)
    return snr >= WAVELENGTH_SNR_MIN


# --- fitting -----------------------------------------------------------------

def default_fit_start(time, current):
    """The time of peak |current| — where the capacitive spike ends.

    A potential step draws a large capacitive transient before ion kinetics dominate
    (625 µA against a 30 µA settled value, 2026-09-11), and fitting through it wrecks a
    single exponential. This is computed rather than guessed, and anchors the optical
    and electrical fits to the same instant.
    """
    t = np.asarray(time, dtype=float)
    i = np.abs(np.asarray(current, dtype=float))
    if not len(t) or len(t) != len(i) or not np.any(np.isfinite(i)):
        return None
    return float(t[int(np.nanargmax(i))])


class FitResult:
    """One fit. `ok` is False when the fit did not converge or its uncertainty makes
    the number meaningless — the caller shows the reason rather than a plausible
    wrong value."""

    def __init__(self, model, params=None, sd=None, ok=False, reason="", n=0,
                 t0=0.0, t_first=None, t_last=None):
        self.model = model
        self.params = params
        self.sd = sd
        self.ok = ok
        self.reason = reason
        self.n = n
        # The fit is done against elapsed time from the window start, so t0 is
        # needed to put the curve back on the segment's own time axis. Without it
        # a plotted fit would be offset from its data and look wrong.
        self.t0 = t0
        self.t_first = t_first    # first/last time actually fitted, for shading
        self.t_last = t_last      # the window on a plot of the whole trace

    def curve(self, time):
        """The fitted model evaluated at absolute segment times, for plotting over
        the data. NaN outside the fitted window — the fit makes no claim there, and
        extrapolating a decay backwards through the capacitive spike would draw a
        confident line through data it never saw."""
        if not self.ok:
            return None
        t = np.asarray(time, dtype=float)
        func, _names = MODELS[self.model]
        y = func(t - self.t0, *self.params)
        if self.t_first is not None:
            y = np.where((t >= self.t_first) & (t <= self.t_last), y, np.nan)
        return y

    @property
    def tau(self):
        """The raw τ the user tunes against. For biexp this is the SLOWER component;
        ⟨τ⟩ is what the ratio view uses."""
        if not self.ok:
            return None
        if self.model == "biexp":
            return float(max(self.params[2], self.params[4]))
        return float(self.params[2])

    @property
    def beta(self):
        return float(self.params[3]) if self.ok and self.model == "stretched" else None

    @property
    def tau_sd(self):
        if not self.ok:
            return None
        if self.model == "biexp":
            return float(self.sd[2] if self.params[2] >= self.params[4] else self.sd[4])
        return float(self.sd[2])

    @property
    def mean_tau(self):
        return mean_relaxation_time(self.model, self.params) if self.ok else None

    def __repr__(self):
        if not self.ok:
            return f"<FitResult {self.model} FAILED: {self.reason}>"
        return f"<FitResult {self.model} tau={self.tau:.4g} <tau>={self.mean_tau:.4g}>"


def _clip_to_bounds(p0, lower, upper):
    """curve_fit raises if the starting point sits outside the bounds. Nudged just
    inside rather than onto the edge: a parameter pinned exactly at its bound has no
    room to move and the fit stalls there."""
    p0 = np.asarray(p0, dtype=float)
    lo, hi = np.asarray(lower, float), np.asarray(upper, float)
    inset = np.where(np.isfinite(lo) & np.isfinite(hi), (hi - lo) * 1e-6, 0.0)
    return np.clip(p0, lo + inset, hi - inset)


def _initial_guess(model, t, y):
    """Starting parameters. curve_fit's default of all-ones does not converge on data
    whose timescale is seconds and whose amplitude is microamps."""
    span = float(t[-1] - t[0]) or 1.0
    a0, b0 = float(y[-1]), float(y[0] - y[-1])
    if model == "exp":
        return [a0, b0, span / 3]
    if model == "biexp":
        return [a0, b0 / 2, span / 20, b0 / 2, span / 3]
    return [a0, b0, span / 3, 0.8]


def fit_transient(time, values, model="exp", t_start=None, t_stop=None):
    """Fit one transient over a window. Never raises — a bad fit comes back `ok=False`.

    Fitting is on a button rather than automatic, so a failure here is a normal outcome
    the user reacts to by changing the model or the window.
    """
    if model not in MODELS:
        raise ValueError(f"model must be one of {sorted(MODELS)}; got {model!r}")
    func, _names = MODELS[model]

    t = np.asarray(time, dtype=float)
    y = np.asarray(values, dtype=float)
    keep = np.isfinite(t) & np.isfinite(y)
    if t_start is not None:
        keep &= t >= t_start
    if t_stop is not None:
        keep &= t <= t_stop
    t, y = t[keep], y[keep]

    n_params = len(MODELS[model][1])
    if len(t) <= n_params:
        return FitResult(model, reason=f"only {len(t)} points in the window "
                                       f"for a {n_params}-parameter fit", n=len(t))

    # Fit against elapsed time so the models' exp(-t/tau) is anchored at the window
    # start rather than at the segment's zero.
    t0 = t[0]
    try:
        # The least-squares SEARCH legitimately probes nonsense parameters on its way
        # to the answer: tau -> 0 (divide by zero), tau < 0 under a fractional beta
        # ((-x)**beta -> nan), a tiny tau1 (exp overflow). numpy warns on each, and
        # those warnings were reaching the user's shell on every fit -- three of them
        # on the first launch at PLU -- which reads like a malfunction when it is the
        # optimizer doing its job. What matters is the OUTCOME, and that is validated
        # below: a fit that ends up in one of those regions is rejected by the tau
        # and uncertainty checks, not by whether a trial step warned.
        #
        # Scoped to this call rather than set module-wide, so a genuine numerical
        # fault anywhere else still surfaces.
        lower, upper = MODEL_BOUNDS[model]
        p0 = _clip_to_bounds(_initial_guess(model, t - t0, y), lower, upper)
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            popt, pcov = curve_fit(func, t - t0, y, p0=p0,
                                   bounds=(lower, upper), maxfev=10000)
    except Exception as exc:  # noqa: BLE001 — a failed fit is a normal outcome
        return FitResult(model, reason=str(exc), n=len(t))

    sd = np.sqrt(np.abs(np.diag(pcov)))
    if not np.all(np.isfinite(sd)):
        return FitResult(model, reason="uncertainty is undefined (singular covariance)",
                         n=len(t))

    result = FitResult(model, params=popt, sd=sd, ok=True, n=len(t),
                       t0=t0, t_first=float(t[0]), t_last=float(t[-1]))
    tau, tau_sd = result.tau, result.tau_sd
    if tau is None or tau <= 0:
        return FitResult(model, reason=f"nonphysical tau ({tau})", n=len(t))
    if tau_sd > FIT_SD_REJECT_FRACTION * abs(tau):
        return FitResult(
            model, reason=f"uncertainty too large (tau = {tau:.4g} +/- {tau_sd:.4g})",
            n=len(t))
    return result


def tau_ratio(numerator, denominator):
    """⟨τ⟩ ratio between two fits, or None if either failed.

    None rather than NaN so the caller can leave a visible GAP at that potential — a
    failed fit is information, and silently skipping the point would hide it.
    """
    if numerator is None or denominator is None or not (numerator.ok and denominator.ok):
        return None
    top, bottom = numerator.mean_tau, denominator.mean_tau
    if not bottom:
        return None
    return float(top / bottom)
