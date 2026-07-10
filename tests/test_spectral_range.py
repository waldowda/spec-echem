"""
Tests for spec_echem.spectral_range.recommend_wavelength_range.

Crafts a no-sample test-absorbance with the real observed shape — a quiet
central plateau (σ≈0.001) with noisy edges (±0.02 below ~420 nm, ±0.005 above
~1030 nm) — and checks the suggestion trims to the plateau, the rationale
numbers are right, and the conservative<->liberal knob behaves.
"""
import numpy as np

from spec_echem.spectral_range import recommend_wavelength_range

WL = np.linspace(380.0, 1100.0, 1265)   # matches the real calibrated window


def _test_abs(seed=0):
    """No-sample absorbance: σ=0.001 plateau, noisy edges (like Dean's blank)."""
    rng = np.random.default_rng(seed)
    sigma = np.full(WL.size, 0.001)
    sigma[WL < 420.0] = 0.02
    sigma[WL > 1030.0] = 0.005
    return rng.normal(0.0, sigma)


def test_trims_to_the_plateau():
    lo, hi, r = recommend_wavelength_range(WL, _test_abs(), noise_mult=3.0)
    # low edge (noisy < 420) and high edge (noisy > 1030) are trimmed off
    assert 400.0 < lo < 445.0, lo
    assert 1000.0 < hi < 1060.0, hi
    # plateau noise recovered to the right order of magnitude
    assert 0.0005 < r["plateau_sigma"] < 0.002, r["plateau_sigma"]
    assert r["wl_start"] == lo and r["wl_stop"] == hi
    assert "Suggested" in r["summary"]


def test_liberal_keeps_more_than_conservative():
    lo_c, hi_c, _ = recommend_wavelength_range(WL, _test_abs(), noise_mult=2.0)
    lo_l, hi_l, _ = recommend_wavelength_range(WL, _test_abs(), noise_mult=6.0)
    # a more liberal (higher) multiplier keeps at least as wide a band
    assert lo_l <= lo_c and hi_l >= hi_c
    assert (hi_l - lo_l) >= (hi_c - lo_c)


def test_quiet_spectrum_keeps_full_range():
    rng = np.random.default_rng(1)
    quiet = rng.normal(0.0, 0.001, WL.size)   # uniformly quiet, no bad edges
    lo, hi, _ = recommend_wavelength_range(WL, quiet, noise_mult=3.0)
    assert lo <= 385.0 and hi >= 1095.0


def test_ref_corroboration_reported():
    # tungsten-like hump that falls off at the edges (dim there)
    lamp = 500.0 + 45000.0 * np.exp(-((WL - 750.0) ** 2) / (2 * 180.0 ** 2))
    dark = np.zeros(WL.size)
    _, _, r = recommend_wavelength_range(WL, _test_abs(), dark=dark, ref=lamp)
    assert r["ref_edges"] is not None
    lo_e, hi_e = r["ref_edges"]
    assert lo_e < hi_e
    assert r["ref_peak"] > 0
    assert "lamp" in r["summary"].lower()
