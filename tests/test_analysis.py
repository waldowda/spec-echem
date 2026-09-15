"""
Tests for spec_echem.analysis — the maths behind the Results and Analysis tabs.

Everything here runs against synthetic data where the answer is known, because the
failure mode that matters is a fit that is quietly wrong: curve_fit does not raise on
bad data, it returns a number with a huge covariance.
"""
import numpy as np
import pytest

from spec_echem.analysis import (
    FitResult,
    auto_wavelengths, fit_transient, mean_relaxation_time,
    tau_ratio, model_exp, model_biexp, model_stretched,
)


# --- auto-wavelength: the signed difference ---------------------------------

def _film(n_wl=200, n_t=10):
    """A film whose pi-pi* BLEACHES more than its polaron band GROWS — the case where
    |dA| picks the wrong one."""
    wl = np.linspace(400.0, 1100.0, n_wl)
    pi = np.exp(-0.5 * ((wl - 550.0) / 40.0) ** 2)      # bleaches
    pol = np.exp(-0.5 * ((wl - 900.0) / 60.0) ** 2)     # grows
    a = np.zeros((n_wl, n_t))
    for j, f in enumerate(np.linspace(0.0, 1.0, n_t)):
        a[:, j] = 1.0 - 0.8 * f * pi + 0.3 * f * pol    # bleach is the LARGER change
    return a, wl


def test_the_polaron_and_pi_bands_are_told_apart_by_sign():
    """|dA| would return the bleach for both. The signed difference must not."""
    a, wl = _film()
    lam_pol, lam_pi = auto_wavelengths(a, wl)

    assert 850 < lam_pol < 950, f"polaron should be the GROWTH near 900 nm, got {lam_pol}"
    assert 500 < lam_pi < 600, f"pi-pi* should be the BLEACH near 550 nm, got {lam_pi}"


def test_the_magnitude_would_have_picked_the_wrong_band():
    """Pins WHY the signed form is used: on this film the bleach is larger, so argmax
    of |dA| lands on pi-pi* while the caller believes it is watching the polaron."""
    a, wl = _film()
    delta = a[:, -1] - a[:, 0]
    naive = float(wl[int(np.argmax(np.abs(delta)))])
    lam_pol, _ = auto_wavelengths(a, wl)

    assert 500 < naive < 600           # |dA| gives the pi band...
    assert abs(naive - lam_pol) > 200  # ...which is not the polaron


def test_a_single_spectrum_cannot_give_a_difference():
    a, wl = _film(n_t=1)
    assert auto_wavelengths(a, wl) == (None, None)


def test_a_shape_mismatch_is_refused_rather_than_broadcast():
    a, wl = _film()
    with pytest.raises(ValueError, match="n_wavelengths"):
        auto_wavelengths(a, wl[:-5])


# --- fitting: recover a known answer ----------------------------------------

def test_a_single_exponential_recovers_its_time_constant():
    t = np.linspace(0.0, 20.0, 400)
    y = model_exp(t, 0.5, 2.0, 3.5)
    fit = fit_transient(t, y, "exp")

    assert fit.ok
    assert fit.tau == pytest.approx(3.5, rel=1e-3)
    assert fit.mean_tau == pytest.approx(3.5, rel=1e-3)


def test_a_biexponential_reports_the_slow_component_as_tau():
    t = np.linspace(0.0, 40.0, 800)
    y = model_biexp(t, 0.1, 1.0, 0.4, 0.8, 9.0)
    fit = fit_transient(t, y, "biexp")

    assert fit.ok
    assert fit.tau == pytest.approx(9.0, rel=0.05)      # the SLOW one
    # <tau> is amplitude-weighted, so it sits between the two components
    assert 0.4 < fit.mean_tau < 9.0


def test_a_stretched_exponential_recovers_tau_and_beta():
    t = np.linspace(0.0, 30.0, 600)
    y = model_stretched(t, 0.2, 1.5, 4.0, 0.6)
    fit = fit_transient(t, y, "stretched")

    assert fit.ok
    assert fit.tau == pytest.approx(4.0, rel=0.05)
    assert fit.beta == pytest.approx(0.6, rel=0.05)


