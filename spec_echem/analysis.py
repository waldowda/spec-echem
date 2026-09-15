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
from scipy.stats import t as student_t
from scipy.special import gamma

logger = logging.getLogger(__name__)

# A fit whose parameter uncertainty is this fraction of the parameter is not a
# measurement. curve_fit does not raise on a bad fit — it returns a huge covariance —
# so without a check the table would show a confident-looking number.
FIT_SD_REJECT_FRACTION = 0.5

# A tau far longer than the data it was fitted to is an extrapolation, not a
# measurement, and the SD check cannot catch it: a nearly straight line is a very
# WELL-DETERMINED exponential with an enormous tau and a tiny uncertainty. MEASURED
# on the 20250710 reference run, where the charge integral returned tau = 5.5e11 s from a
# 60 s segment -- 17000 years -- and flattened every real point on the ladder to zero.
# 10x the window is generous: a decay that slow is 5% complete by the end.
FIT_MAX_TAU_SPANS = 10.0


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

# The functional form, shown beside the Model dropdown -- Requested: "why don't you put the
# equation to the right of the model choice area above instead of adding yet more info
# in the graph?" It belongs where the model is CHOSEN, and the legend is already dense.
# A is what the curve approaches as t -> inf; B is the amplitude of the part that
# decays; y(0) = A + sum(B). ASCII so it renders on any matplotlib.
MODEL_FORMULAS = {
    "exp": "A + B*exp(-t/tau)",
    "biexp": "A + B1*exp(-t/tau1) + B2*exp(-t/tau2)",
    "stretched": "A + B*exp(-(t/tau)^beta)",
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
        biexp      <τ> = (|B₁|τ₁ + |B₂|τ₂) / (|B₁| + |B₂|)   amplitude-weighted
        stretched  <τ> = (τ/β)·Γ(1/β)
    """
    if model == "exp":
        return float(params[2])
    if model == "biexp":
        _, b1, tau1, b2, tau2 = params
        # |B|, not B. The textbook amplitude-weighted mean assumes both components
        # decay the same way, and then the signs agree and it does not matter. When
        # a fit puts a small RISING component against a large falling one -- which
        # the 20250710 reference run does above +0.5 V -- the signed weights partly
        # cancel and the "mean" leaves the range of its own components: 0.117 s from
        # tau1 = 0.553 s and tau2 = 4.11 s, and NEGATIVE once the denominator crosses
        # zero. A mean relaxation time outside [min(tau), max(tau)] is not one.
        denom = abs(b1) + abs(b2)
        if denom == 0:
            return float("nan")
        return float((abs(b1) * tau1 + abs(b2) * tau2) / denom)
    if model == "stretched":
        _, _, tau, beta = params
        if beta <= 0:
            return float("nan")
        return float((tau / beta) * gamma(1.0 / beta))
    raise ValueError(f"unknown model {model!r}")


# --- wavelength selection ----------------------------------------------------

# Below this the lamp and optics deliver nothing on these rigs -- MEASURED 416 counts
# at 381 nm on 20260709_P3HT_01, against 41250 at 780 nm. Requested: "there should be no
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


def probe_wavelength(absorbance, wavelengths, doping=True):
    """The POLARON wavelength -- which is NOT always the band that grows.

    On DOPING the polaron grows while pi-pi* bleaches, so the polaron is the growth.
    On DEDOPING and pre-dedoping it is the other way round: the polaron DECAYS while
    pi-pi* recovers, and taking the growth there returns pi labelled as the polaron.

    ONE definition, because both the Results tab and the Analysis tab need it and the
    first copy of this logic only went into one of them.
    """
    grows, bleaches = auto_wavelengths(absorbance, wavelengths)
    if grows is None:
        return None
    return grows if doping else bleaches


# --- fitting -----------------------------------------------------------------

class FitResult:
    """One fit. `ok` is False when the fit did not converge or its uncertainty makes
    the number meaningless — the caller shows the reason rather than a plausible
    wrong value."""

    def __init__(self, model, params=None, sd=None, ok=False, reason="", n=0,
                 t0=0.0, t_first=None, t_last=None, cov=None):
        self.model = model
        self.params = params
        self.sd = sd
        # The FULL covariance, not just its diagonal: <tau> is a nonlinear function
        # of several parameters, so its uncertainty needs the off-diagonal terms.
        # tau and beta of a stretched exponential are strongly anticorrelated, and
        # ignoring that overstates the error badly.
        self.cov = cov
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
        the data. Available for a REJECTED fit too, as long as it converged -- seeing
        what a bad fit looks like is how you work out what to change. None only when
        there are no parameters at all (curve_fit raised, or too few points).

        NaN outside the fitted window — the fit makes no claim there, and
        extrapolating a decay backwards through the capacitive spike would draw a
        confident line through data it never saw."""
        if self.params is None:
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
        ⟨τ⟩ is what the ratio view uses.

        Available whenever the fit CONVERGED, pass or fail. Requested: "even when a fit
        'fails', the result should still be viewable... since you didn't share the
        results the scientist doesn't have information to make informed decisions."
        `ok` says whether it passed the physical checks, not whether it has numbers.
        """
        if self.params is None:
            return None
        if self.model == "biexp":
            return float(max(self.params[2], self.params[4]))
        return float(self.params[2])

    @property
    def beta(self):
        return float(self.params[3]) \
            if self.params is not None and self.model == "stretched" else None

    @property
    def tau_sd(self):
        if self.params is None or self.sd is None:
            return None
        if self.model == "biexp":
            return float(self.sd[2] if self.params[2] >= self.params[4] else self.sd[4])
        return float(self.sd[2])

    @property
    def needs_review(self):
        """Converged, but a physical check objected. NOT "failed".

        Requested: "the scientist should have results and make decisions and not have the
        software make decisions about whether the user should see data or fits to
        data... I would highlight these fits as questionable or requiring extra
        review."

        So the software's job here is to raise a concern, never to withhold. The fit
        has a curve and a full set of parameters; `reason` says what to look at.
        """
        return (not self.ok) and self.params is not None

    @property
    def did_not_converge(self):
        """No parameters at all — curve_fit raised, the covariance was singular, or
        there were fewer points than parameters. A statement of fact, not a verdict:
        there is genuinely nothing to show."""
        return self.params is None

    @property
    def mixed_amplitude_signs(self):
        """True when a multi-component fit's prefactors disagree in sign.

        Requested: "generally the two prefactors need to be the same sign (except say if
        there is a bipolaron stealing abs from the polaron then there are competing
        processes)."

        So this is NOT automatically wrong. Two components of one process pull the
        same way; opposite signs mean either competing processes -- a bipolaron band
        growing at the polaron's expense -- or a fit that has wandered somewhere
        unphysical. Only someone who knows the sample can tell which, so the fit
        says it rather than deciding.
        """
        if self.params is None:
            return False
        amps = [v for name, v in zip(MODELS[self.model][1], self.params)
                if name.startswith("B")]
        return len(amps) > 1 and not (all(a >= 0 for a in amps)
                                      or all(a <= 0 for a in amps))

    @property
    def y_at_start(self):
        """The model at the START of its fitted window: A + sum of the prefactors.

        An identity of every model here, and the one number that ties the fitted
        amplitudes back to the data -- it must match the first point the fit saw.
        """
        if self.params is None:
            return None
        amplitudes = [v for name, v in zip(MODELS[self.model][1], self.params)
                      if name.startswith("B")]
        return float(self.params[0] + sum(amplitudes))

    @property
    def mean_tau(self):
        return (mean_relaxation_time(self.model, self.params)
                if self.params is not None else None)

    @property
    def mean_tau_sd(self):
        """1-sigma on <tau>, by the delta method: sigma^2 = grad(f)' C grad(f).

        <tau> is what the ladder plots, and for biexp and stretched it is a nonlinear
        combination of the fitted parameters -- so its uncertainty is NOT tau_sd. The
        gradient is numerical because the closed forms (one of which involves the
        digamma function) would be a second place for the mean-time definition to live
        and drift out of step with mean_relaxation_time.
        """
        if self.params is None or self.cov is None:
            return None
        params = np.asarray(self.params, dtype=float)
        grad = np.zeros(len(params))
        for i, value in enumerate(params):
            step = 1e-6 * max(abs(value), 1e-8)
            up, down = params.copy(), params.copy()
            up[i], down[i] = value + step, value - step
            try:
                grad[i] = ((mean_relaxation_time(self.model, up)
                            - mean_relaxation_time(self.model, down)) / (2 * step))
            except (ValueError, ZeroDivisionError, FloatingPointError):
                return None
        var = float(grad @ np.asarray(self.cov, dtype=float) @ grad)
        return float(np.sqrt(var)) if np.isfinite(var) and var >= 0 else None

    @property
    def mean_tau_ci95(self):
        """Half-width of the 95% confidence interval on <tau>.

        Student t on (n - p) degrees of freedom, not a flat 1.96: with 600 points the
        two agree to <1%, but a short segment fitted with a 5-parameter biexp can have
        few enough degrees of freedom for it to matter.
        """
        sd = self.mean_tau_sd
        if sd is None:
            return None
        dof = self.n - len(self.params)
        if dof < 1:
            return None
        return float(student_t.ppf(0.975, dof) * sd)

    def residual_split(self, time, values):
        """(noise, systematic, fraction_of_swing) for the residual over the window.

        Successive differences cancel any smooth trend, so the point-to-point scatter
        of the residual IS the measurement noise; whatever RMS is left over and above
        that is a smooth curve the model failed to follow.

            noise      = SD(diff(resid)) / sqrt(2)
            systematic = sqrt(max(SD(resid)^2 - noise^2, 0))

        This is the "is it the right model" readout. A residual dominated by noise
        means the model has taken everything there is; one dominated by systematic
        means it has not, however small the uncertainty on its parameters. MEASURED
        on the 20250710 reference run Doping 0 @ 800 nm: noise 3.2e-4 OD, systematic
        1.5e-3 OD -- 4.6x the noise, but only 1.4% of a 0.106 OD swing.

        None when there is no curve or too few points inside the window.
        """
        curve = self.curve(time)
        if curve is None:
            return None
        y = np.asarray(values, dtype=float)
        resid = y - curve
        keep = np.isfinite(resid)
        resid = resid[keep]
        if resid.size < 3:
            return None
        total = float(np.std(resid, ddof=1))
        noise = float(np.std(np.diff(resid), ddof=1) / np.sqrt(2.0))
        systematic = float(np.sqrt(max(total ** 2 - noise ** 2, 0.0)))
        swing = float(np.nanmax(y[keep]) - np.nanmin(y[keep])) if keep.any() else 0.0
        fraction = systematic / swing if swing > 0 else float("nan")
        return noise, systematic, fraction

    def describe(self):
        """Every fitted parameter, one string per line, for the plot legend.

        Requested: "for biexp and strexp, I think it is important to include prefactor 1,
        tau1, prefactor 2, tau2, and mean tau." The legend showed only the SLOWER tau
        of a biexp, so the fast component -- the capacitive one, and the reason for
        choosing biexp at all -- was invisible.

        The baseline A is left out: it is an offset, not a kinetic parameter. It is
        still on self.params for anyone who wants it.

        Note the two DIFFERENT intervals, which is why each is labelled: the
        per-parameter +/- is 1 SD straight off the covariance diagonal, while <tau>
        carries the 95% CI that the ladder plots.
        """
        if self.params is None:
            return []
        names = MODELS[self.model][1]
        sds = self.sd if self.sd is not None else [float("nan")] * len(names)
        lines = [f"{self.model}   (+/- = 1 SD)"
                 + ("" if self.ok else "   [NEEDS REVIEW — see below]")]
        for name, value, sd in zip(names, self.params, sds):
            unit = " s" if name.startswith("tau") else ""
            lines.append(f"{name} = {value:.4g} +/- {sd:.2g}{unit}")
        # y(0) = A + sum(B) is an identity of the model, and it is the check the user
        # asked for: it must equal the first fitted data point. Showing it also makes
        # the sign of B readable -- B < 0 is a RISING component, climbing to the
        # plateau A from below, which is what a growing polaron band does. Without A
        # on screen a negative prefactor looks like an error rather than a direction.
        lines.append(f"y(0) = {self.y_at_start:.4g}   (A + sum of prefactors)")
        if self.mixed_amplitude_signs:
            lines.append("! prefactors differ in SIGN — competing processes"
                         " (e.g. bipolaron vs polaron), or a poor fit")
        ci = self.mean_tau_ci95
        lines.append(f"mean tau = {self.mean_tau:.4g}"
                     + (f" +/- {ci:.2g}" if ci is not None else "")
                     + " s (95% CI)")
        lines.append(f"{self.n} pts")
        if not self.ok:
            # The concern, alongside the numbers rather than instead of them. The
            # software raises it; the scientist decides what it means.
            lines.append(f"NEEDS REVIEW: {self.reason}")
        return lines

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
        # on the first launch under SpecEchem32 -- which reads like a malfunction when it is the
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
                       t0=t0, t_first=float(t[0]), t_last=float(t[-1]), cov=pcov)
    tau, tau_sd = result.tau, result.tau_sd
    if tau is None or tau <= 0:
        return _rejected(result, f"nonphysical tau ({tau})")
    span = float(t[-1] - t[0])
    if span > 0 and tau > FIT_MAX_TAU_SPANS * span:
        return _rejected(
            result,
            f"tau ({tau:.3g} s) exceeds {FIT_MAX_TAU_SPANS:g}x the {span:.3g} s "
            f"window - not measurable from it")
    if tau_sd > FIT_SD_REJECT_FRACTION * abs(tau):
        return _rejected(
            result, f"uncertainty too large (tau = {tau:.4g} +/- {tau_sd:.4g})")
    return result


def _rejected(result, reason):
    """A fit that CONVERGED but failed a physical check, keeping its parameters.

    Requested: "I think it is still useful to know what the failed fit looks like... so
    perhaps we can understand why and setup a method to get a better fit." A rejected
    exponential running flat through a real decay says change the model; one hugging
    the capacitive spike says move the window. Discarding the parameters threw away
    the only evidence of which.

    ok stays False -- the number is still not a measurement. Only the curve survives.
    """
    return FitResult(result.model, params=result.params, sd=result.sd, ok=False,
                     reason=reason, n=result.n, t0=result.t0,
                     t_first=result.t_first, t_last=result.t_last, cov=result.cov)


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


# --- density of states from a CV ---------------------------------------------
# Design: docs/analysis-design.md. For a sweep slow enough to be quasi-equilibrium the
# current IS the differential charge, i = v·dQ/dV, so
#
#     g(E) = i / (v · e · V_film)        states eV⁻¹ cm⁻³,   E = −eV
#
# Units: (C/s)·(s/V)/(C·cm³) = eV⁻¹cm⁻³.
#
# The arithmetic is trivial. What is NOT settled here, deliberately: the capacitive
# baseline is NOT subtracted (double-layer charging is not density of states, but
# whatever is removed changes the answer and must be visible), and whether the scan
# rate was slow enough is an empirical question — run several rates and check i/v
# collapses — that no amount of code can answer.

ELEMENTARY_CHARGE = 1.602176634e-19      # C


def scan_rate_from_sweep(potential, duration_s):
    """Scan rate in V/s, from the potential trace and how long the sweep took.

    CV.txt records potential and current only -- no time column (docs/data-format.md),
    so the rate cannot come from it alone. But the CV's spectra file carries corrected
    times, and total path swept / elapsed time IS the rate:

        v = sum(|dV|) / duration

    MEASURED on the reference run: 7.19 V over 73.0 s = 98.4 mV/s, against a nominal
    100 mV/s. Preferring this over the settings value follows the same rule as
    segment potentials -- take it from the data, because the form may describe a
    different experiment.

    Returns None when it cannot be computed. The estimate assumes the spectra cover
    the whole sweep, which is how a CV segment is acquired; a truncated spectra file
    would make it read high.
    """
    v = np.asarray(potential, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 2 or not duration_s or duration_s <= 0:
        return None
    swept = float(np.abs(np.diff(v)).sum())
    if swept <= 0:
        return None
    return swept / float(duration_s)


def cv_sweeps(potential):
    """Index slices for each monotonic sweep of a CV, split at every reversal.

    A CV runs several cycles and the film is not the same on the first as on the last,
    so the caller picks which to use rather than being handed an average.
    """
    v = np.asarray(potential, dtype=float)
    if v.size < 3:
        return []
    direction = np.sign(np.diff(v))
    direction[direction == 0] = 0
    # ignore zero-steps when deciding where the sweep turns around
    nonzero = direction[direction != 0]
    if nonzero.size == 0:
        return []
    sweeps, start, current = [], 0, nonzero[0]
    for i, step in enumerate(direction):
        if step != 0 and step != current:
            sweeps.append(slice(start, i + 1))
            start, current = i, step
    sweeps.append(slice(start, len(v)))
    return [s for s in sweeps if (s.stop - s.start) >= 3]


def _last_complete_cycle(v, sweeps):
    """The last FULL sweep of each direction, in order.

    A CV usually starts and ends partway along a sweep -- at 0 V rather than at a
    vertex -- so the first and last entries from cv_sweeps are partial. Taking the
    last two gave one full sweep and one truncated tail, which is why the reverse
    curve covered a shorter potential range than the forward one on the same plot.
    """
    spans = [abs(v[s][-1] - v[s][0]) for s in sweeps]
    if not spans:
        return []
    full = max(spans)
    complete = [s for s, span in zip(sweeps, spans) if span >= 0.9 * full]
    last_rising = next((s for s in reversed(complete) if v[s][-1] > v[s][0]), None)
    last_falling = next((s for s in reversed(complete) if v[s][-1] < v[s][0]), None)
    return [s for s in (last_rising, last_falling) if s is not None]


def density_of_states(potential, current, scan_rate_v_per_s, volume_cm3=None,
                      last_cycle_only=True):
    """DOS against energy, one entry per sweep direction.

    Returns a list of dicts: {"direction": "forward"|"reverse", "energy_ev",
    "dos", "units"}. With `volume_cm3` the units are states eV⁻¹cm⁻³; without it the
    value is dQ/dV in C/V and `units` says so, rather than a volume being invented.

    `last_cycle_only` keeps the final forward/reverse pair — the film has been cycled
    by then, so it is the closest to a settled response. Directions are kept SEPARATE:
    hysteresis is real and averaging it away hides an effect.
    """
    v = np.asarray(potential, dtype=float)
    i = np.asarray(current, dtype=float)
    if v.size != i.size:
        raise ValueError(f"potential and current differ in length: {v.size} vs {i.size}")
    if not scan_rate_v_per_s or scan_rate_v_per_s <= 0:
        raise ValueError(f"scan rate must be positive, got {scan_rate_v_per_s!r}")

    sweeps = cv_sweeps(v)
    if not sweeps:
        return []
    if last_cycle_only:
        sweeps = _last_complete_cycle(v, sweeps)
        if not sweeps:
            return []

    denom = ELEMENTARY_CHARGE * volume_cm3 if volume_cm3 else 1.0
    units = ("states eV^-1 cm^-3" if volume_cm3 else "dQ/dV (C/V) — no film volume")

    out = []
    for sweep in sweeps:
        vv, ii = v[sweep], i[sweep]
        rising = vv[-1] > vv[0]
        # SIGNED sweep rate. dQ/dV = i / (dV/dt), and on the reverse sweep BOTH are
        # negative, so the quotient is positive -- a DOS is positive in either
        # direction. Dividing by the magnitude flipped the reverse sweep below zero.
        rate = scan_rate_v_per_s if rising else -scan_rate_v_per_s
        out.append({
            # Rising potential REMOVES electrons from the film (oxidising, p-doping);
            # falling potential puts them back (reducing, de-doping). Worth saying on
            # the plot: "forward" alone does not tell you which way charge is going.
            "direction": "oxidising (forward)" if rising else "reducing (reverse)",
            # E = -eV: a more positive potential removes electrons, i.e. probes
            # deeper into the occupied states.
            "energy_ev": -vv,
            "dos": ii / (rate * denom),
            "units": units,
        })
    return out
