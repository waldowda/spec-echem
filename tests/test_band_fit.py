"""Fitting every wavelength in a band.

The scientific point is tau(lambda): a relaxation time that VARIES across an
absorption band says the band is not one species relaxing, and a single probe
wavelength cannot show that.
"""
import numpy as np
import pytest

from spec_echem.analysis import BandFit, fit_band

N_T = 80


def _band(taus, noise=2e-4, seed=0, a=0.1, b=0.5):
    """A synthetic band whose tau varies with wavelength, by construction."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 12.0, N_T)
    wl = np.linspace(700.0, 900.0, len(taus))
    absorb = np.array([a + b * (1 - np.exp(-t / tau)) + rng.normal(0, noise, t.size)
                       for tau in taus])
    return absorb, wl, t


def test_tau_is_recovered_across_the_band():
    taus = [1.0, 1.5, 2.0, 2.5, 3.0]
    absorb, wl, t = _band(taus)
    band = fit_band(absorb, wl, t, 700, 900)

    assert len(band) == len(taus)
    np.testing.assert_allclose(band.wavelengths, wl)
    got = [r.tau for _w, r in band.ok]
    np.testing.assert_allclose(got, taus, rtol=0.02)


def test_a_wavelength_that_will_not_fit_does_not_kill_the_band():
    """The version this mimics calls curve_fit bare, so one non-converging
    wavelength raises and the whole band is lost. A band is most interesting at its
    EDGES, which is exactly where fits get hard."""
    absorb, wl, t = _band([1.0, 1.5, 2.0, 2.5, 3.0])
    absorb[2, :] = np.nan                       # a wavelength that cannot be fitted

    band = fit_band(absorb, wl, t, 700, 900)

    assert len(band) == 5                       # all five present
    assert len(band.ok) == 4                    # four usable
    assert band.summary()["no_convergence"] == 1
    bad = band.results[2]
    assert not bad.ok and bad.reason             # says WHY, rather than vanishing


def test_every_wavelength_is_fitted_including_the_first():
    """The original starts at index.values[1:], silently dropping the first, with no
    comment saying why.

    Asserts the COUNT and the pairing, not just the first label: wavelengths come
    from the row index and results from the fitting loop, so checking
    `wavelengths[0]` alone passes even when the loop has dropped an iteration and
    every tau is attached to the wrong wavelength. Mutation testing found exactly
    that hole on 2026-09-30."""
    taus = [1.0, 2.0, 3.0]
    absorb, wl, t = _band(taus)
    band = fit_band(absorb, wl, t, 700, 900)

    assert len(band.results) == len(band.wavelengths) == 3
    assert band.wavelengths[0] == pytest.approx(700.0)
    assert band.results[0].ok
    # each tau sits against the wavelength it was fitted at
    for expected, (got_wl, r) in zip(taus, band.ok):
        assert r.tau == pytest.approx(expected, rel=0.02)
        assert got_wl in wl


def test_misaligned_arrays_are_refused_outright():
    """A wrong answer, not a crash: every tau labelled with the wrong wavelength."""
    from spec_echem.analysis import BandFit
    with pytest.raises(ValueError, match="must be parallel"):
        BandFit([700.0, 800.0], [object()], "exp", 700, 800)


def test_the_band_is_clipped_to_the_requested_range():
    absorb, wl, t = _band([1.0, 1.5, 2.0, 2.5, 3.0])          # 700..900 in 50s
    band = fit_band(absorb, wl, t, 760, 860)          # 800 and 850 nm
    assert band.wavelengths.min() >= 760 and band.wavelengths.max() <= 860
    assert len(band) == 2
    # reversed endpoints mean the same band
    assert len(fit_band(absorb, wl, t, 860, 760)) == 2


def test_a_band_outside_the_data_says_what_is_available():
    absorb, wl, t = _band([1.0, 2.0])
    with pytest.raises(ValueError, match=r"no wavelengths between .*700\.0-900\.0 nm"):
        fit_band(absorb, wl, t, 1200, 1400)


def test_a_wrong_shaped_absorbance_is_refused():
    absorb, wl, t = _band([1.0, 2.0])
    with pytest.raises(ValueError, match="n_wavelengths, n_times"):
        fit_band(absorb.T, wl, t, 700, 900)


def test_dead_pixels_are_flagged_but_still_fitted():
    """Flagged, never skipped: the user chose this band deliberately, and silently
    dropping pixels would misrepresent where it ends."""
    absorb, wl, t = _band([1.0, 1.5, 2.0, 2.5, 3.0])
    rng = np.random.default_rng(1)
    absorb[0, :] = rng.normal(0.0, 2e-4, N_T)       # noise, no transient

    band = fit_band(absorb, wl, t, 700, 900)

    assert len(band) == 5                            # still fitted
    assert pytest.approx(700.0) == band.low_snr[0]
    assert band.summary()["low_snr"] == 1
    assert band.table()["low_snr"].iloc[0]


def test_the_table_carries_parameters_uncertainties_and_reasons():
    """The original returns bare taus: which parameter is which is implicit, there is
    no uncertainty, and a failed wavelength cannot be represented at all."""
    absorb, wl, t = _band([1.0, 2.0, 3.0])
    table = fit_band(absorb, wl, t, 700, 900).table()

    assert list(table["wavelength_nm"]) == list(wl)
    for column in ("tau", "tau_sd", "tau_mean", "ok", "reason", "n_points",
                   "A", "B", "low_snr"):
        assert column in table.columns
    assert (table["tau_sd"] > 0).all()


def test_taus_stay_aligned_with_the_wavelengths():
    """NaN where a fit produced nothing, so tau(lambda) can be plotted directly
    without the x and y drifting apart."""
    absorb, wl, t = _band([1.0, 1.5, 2.0])
    absorb[1, :] = np.nan
    w, tau = fit_band(absorb, wl, t, 700, 900).taus()
    assert w.size == tau.size == 3
    assert np.isnan(tau[1]) and np.isfinite(tau[0]) and np.isfinite(tau[2])


def test_the_window_is_honoured_and_reported():
    absorb, wl, t = _band([1.0, 2.0])
    band = fit_band(absorb, wl, t, 700, 900, t_start=2.0, t_stop=9.0)
    assert band.t_first >= 2.0 and band.t_last <= 9.0
    assert band.results[0].n < N_T                    # fewer points than the segment


@pytest.mark.parametrize("model", ["exp", "biexp", "stretched"])
def test_every_model_returns_a_result_per_wavelength(model):
    """The contract is that fit_band NEVER raises and always returns one result per
    wavelength -- not that every model converges. A biexponential on
    single-exponential data is ill-posed and legitimately fails; the band still comes
    back, one row each, saying so."""
    absorb, wl, t = _band([1.0, 2.0, 3.0], noise=1e-4)
    band = fit_band(absorb, wl, t, 700, 900, model=model)

    assert band.model == model
    assert len(band) == 3 and len(band.results) == 3
    table = band.table()
    assert len(table) == 3 and "tau" in table.columns
    assert all(isinstance(r.reason, str) for r in band.results)


def test_a_genuinely_biexponential_band_fits_as_biexponential():
    """The complement: given two real timescales, the biexp model finds them."""
    rng = np.random.default_rng(3)
    t = np.linspace(0.0, 30.0, 300)
    wl = np.array([780.0, 800.0])
    absorb = np.array([0.05 + 0.3 * (1 - np.exp(-t / 0.8))
                       + 0.4 * (1 - np.exp(-t / 9.0))
                       + rng.normal(0, 5e-5, t.size) for _ in wl])

    band = fit_band(absorb, wl, t, 700, 900, model="biexp")

    assert len(band.converged) == 2
    for _w, r in band.converged:
        taus = sorted((r.params[2], r.params[4]))
        assert taus[0] == pytest.approx(0.8, rel=0.15)
        assert taus[1] == pytest.approx(9.0, rel=0.15)


@pytest.mark.parametrize("model,expected", [
    ("exp", "tau"), ("biexp", "tau2"), ("stretched", "tau")])
def test_every_model_has_a_comparable_tau_column(model, expected):
    """taus() plots ONE tau per wavelength whatever the model, so the table must
    carry the matching column. A biexp band otherwise has tau1 and tau2 and no single
    name for the one being plotted."""
    absorb, wl, t = _band([1.0, 2.0], noise=1e-5)
    table = fit_band(absorb, wl, t, 700, 900, model=model).table()

    assert "tau" in table.columns and expected in table.columns
    band = fit_band(absorb, wl, t, 700, 900, model=model)
    _w, plotted = band.taus()
    np.testing.assert_allclose(table["tau"].to_numpy(), plotted, equal_nan=True)