def test_the_window_excludes_the_capacitive_spike():
    """The whole reason the window exists: fitting through the spike gives the wrong
    time constant for the part that matters."""
    t = np.linspace(0.0, 30.0, 601)
    y = 3.0e-5 * np.exp(-t / 4.0) + 6.0e-4 * np.exp(-t / 0.05)

    through = fit_transient(t, y, "exp")
    windowed = fit_transient(t, y, "exp", t_start=1.0)

    assert windowed.ok
    assert windowed.tau == pytest.approx(4.0, rel=0.05)
    # fitting through the spike does not recover the ionic time constant
    assert not through.ok or abs(through.tau - 4.0) > 0.4


# --- the failures that must not look like numbers ---------------------------

def test_pure_noise_is_marked_for_review_but_its_numbers_stay_visible():
    """Dean: "even when a fit 'fails', the result should still be viewable... since
    you didn't share the results the scientist doesn't have information to make
    informed decisions."

    So `ok` says the fit failed a check, NOT that it has no numbers. A fit that
    converged keeps tau, beta and <tau>; what changes is that the reason travels with
    them.
    """
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 10.0, 200)
    fit = fit_transient(t, rng.normal(size=200), "exp")

    assert not fit.ok
    assert fit.reason
    assert fit.tau is not None, "the number must remain readable"
    assert fit.mean_tau is not None
    assert any("NEEDS REVIEW" in line for line in fit.describe())
    assert fit.needs_review and not fit.did_not_converge

def test_too_few_points_for_the_model_is_refused():
    t = np.linspace(0.0, 1.0, 4)
    fit = fit_transient(t, np.ones(4), "biexp")     # 5 parameters, 4 points
    assert not fit.ok and "points" in fit.reason


def test_an_unknown_model_is_a_programming_error_not_a_failed_fit():
    with pytest.raises(ValueError, match="model must be"):
        fit_transient([0, 1], [1, 0], "quadratic")


# --- mean relaxation time: the ratio depends on this ------------------------

def test_the_stretched_mean_time_is_not_the_raw_tau():
    """For beta < 1 the mean relaxation time exceeds tau, so a ratio built from raw
    tau is wrong in a way that looks entirely plausible."""
    raw = mean_relaxation_time("stretched", (0.0, 1.0, 4.0, 0.5))
    assert raw == pytest.approx(4.0 / 0.5 * 1.0, rel=0.3)   # (tau/beta)*Gamma(1/beta)
    assert raw > 4.0 * 1.5                                   # materially larger than tau


def test_beta_of_one_reduces_a_stretched_fit_to_a_simple_exponential():
    assert mean_relaxation_time("stretched", (0, 1, 4.0, 1.0)) == pytest.approx(4.0)


def test_the_biexponential_mean_is_amplitude_weighted():
    # equal amplitudes -> the arithmetic mean of the two time constants
    assert mean_relaxation_time("biexp", (0, 1.0, 2.0, 1.0, 8.0)) == pytest.approx(5.0)
    # a dominant fast component pulls it down
    assert mean_relaxation_time("biexp", (0, 9.0, 2.0, 1.0, 8.0)) == pytest.approx(2.6)


# --- the ratio --------------------------------------------------------------

def test_a_failed_fit_gives_no_ratio_so_the_plot_can_show_a_gap():
    """None rather than NaN: a failed fit is information, and silently dropping the
    point would hide which potential could not be fitted."""
    t = np.linspace(0.0, 20.0, 400)
    good = fit_transient(t, model_exp(t, 0.0, 1.0, 3.0), "exp")
    bad = fit_transient(t, np.random.default_rng(1).normal(size=400), "exp")

    assert tau_ratio(good, good) == pytest.approx(1.0)
    assert tau_ratio(good, bad) is None
    assert tau_ratio(bad, good) is None
    assert tau_ratio(good, None) is None


def test_the_ratio_uses_mean_times_so_models_stay_comparable():
    t = np.linspace(0.0, 30.0, 600)
    slow = fit_transient(t, model_exp(t, 0.0, 1.0, 8.0), "exp")
    fast = fit_transient(t, model_exp(t, 0.0, 1.0, 2.0), "exp")
    assert tau_ratio(slow, fast) == pytest.approx(4.0, rel=0.02)


