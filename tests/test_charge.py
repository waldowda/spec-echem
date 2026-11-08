"""Charge, carrier density, bleach and the equilibrium checks (2026-11-06)."""
import numpy as np
import pytest

from spec_echem.charge import (E_CHARGE_C, analyse_step, bleach_fraction,
                               block_sem, charge_density, film_rel_u,
                               first_sample_delays, head_charge_max,
                               carrier_density, integrate, integrate_every_other,
                               reversibility, reversibility_u, tail_drift,
                               two_sig_fig_u)

T = np.arange(0.0, 60.0 + 1e-9, 0.1)


def test_a_constant_current_integrates_to_i_times_t_and_is_all_leak():
    step = analyse_step("Doping 0", 0.4, "doping", T, np.full(T.size, 2e-6))
    assert step.q_raw == pytest.approx(2e-6 * 60.0)
    assert step.leak_current == pytest.approx(2e-6)
    # "All leak" (0) is one end of the background range, so it lies within +- q_u.
    assert step.q_raw - step.leak_current * step.duration == pytest.approx(0.0, abs=1e-15)
    assert abs(step.q_corrected) <= step.q_u * (1 + 1e-9)


def test_a_decay_on_a_leak_gives_back_the_decay_charge():
    i0, tau, leak = 50e-6, 2.0, 0.3e-6
    i = i0 * np.exp(-T / tau) + leak
    step = analyse_step("Doping 0", 0.4, "doping", T, i)
    assert step.q_raw == pytest.approx(i0 * tau + leak * 60.0, rel=1e-3)
    # Midpoint of "leak all along" and "leak grew from zero", +- half the gap; the
    # true decay charge (a constant leak) sits at the edge of that range.
    assert step.q_corrected == pytest.approx(i0 * tau + 0.25 * leak * 60.0, rel=1e-3)
    assert step.u_background == pytest.approx(0.25 * leak * 60.0, rel=1e-3)
    assert abs(step.q_corrected - i0 * tau) <= step.q_u * 1.01
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


def test_charge_density_is_carrier_density_times_e():
    # 1 mC into 1e-5 cm^3 = 100 C/cm^3.
    assert charge_density(1e-3, 1.0, 100.0) == pytest.approx(100.0)
    assert charge_density(-1e-3, 1.0, 100.0) == pytest.approx(100.0)
    assert charge_density(1e-3, 1.0, 0.0) is None
    assert charge_density(1e-3, 1.0, 100.0) == pytest.approx(
        E_CHARGE_C * carrier_density(1e-3, 1.0, 100.0))


# --- uncertainties (2026-11-08) ---------------------------------------------------

def test_two_significant_figures_is_half_a_unit_in_the_second():
    assert two_sig_fig_u(100.0) == pytest.approx(5.0)
    assert two_sig_fig_u(150.0) == pytest.approx(5.0)
    assert two_sig_fig_u(2.4) == pytest.approx(0.05)
    assert two_sig_fig_u(0.0) == 0.0


def test_film_volume_uncertainty_adds_in_quadrature():
    # 100 +- 5 nm and 2.4 +- 0.05 cm2: 5% and 2.08% -> 5.42%.
    assert film_rel_u(100.0, 2.4) == pytest.approx(np.hypot(0.05, 0.05 / 2.4))
    assert film_rel_u(100.0, 2.4, 10.0, 0.24) == pytest.approx(np.hypot(0.1, 0.1))
    assert film_rel_u(0.0, 2.4) is None


def test_a_background_that_grows_from_zero_is_inside_the_range():
    """The other end of the background model: a leak ramping up over the step."""
    i0, tau, leak = 50e-6, 2.0, -1.5e-6
    i = i0 * np.exp(-T / tau) + leak * T / 60.0
    step = analyse_step("Doping 0", 0.0, "doping", T, i)
    assert abs(step.q_corrected - i0 * tau) <= step.q_u * 1.05
    # ...and the slope it ends on is flagged: 1.5 uA/min over 60 s is 45 uC = 45% of Q.
    assert not step.at_equilibrium
    assert "current at the end still changing" in step.reason


def test_a_steady_background_is_not_flagged_by_the_trend_check():
    i = 50e-6 * np.exp(-T / 2.0) - 1.5e-6
    step = analyse_step("Doping 0", 0.0, "doping", T, i)
    assert "still changing" not in step.reason
    assert "tail current trend" in step.reason


def test_the_integration_check_compares_every_other_sample():
    t = np.array([0.0, 1.0, 2.0, 3.0])
    i = np.array([4.0, 2.0, 1.0, 1.0])
    # Every other sample, ending on the last: 0, 2, 3 -> (4+1)/2*2 + (1+1)/2*1 = 6.
    assert integrate_every_other(t, i) == pytest.approx(6.0)
    assert integrate(t, i) == pytest.approx(3.0 + 1.5 + 1.0)
    step = analyse_step("Doping 0", 0.4, "doping", T, 50e-6 * np.exp(-T / 0.3))
    assert step.u_integration > 0
    assert step.u_integration == pytest.approx(
        abs(step.q_raw - integrate_every_other(T, 50e-6 * np.exp(-T / 0.3))))


