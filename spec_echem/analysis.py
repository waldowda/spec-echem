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

def auto_wavelengths(absorbance, wavelengths, early=0, late=-1):
    """(λ_polaron, λ_π) from the SIGNED change in absorbance.

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
    return float(wl[int(np.argmax(delta))]), float(wl[int(np.argmin(delta))])


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
        popt, pcov = curve_fit(func, t - t0, y, p0=_initial_guess(model, t - t0, y),
                               maxfev=10000)
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
