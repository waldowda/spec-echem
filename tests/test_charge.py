"""Charge, carrier density, bleach and the equilibrium checks (2026-11-06)."""
import numpy as np
import pytest

from spec_echem.charge import (E_CHARGE_C, analyse_step, bleach_fraction,
                               carrier_density, integrate, reversibility, tail_drift)

T = np.arange(0.0, 60.0 + 1e-9, 0.1)


def test_a_constant_current_integrates_to_i_times_t_and_is_all_leak():
    step = analyse_step("Doping 0", 0.4, "doping", T, np.full(T.size, 2e-6))
    assert step.q_raw == pytest.approx(2e-6 * 60.0)
    assert step.leak_current == pytest.approx(2e-6)
    assert step.q_corrected == pytest.approx(0.0, abs=1e-15)


def test_a_decay_on_a_leak_gives_back_the_decay_charge():
    i0, tau, leak = 50e-6, 2.0, 0.3e-6
    i = i0 * np.exp(-T / tau) + leak
    step = analyse_step("Doping 0", 0.4, "doping", T, i)
    assert step.q_raw == pytest.approx(i0 * tau + leak * 60.0, rel=1e-3)
    assert step.q_corrected == pytest.approx(i0 * tau, rel=1e-3)
    assert step.at_equilibrium, step.reason


def test_carrier_density_by_hand():
    # 1 mC into 1 cm^2 x 100 nm = 1e-5 cm^3.
    assert carrier_density(1e-3, 1.0, 100.0) == pytest.approx(1e-3 / (E_CHARGE_C * 1e-5))
    assert carrier_density(-1e-3, 1.0, 100.0) > 0          # dedoping charge is negative
    assert carrier_density(1e-3, 0.0, 100.0) is None       # no film size, no density


def test_bleach_is_the_fraction_of_the_neutral_absorbance_lost():
    assert bleach_fraction(0.6, 1.0) == pytest.approx(0.4)
    assert bleach_fraction(1.2, 1.0) == pytest.approx(-0.2)  # a band that grew
    assert bleach_fraction(0.6, None) is None


def test_a_step_cut_short_is_flagged_by_both_checks():
    tau = 40.0
    i = 50e-6 * np.exp(-T / tau)
    a = 1.0 - 0.4 * (1 - np.exp(-T / tau))
    step = analyse_step("Doping 0", 0.4, "doping", T, i, absorbance_time=T,
                        absorbance=a, a0=1.0, mean_taus={"absorbance": tau})
    assert not step.at_equilibrium
    assert "under 5 x absorbance tau" in step.reason
    assert "absorbance still moving" in step.reason
    assert "current still moving" in step.reason
    # Flagged, not withheld: every number is still there.
    assert step.q_raw > 0 and step.bleach is not None


def test_a_settled_step_passes_and_says_which_checks_it_passed():
    tau = 1.0
    a = 1.0 - 0.4 * (1 - np.exp(-T / tau))
    step = analyse_step("Doping 0", 0.4, "doping", T, 50e-6 * np.exp(-T / tau),
                        absorbance_time=T, absorbance=a, a0=1.0,
                        mean_taus={"absorbance": tau})
    assert step.at_equilibrium
    assert step.bleach == pytest.approx(0.4, abs=1e-6)
    assert "5 tau" in step.reason and "absorbance drift" in step.reason


def test_noise_on_a_flat_tail_is_not_drift():
    rng = np.random.default_rng(1)
    v = 0.5 + rng.normal(0, 0.003, T.size)
    assert tail_drift(T, v, swing=0.4) < 0.02


def test_reversibility_compares_the_corrected_charges():
    i = 50e-6 * np.exp(-T / 2.0)
    dope = analyse_step("Doping 0", 0.4, "doping", T, i)
    dedope = analyse_step("Dedoping 0", 0.4, "dedoping", T, -0.8 * i)
    assert reversibility(dope, dedope) == pytest.approx(0.8, rel=1e-6)
    assert reversibility(dope, None) is None


def test_integrate_is_the_trapezoid_rule():
    assert integrate([0.0, 1.0, 3.0], [0.0, 2.0, 2.0]) == pytest.approx(1.0 + 4.0)


def test_the_tau_check_catches_what_drift_alone_misses():
    """tau = 20 s in a 60 s step: the last 5 s move only ~1.5% of the change, under
    the 2% drift limit, yet the step is only 95% complete. Why there are two checks."""
    tau = 20.0
    a = 1.0 - 0.4 * (1 - np.exp(-T / tau))
    drift_only = analyse_step("Doping 0", 0.4, "doping", T, 50e-6 * np.exp(-T / tau),
                              absorbance_time=T, absorbance=a, a0=1.0)
    with_tau = analyse_step("Doping 0", 0.4, "doping", T, 50e-6 * np.exp(-T / tau),
                            absorbance_time=T, absorbance=a, a0=1.0,
                            mean_taus={"absorbance": tau})
    assert "absorbance still moving" not in drift_only.reason
    assert not with_tau.at_equilibrium