# --- the fitted curve, for plotting over the data ---------------------------
# A tau on its own cannot be judged; the GUI draws the model over the points, so
# the curve has to land on the data it was fitted to.

def test_the_fitted_curve_lands_on_its_data():
    t = np.linspace(0.0, 10.0, 200)
    y = 0.5 + 2.0 * np.exp(-t / 1.7)
    fit = fit_transient(t, y, "exp")
    assert np.nanmax(np.abs(fit.curve(t) - y)) < 1e-9


def test_the_curve_is_nan_outside_the_fitted_window():
    """The fit makes no claim before its window. Extrapolating a decay back through
    the capacitive spike would draw a confident line through data it never saw."""
    t = np.linspace(0.0, 10.0, 200)
    y = 0.5 + 2.0 * np.exp(-t / 1.7)
    fit = fit_transient(t, y, "exp", t_start=2.0, t_stop=8.0)
    curve = fit.curve(t)
    assert np.isnan(curve[t < 2.0]).all()
    assert np.isnan(curve[t > 8.0]).all()
    assert np.isfinite(curve[(t >= 2.0) & (t <= 8.0)]).all()


def test_a_failed_fit_has_no_curve_to_draw():
    fit = FitResult("exp", reason="nope")
    assert fit.curve(np.linspace(0, 1, 10)) is None


def test_the_curve_is_offset_correctly_for_a_late_window():
    """The fit is done against elapsed time from the window start, so plotting it
    needs t0 — without it the curve would sit on the data shifted sideways."""
    t = np.linspace(0.0, 20.0, 400)
    y = 1.0 + 3.0 * np.exp(-t / 2.5)
    fit = fit_transient(t, y, "exp", t_start=10.0)
    late = t >= 10.0
    assert np.nanmax(np.abs(fit.curve(t)[late] - y[late])) < 1e-8


# --- band selection has to survive real noise -------------------------------
# MEASURED on 20260709_P3HT_01: the blue edge of that spectrometer sits on the dark
# floor (416 counts at 381 nm), and its absorbance swings +0.143 by noise alone --
# larger than the real polaron band's +0.093. Raw argmax picked the junk every time.

def test_a_noisy_dead_pixel_does_not_beat_a_real_band():
    rng = np.random.default_rng(0)
    wl = np.linspace(380.0, 1100.0, 200)
    t = np.linspace(0.0, 20.0, 120)
    frac = 1.0 - np.exp(-t / 4.0)
    polaron = np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2)
    a = 0.02 + np.outer(polaron, 0.10 * frac)
    a += rng.normal(0.0, 0.0005, a.shape)                  # real bands: clean
    dead = wl < 410.0                                      # the blue edge: not
    a[dead] += rng.normal(0.0, 0.08, (dead.sum(), len(t)))

    grows, _bleaches = auto_wavelengths(a, wl)
    assert 740 < grows < 880, f"picked {grows:.0f} nm, expected the polaron band"


def test_clean_data_is_unaffected_by_the_noise_gate():
    """When every pixel is significant this must still be plain argmax of dA, or
    the gate would change answers on data that never had a problem."""
    wl = np.linspace(400.0, 1100.0, 120)
    t = np.linspace(0.0, 20.0, 120)
    frac = 1.0 - np.exp(-t / 4.0)
    pi = np.exp(-0.5 * ((wl - 550.0) / 40.0) ** 2)
    polaron = np.exp(-0.5 * ((wl - 900.0) / 60.0) ** 2)
    a = 0.02 + np.outer(pi, 0.85 - 0.55 * frac) + np.outer(polaron, 0.50 * frac)
    grows, bleaches = auto_wavelengths(a, wl)
    assert 850 < grows < 950
    assert 500 < bleaches < 600