def test_noise_enters_through_the_tail_scatter():
    rng = np.random.default_rng(3)
    base = 50e-6 * np.exp(-T / 2.0)
    quiet = analyse_step("Doping 0", 0.4, "doping", T, base)
    noisy = analyse_step("Doping 0", 0.4, "doping", T, base + rng.normal(0, 1e-7, T.size))
    assert quiet.u_noise == pytest.approx(0.0, abs=1e-15)
    # 2 sigma x dt x sqrt(N) on the integral = 2e-7 * 0.1 * 24.5 = 4.9e-7 C, and
    # 2 sigma / sqrt(51) x 0.75 x 60 s on the background mean = 1.26e-6 C:
    # 1.35e-6 C in quadrature.
    assert noisy.u_noise == pytest.approx(1.35e-6, rel=0.2)


def test_head_charge_runs_the_first_decay_back_to_switch_on():
    tau, delay = 0.5, 0.1
    i = 1e-3 * np.exp(-T / tau)
    # The exact charge in the 0.1 s before the first sample, for that exponential.
    assert head_charge_max(T, i, delay) == pytest.approx(
        1e-3 * tau * (np.exp(delay / tau) - 1), rel=1e-6)
    assert head_charge_max(T, -i, delay) < 0               # dedoping keeps its sign
    assert head_charge_max(T, i, None) is None             # no timing, no estimate
    flat = np.full(T.size, 2e-6)                           # not a decay: rectangle
    assert head_charge_max(T, flat, delay) == pytest.approx(2e-7)
    step = analyse_step("Doping 0", 0.4, "doping", T, i, head_delay_s=delay)
    assert step.q_head_max == pytest.approx(head_charge_max(T, i, delay))
    assert step.q_raw == pytest.approx(integrate(T, i))    # never added to Q


def test_densities_carry_the_charge_uncertainty_only():
    i = 50e-6 * np.exp(-T / 2.0) - 1e-6
    step = analyse_step("Doping 0", 0.4, "doping", T, i, area_cm2=1.0,
                        thickness_nm=100.0)
    assert step.charge_density_u == pytest.approx(step.q_u / 1e-5)
    assert step.carriers_u == pytest.approx(step.q_u / (E_CHARGE_C * 1e-5))


def test_block_sem_is_wider_than_the_plain_sem_on_a_drifting_tail():
    rng = np.random.default_rng(0)
    v = np.linspace(0.0, 0.01, 50) + rng.normal(0, 0.001, 50)
    plain = np.std(v, ddof=1) / np.sqrt(v.size)
    assert block_sem(v) > plain
    assert block_sem([1.0]) is None


def test_bleach_uncertainty_from_the_tail_and_a0():
    rng = np.random.default_rng(5)
    a = 0.6 + rng.normal(0, 0.002, T.size)
    step = analyse_step("Doping 0", 0.4, "doping", T, 50e-6 * np.exp(-T / 1.0),
                        absorbance_time=T, absorbance=a, a0=1.0)
    tail = a[T >= T[-1] - 5.0]
    assert step.bleach_u == pytest.approx(2 * block_sem(tail))
    with_a0 = analyse_step("Doping 0", 0.4, "doping", T, 50e-6 * np.exp(-T / 1.0),
                           absorbance_time=T, absorbance=a, a0=1.0, a0_u=0.01)
    assert with_a0.bleach_u == pytest.approx(
        np.hypot(2 * block_sem(tail), step.a_end * 0.01))


def test_reversibility_uncertainty_combines_both_charges():
    i = 50e-6 * np.exp(-T / 2.0) - 1e-6
    dope = analyse_step("Doping 0", 0.4, "doping", T, i)
    dedope = analyse_step("Dedoping 0", 0.4, "dedoping", T, -0.8 * i)
    r = reversibility(dope, dedope)
    assert reversibility_u(dope, dedope) == pytest.approx(
        r * np.hypot(dope.q_u / dope.q_corrected, dedope.q_u / dedope.q_corrected))
    assert reversibility_u(dope, None) is None


def test_first_sample_delays_come_from_the_run_log():
    log = ("2026-11-05 12:56:12,779  INFO    Doping 0 timing, from cell ON: "
           "Measure() returned +0.000 s | trigger edge +0.020 s | first Ei sample "
           "+0.095 s.\n"
           "2026-11-05 12:57:26,693  INFO    Dedoping 10 timing, from cell ON: "
           "trigger edge +0.020 s | first Ei sample +0.140 s.\n"
           "2026-11-05 12:58:00,000  INFO    Doping 1 timing, from cell ON: "
           "trigger edge +0.020 s.\n")
    assert first_sample_delays(log) == {"Doping 0": 0.095, "Dedoping 10": 0.14}
    assert first_sample_delays("") == {}


def test_the_charge_uncertainty_is_its_three_parts_in_quadrature():
    rng = np.random.default_rng(7)
    i = 50e-6 * np.exp(-T / 0.3) - 1e-6 + rng.normal(0, 1e-7, T.size)
    step = analyse_step("Doping 0", 0.4, "doping", T, i)
    assert min(step.u_background, step.u_integration, step.u_noise) > 0
    assert step.q_u == pytest.approx(np.sqrt(step.u_background ** 2 + step.u_integration
                                             ** 2 + step.u_noise ** 2))
    # The noise part by hand: 2 sigma on the integral and on the background mean.
    tail = T >= T[-1] - 5.0
    line = np.polyfit(T[tail], i[tail], 1)
    sigma = np.std(i[tail] - np.polyval(line, T[tail]), ddof=2)
    assert step.u_noise == pytest.approx(np.hypot(
        2 * sigma * 0.1 * np.sqrt(T.size), 2 * sigma / np.sqrt(tail.sum()) * 0.75 * 60.0),
        rel=1e-6)
