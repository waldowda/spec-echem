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


def test_trims_both_edges_at_a_tight_tolerance():
    # max_noise between the plateau (0.001) and the high edge (0.005) trims both
    lo, hi, r = recommend_wavelength_range(WL, _test_abs(), max_noise=0.003)
    assert 400.0 < lo < 445.0, lo
    assert 1000.0 < hi < 1060.0, hi
    assert 0.0005 < r["plateau_sigma"] < 0.002, r["plateau_sigma"]
    assert r["wl_start"] == lo and r["wl_stop"] == hi
    assert "Keeping" in r["summary"]


def test_liberal_tolerance_keeps_the_low_noise_high_edge():
    # high edge σ≈0.005 is kept when max_noise=0.01 but trimmed at 0.002
    _, hi_tol, _ = recommend_wavelength_range(WL, _test_abs(), max_noise=0.01)
    _, hi_tight, _ = recommend_wavelength_range(WL, _test_abs(), max_noise=0.002)
    assert hi_tol > hi_tight
    assert hi_tol > 1050.0     # 0.005 < 0.01 → the high edge survives


def test_higher_tolerance_keeps_at_least_as_much():
    lo_c, hi_c, _ = recommend_wavelength_range(WL, _test_abs(), max_noise=0.002)
    lo_l, hi_l, _ = recommend_wavelength_range(WL, _test_abs(), max_noise=0.02)
    assert lo_l <= lo_c and hi_l >= hi_c
    assert (hi_l - lo_l) >= (hi_c - lo_c)


def test_quiet_spectrum_keeps_full_range():
    rng = np.random.default_rng(1)
    quiet = rng.normal(0.0, 0.001, WL.size)   # uniformly quiet, no bad edges
    lo, hi, _ = recommend_wavelength_range(WL, quiet, max_noise=0.01)
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