def test_pixels_below_the_optical_window_never_win():
    """Dean: "there should be no data below 410 nm or so." Below that the lamp and
    optics deliver nothing, so whatever the pixel reports is not a measurement."""
    wl = np.linspace(380.0, 1100.0, 200)
    t = np.linspace(0.0, 20.0, 60)
    frac = 1.0 - np.exp(-t / 4.0)
    a = 0.02 + np.outer(np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2), 0.10 * frac)
    a[wl < 410.0] += np.outer(np.ones((wl < 410.0).sum()), 5.0 * frac)  # huge and fake
    grows, _ = auto_wavelengths(a, wl)
    assert grows > 410.0, f"picked {grows:.0f} nm, inside the dead region"


def test_an_excluded_pixel_cannot_win_the_opposite_end():
    """Excluded pixels must go to -inf for the max and +inf for the min. Zeroing
    them lets a rejected pixel win argmin whenever every real delta is positive."""
    wl = np.linspace(380.0, 1100.0, 200)
    t = np.linspace(0.0, 20.0, 60)
    frac = 1.0 - np.exp(-t / 4.0)
    # every in-window pixel GROWS, so 0.0 would be the smallest value present
    a = 0.02 + np.outer(np.linspace(0.05, 0.30, len(wl)), frac)
    _grows, bleaches = auto_wavelengths(a, wl)
    assert bleaches > 410.0, f"argmin fell into the masked region at {bleaches:.0f} nm"


# --- the optimizer must not talk to the shell --------------------------------

def test_fitting_awkward_data_emits_no_runtime_warnings():
    """Dean's first launch at PLU printed three numpy RuntimeWarnings from the model
    functions. They come from curve_fit's trial steps -- tau -> 0, tau < 0 under a
    fractional beta, a tiny tau1 -- not from the answer, which is validated anyway.
    Reaching the shell they read as a malfunction, and the project keeps the shell
    silent.

    Warnings are RECORDED, not raised: simplefilter("error") would turn them into
    exceptions inside curve_fit, where fit_transient's own except-Exception swallows
    them, and the test would pass whether or not the fix was present.
    """
    import warnings

    t = np.linspace(0.0, 30.0, 300)
    awkward = {
        "step-like": 0.5 * (t > 1),
        "two-scale": 1e-4 * np.exp(-t / 0.02) + 1e-6 * np.exp(-t / 12),
        "flat noise": np.random.default_rng(0).normal(0.0, 1e-3, 300),
        "still rising": 0.02 * (t / 30.0) ** 3,
    }
    leaked = []
    for name, y in awkward.items():
        for model in ("exp", "biexp", "stretched"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                fit_transient(t, y, model)
            leaked += [f"{name}/{model}: {w.message}" for w in caught
                       if issubclass(w.category, RuntimeWarning)]
    assert not leaked, "numpy warnings reached the caller:\n" + "\n".join(leaked)


# --- physical bounds ---------------------------------------------------------
# Dean: "tau > 0 and beta needs to be 0 < beta <(=) 1.0."

def test_beta_cannot_exceed_one():
    """Above 1 it is a COMPRESSED exponential -- a different physical claim that
    this model does not offer. Compressed data must clamp, not be reported."""
    t = np.linspace(0.0, 20.0, 300)
    y = 0.5 + 2.0 * np.exp(-((t / 3.0) ** 1.8))      # genuinely compressed
    fit = fit_transient(t, y, "stretched")
    assert fit.ok
    assert fit.beta <= 1.0 + 1e-9, f"beta came back {fit.beta}"


def test_a_genuine_stretch_is_still_recovered():
    """The clamp must not flatten real stretched behaviour into beta = 1."""
    t = np.linspace(0.0, 20.0, 300)
    y = 0.5 + 2.0 * np.exp(-((t / 3.0) ** 0.6))
    fit = fit_transient(t, y, "stretched")
    assert fit.ok
    assert fit.beta == pytest.approx(0.6, abs=0.02)
    assert fit.tau == pytest.approx(3.0, abs=0.05)


def test_every_time_constant_is_positive():
    """A negative tau is not a slow decay, it is a growing exponential. The bounds
    keep the optimizer out of that region instead of rejecting it afterwards."""
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 30.0, 300)
    awkward = [rng.normal(0.0, 1e-3, 300),            # noise
               0.02 * (t / 30.0) ** 3,                # still rising
               0.5 * (t > 1)]                         # step
    for y in awkward:
        for model in ("exp", "biexp", "stretched"):
            fit = fit_transient(t, y, model)
            if fit.ok:
                assert fit.tau > 0
                if model == "biexp":
                    assert fit.params[2] > 0 and fit.params[4] > 0


