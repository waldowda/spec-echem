"""The model-free impedance estimators and the Randles fit (spec_echem/impedance.py),
on synthetic sweeps with known answers. The 2026-11-13 Randles dummy gave R_s 200.5,
R_ct 3007 ohm and Cdl 1.003 uF with the same code; that file is not in the repo."""
import numpy as np
import pandas as pd
import pytest

from spec_echem import impedance as imp

F = np.logspace(4, 0, 21)          # 10 kHz to 1 Hz, 5 per decade, as the PITT sweeps


def _noisy(z, rel=0.002, seed=0):
    rng = np.random.default_rng(seed)
    return z * (1 + rel * rng.normal(size=z.size)) * np.exp(
        1j * np.radians(0.1) * rng.normal(size=z.size))


def _ladder(f, rs, cdl, rct, cmu):
    """Rs + [Cdl || (Rct + Cmu)]: a blocking capacitance behind a resistance."""
    w = 2 * np.pi * f
    return rs + 1.0 / (1j * w * cdl + 1.0 / (rct + 1.0 / (1j * w * cmu)))


def test_c_parallel_is_c_at_every_frequency_where_minus_one_over_wz_diverges():
    """The reason C' is the estimator: on R || C, -1/(wZ'') read 3150 uF at 1 Hz
    for the 1 uF dummy."""
    z = imp.z_randles(F, 200.0, 3010.0, 1e-6)
    cp, cs = imp.capacitance_spectrum(F, z, 200.0)
    assert cp == pytest.approx(np.full(F.size, 1e-6), rel=1e-9)
    assert cs[-1] > 1e3 * 1e-6


def test_the_randles_fit_recovers_the_dummy_within_its_uncertainty():
    true = {"rs": 200.0, "rct": 3010.0, "cdl": 1e-6}
    fit = imp.fit_impedance(F, _noisy(imp.z_randles(F, **true)), "randles")
    assert fit["status"] == "ok" and not fit["concerns"]
    for name, value in true.items():
        assert abs(fit["params"][name] - value) < 3 * fit["sd"][name]
        assert fit["params"][name] == pytest.approx(value, rel=0.01)
    assert fit["rel_rms"] < 0.005


def test_a_parameter_the_sweep_cannot_see_is_marked_not_hidden():
    """A 1 pF double layer is invisible below 10 kHz behind 3 kohm, so the sweep is a
    plain 3210 ohm resistor and cannot say how it splits into Rs and Rct. The values
    are returned, the total is right, and both undetermined ones are marked."""
    z = _noisy(imp.z_randles(F, 200.0, 3010.0, 1e-12))
    fit = imp.fit_impedance(F, z, "randles")
    assert fit["status"] == "needs_review"
    assert any(c.startswith("cdl ") for c in fit["concerns"])
    assert any(c.startswith("rct ") for c in fit["concerns"])
    assert fit["params"]["rs"] + fit["params"]["rct"] == pytest.approx(3210.0, rel=0.005)


def test_too_few_frequencies_did_not_converge():
    fit = imp.fit_impedance(F[:1], imp.z_randles(F[:1], 200.0, 3010.0, 1e-6))
    assert fit["status"] == "did_not_converge" and "params" not in fit


def test_the_plateau_is_reached_only_well_below_the_knee():
    """Rct*Cmu = 1 s: the knee is at 0.16 Hz. Swept to 0.01 Hz C' reaches Cdl + Cmu;
    stopped at 1 Hz it is still rising and is a lower bound, and says so."""
    rs, cdl, rct, cmu = 100.0, 1e-6, 1e3, 1e-3
    deep = np.logspace(4, -2, 31)
    low = imp.low_frequency_capacitance(deep, _ladder(deep, rs, cdl, rct, cmu), rs)
    assert low["reached"] and not any("plateau" in c for c in low["concerns"])
    assert low["c_lf"] == pytest.approx(cdl + cmu, rel=0.05)

    short = imp.low_frequency_capacitance(F, _ladder(F, rs, cdl, rct, cmu), rs)
    assert not short["reached"]
    assert short["c_lf"] < cdl + cmu                           # a lower bound
    assert any("lower bound" in c for c in short["concerns"])


def test_c_parallel_never_rises_with_frequency():
    rs = 100.0
    cp, _cs = imp.capacitance_spectrum(F, _ladder(F, rs, 1e-6, 1e3, 1e-3), rs)
    # F runs high to low, so C' must never fall along the array.
    assert np.all(np.diff(cp) >= -1e-9 * cp.max())


def test_the_double_layer_comes_from_the_flat_high_frequency_side():
    rs, cdl = 100.0, 7e-6
    f = np.logspace(5, -1, 31)
    high = imp.high_frequency_capacitance(f, _ladder(f, rs, cdl, 1e4, 1e-3), rs)
    assert high["cdl"] == pytest.approx(cdl, rel=0.05)


def test_a_small_low_frequency_angle_is_raised_with_its_uncertainty():
    """On the dummy at 1 Hz the phase of Z - Rs is under 1 deg and C' is 0.89 +/-
    0.10 uF: the number stays, with the reason and the size of the doubt."""
    z = imp.z_randles(F, 200.0, 3010.0, 1e-6)
    low = imp.low_frequency_capacitance(F, z, 200.0)
    assert abs(low["phase_deg"]) < imp.LF_RESISTIVE_PHASE_DEG
    assert any(c.startswith("resistive") for c in low["concerns"])
    assert low["c_lf_u"] > 0.05                  # ~10%, against ~2% at 10 kHz


def test_rs_is_an_upper_bound_when_the_sweep_stops_too_low():
    f = np.logspace(2, 0, 11)                                  # stops at 100 Hz
    frame = pd.DataFrame({"zfreq": f, "zreal": imp.z_randles(f, 200.0, 3010.0, 1e-6).real,
                          "zimag": imp.z_randles(f, 200.0, 3010.0, 1e-6).imag})
    sweep = imp.analyse_sweep(frame)
    assert sweep["rs"] > 200.0
    assert any("high-frequency limit not reached" in c for c in sweep["concerns"])


def test_a_sweep_uses_the_measured_frequency_and_skips_failed_rows():
    z = imp.z_randles(F, 200.0, 3010.0, 1e-6)
    frame = pd.DataFrame({"frequency_set": F, "zfreq": F * 1.009, "zreal": z.real,
                          "zimag": z.imag, "ok": np.r_[1, 0, np.ones(F.size - 2)]})
    frame.loc[1, ["zreal", "zimag"]] = [1e6, -1e6]      # finite: only `ok` says no
    f, _z = imp.sweep_arrays(frame)
    assert f.size == F.size - 1 and f[0] == pytest.approx(F[0] * 1.009)
    sweep = imp.analyse_sweep(frame)
    assert "1 frequency without a value" in sweep["concerns"]
    assert sweep["randles"]["params"]["cdl"] == pytest.approx(1e-6 / 1.009, rel=0.01)


def test_the_uncertainty_of_c_parallel_matches_a_monte_carlo():
    rs = 100.0
    z = _ladder(F, rs, 1e-6, 1e3, 1e-3)
    rng = np.random.default_rng(3)
    draws = np.array([
        imp.capacitance_spectrum(F, z * (1 + imp.Z_MOD_REL_U * rng.normal(size=F.size))
                                 * np.exp(1j * np.radians(imp.Z_PHASE_U_DEG)
                                          * rng.normal(size=F.size)), rs)[0]
        for _ in range(4000)])
    spread = draws.std(axis=0) / np.abs(draws.mean(axis=0))
    assert imp.capacitance_u(F, z, rs) == pytest.approx(spread, rel=0.15)