def test_a_tau_far_longer_than_the_window_is_rejected():
    """The SD check cannot catch this: a nearly straight line is a very WELL-determined
    exponential with an enormous tau and a tiny uncertainty. MEASURED on
    the 20250710 reference run -- the charge integral returned 5.5e11 s from a 60 s
    segment and flattened every real point on the ladder to zero."""
    t = np.linspace(0.0, 60.0, 600)
    y = 1.0 + 0.001 * t                       # a straight line over the window
    fit = fit_transient(t, y, "exp")
    assert not fit.ok
    assert "window" in fit.reason, fit.reason


def test_a_tau_comparable_to_the_window_is_kept():
    """The bound must not reject a slow but genuinely observed decay."""
    t = np.linspace(0.0, 60.0, 600)
    y = 1.0 + 2.0 * np.exp(-t / 25.0)         # visibly curved, tau < span
    fit = fit_transient(t, y, "exp")
    assert fit.ok, fit.reason
    assert fit.tau == pytest.approx(25.0, rel=0.02)


# --- uncertainty on <tau> ----------------------------------------------------
# Dean: "adding SD's are better yet 95% CI to the plot points would be good."

def test_mean_tau_uncertainty_matches_tau_for_a_single_exponential():
    """<tau> IS tau for an exp, so its uncertainty must be tau_sd exactly -- the
    delta method has to reduce to the trivial case."""
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 400)
    fit = fit_transient(t, 1 + 2 * np.exp(-t / 3.0) + rng.normal(0, 0.01, 400), "exp")
    assert fit.mean_tau_sd == pytest.approx(fit.tau_sd, rel=1e-6)


def test_mean_tau_uncertainty_agrees_with_monte_carlo():
    """The delta method is only right if the covariance propagation is. Refitting
    noisy realisations and taking the spread of <tau> is the independent check."""
    rng = np.random.default_rng(7)
    t = np.linspace(0.0, 20.0, 400)
    base = 1 + 2 * np.exp(-((t / 3.0) ** 0.6))
    fit = fit_transient(t, base + rng.normal(0, 0.02, 400), "stretched")
    spread = [g.mean_tau for g in
              (fit_transient(t, base + rng.normal(0, 0.02, 400), "stretched")
               for _ in range(120)) if g.ok]
    assert len(spread) > 100
    assert fit.mean_tau_sd == pytest.approx(float(np.std(spread, ddof=1)), rel=0.35)


def test_the_stretched_mean_tau_is_less_certain_than_its_raw_tau():
    """<tau> = (tau/beta)*Gamma(1/beta) depends on BOTH fitted parameters, so quoting
    tau_sd as its uncertainty would understate it."""
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 400)
    fit = fit_transient(t, 1 + 2 * np.exp(-((t / 3.0) ** 0.6))
                        + rng.normal(0, 0.01, 400), "stretched")
    assert fit.mean_tau_sd > fit.tau_sd


def test_the_95_percent_interval_is_wider_than_one_sigma():
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 400)
    fit = fit_transient(t, 1 + 2 * np.exp(-t / 3.0) + rng.normal(0, 0.01, 400), "exp")
    assert fit.mean_tau_ci95 == pytest.approx(1.96 * fit.mean_tau_sd, rel=0.02)


# --- a rejected fit keeps its curve ------------------------------------------

def test_a_rejected_fit_still_has_a_curve_to_draw():
    """Dean: "it is still useful to know what the failed fit looks like... so perhaps
    we can understand why and setup a method to get a better fit." A fit that
    CONVERGED but failed a physical check keeps its parameters."""
    t = np.linspace(0.0, 60.0, 600)
    fit = fit_transient(t, 1.0 + 0.001 * t, "exp")       # tau >> window
    assert not fit.ok
    curve = fit.curve(t)
    assert curve is not None and np.isfinite(curve).any()


def test_a_fit_that_never_converged_has_no_curve():
    """The distinction that matters: no parameters means nothing to draw."""
    fit = fit_transient(np.linspace(0, 1, 3), np.zeros(3), "biexp")   # too few points
    assert not fit.ok
    assert fit.curve(np.linspace(0, 1, 3)) is None


def test_the_summary_lists_both_biexp_components():
    """Dean: "for biexp and strexp, I think it is important to include prefactor 1,
    tau1, prefactor 2, tau2, and mean tau." The legend showed only the SLOWER tau, so
    the fast component -- the reason for choosing biexp at all -- was invisible."""
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 400)
    y = 1 + 1.5 * np.exp(-t / 0.8) + 1.0 * np.exp(-t / 6.0) + rng.normal(0, 0.01, 400)
    lines = "\n".join(fit_transient(t, y, "biexp").describe())
    for name in ("B1", "tau1", "B2", "tau2", "mean tau"):
        assert name in lines, f"{name} missing from:\n{lines}"
    assert "95% CI" in lines and "1 SD" in lines, "both intervals must be labelled"


def test_the_summary_lists_tau_and_beta_for_a_stretched_fit():
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 400)
    y = 1 + 2 * np.exp(-((t / 3.0) ** 0.6)) + rng.normal(0, 0.01, 400)
    lines = "\n".join(fit_transient(t, y, "stretched").describe())
    for name in ("tau =", "beta =", "mean tau"):
        assert name in lines, f"{name} missing from:\n{lines}"


def test_the_baseline_is_in_the_summary():
    """A was left out at first, which made the negative prefactors unreadable: with
    no plateau on screen, a B < 0 looks like an error rather than a rising component.
    Dean: "the sum need to equal Y at time 0 of the fit" -- and that identity needs A.
    """
    t = np.linspace(0.0, 20.0, 200)
    fit = fit_transient(t, 1 + 2 * np.exp(-t / 3.0), "exp")
    lines = fit.describe()
    assert any(line.startswith("A =") for line in lines)
    assert any("y(0)" in line for line in lines)
    assert fit.params[0] == pytest.approx(1.0, abs=0.01)


def test_the_biexp_mean_stays_between_its_two_components():
    """A mean relaxation time outside [min(tau), max(tau)] is not one.

    MEASURED on the 20250710 reference run: above +0.5 V the fit puts a small RISING
    component against a large falling one, and signed amplitude weights partly
    cancel -- <tau> came out 0.117 s from tau1 = 0.553 s and tau2 = 4.11 s, and went
    NEGATIVE once the denominator crossed zero. Weighting by |B| fixes it.
    """
    for b1, tau1, b2, tau2 in [(-0.2996, 0.553, +0.03278, 4.11),   # the real case
                               (-0.2762, 0.633, +0.003492, 23.4),
                               (1.0, 0.5, 1.0, 5.0),               # same sign
                               (-1.0, 0.5, -1.0, 5.0)]:
        mean = mean_relaxation_time("biexp", (0.0, b1, tau1, b2, tau2))
        assert min(tau1, tau2) <= mean <= max(tau1, tau2), (b1, tau1, b2, tau2, mean)
        assert mean > 0


def test_equal_amplitudes_average_the_two_times():
    """The sanity case the |B| change must not break."""
    assert mean_relaxation_time("biexp", (0.0, 2.0, 1.0, 2.0, 3.0)) == pytest.approx(2.0)


def test_the_amplitudes_and_baseline_reconstruct_y_at_the_window_start():
    """Dean: "the sum need to equal Y at time 0 of the fit... at least roughly."
    A + sum(B) = y(0) is an identity of the model, and the one number that ties the
    fitted amplitudes back to the data."""
    t = np.linspace(0.0, 20.0, 400)
    for model, y in [("exp", 1.0 + 2.0 * np.exp(-t / 3.0)),
                     ("biexp", 1.0 + 1.5 * np.exp(-t / 0.8) + 0.5 * np.exp(-t / 6.0)),
                     ("stretched", 1.0 + 2.0 * np.exp(-((t / 3.0) ** 0.7)))]:
        fit = fit_transient(t, y, model)
        assert fit.ok, fit.reason
        assert fit.y_at_start == pytest.approx(y[0], rel=0.02)


def test_a_negative_prefactor_means_a_rising_component():
    """Absorbance GROWS on doping, so the exponentials climb to the plateau A from
    below and B is negative. That is a direction, not an error -- which is why A and
    y(0) are on the legend, so the sign is readable."""
    t = np.linspace(0.0, 20.0, 400)
    y = 0.11 - 0.10 * np.exp(-t / 2.0)          # rises to a plateau
    fit = fit_transient(t, y, "exp")
    assert fit.ok
    assert fit.params[0] == pytest.approx(0.11, abs=0.005)    # A is the plateau
    assert fit.params[1] < 0                                   # B is the rise
    assert fit.y_at_start == pytest.approx(y[0], abs=0.005)


def test_opposite_sign_prefactors_are_flagged_not_silently_averaged():
    """Dean: "generally the two prefactors need to be the same sign (except say if
    there is a bipolaron stealing abs from the polaron then there are competing
    processes)."

    So this is not automatically an error, and the fit must not decide. On
    the 20250710 reference run the flag fires at exactly +0.6 and +0.7 V -- the two
    highest doping levels, where a bipolaron would be expected to compete.
    """
    t = np.linspace(0.0, 20.0, 400)
    competing = 0.05 - 0.10 * np.exp(-t / 0.6) + 0.03 * np.exp(-t / 5.0)
    fit = fit_transient(t, competing, "biexp")
    assert fit.ok, fit.reason
    assert fit.mixed_amplitude_signs
    assert any("SIGN" in line for line in fit.describe())


def test_same_sign_prefactors_are_not_flagged():
    t = np.linspace(0.0, 20.0, 400)
    y = 1.0 + 1.5 * np.exp(-t / 0.8) + 0.5 * np.exp(-t / 6.0)
    fit = fit_transient(t, y, "biexp")
    assert fit.ok and not fit.mixed_amplitude_signs
    assert not any("SIGN" in line for line in fit.describe())


def test_a_single_component_fit_can_never_have_mixed_signs():
    """One prefactor cannot disagree with itself; the flag is for sums of parts."""
    t = np.linspace(0.0, 20.0, 200)
    for model in ("exp", "stretched"):
        fit = fit_transient(t, 1 + 2 * np.exp(-t / 3.0), model)
        assert not fit.mixed_amplitude_signs


# --- is it the right model? ---------------------------------------------------
# The parameter uncertainties cannot answer that: they describe the fit WITHIN the
# model. Dean: "leave option 1 documented as within the assumed model. Actually the
# noise / systematic split legend could be helpful."

def test_a_correct_model_leaves_only_noise():
    """Fit the right model to clean data plus white noise and the residual should be
    essentially all scatter, with no smooth curve left over."""
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 600)
    y = 1.0 + 2.0 * np.exp(-t / 3.0) + rng.normal(0, 0.002, 600)
    noise, systematic, _frac = fit_transient(t, y, "exp").residual_split(t, y)
    assert noise == pytest.approx(0.002, rel=0.2), "should recover the noise level"
    assert systematic < noise, f"systematic {systematic:.2g} vs noise {noise:.2g}"


def test_a_wrong_model_leaves_a_systematic_residual():
    """A single exponential over a genuinely two-component decay cannot follow it,
    and the leftover is a smooth curve, not scatter."""
    rng = np.random.default_rng(0)
    t = np.linspace(0.0, 20.0, 600)
    y = (1.0 + 1.5 * np.exp(-t / 0.4) + 1.0 * np.exp(-t / 8.0)
         + rng.normal(0, 0.002, 600))
    noise, systematic, _frac = fit_transient(t, y, "exp").residual_split(t, y)
    assert systematic > 3 * noise, f"systematic {systematic:.2g} vs noise {noise:.2g}"

    # and the right model brings it back down
    n2, s2, _ = fit_transient(t, y, "biexp").residual_split(t, y)
    assert s2 < systematic / 3, "biexp must account for what exp could not"


def test_the_split_needs_a_curve():
    t = np.linspace(0.0, 1.0, 3)
    assert fit_transient(t, np.zeros(3), "biexp").residual_split(t, np.zeros(3)) is None
