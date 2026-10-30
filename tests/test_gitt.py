"""GITT: the plan, its limits, the step-end rule, and the loop on a fake RC cell.

The clock is simulated (as in test_pitt_run.py) so a titration of many minutes runs
in milliseconds and every timing assertion is exact.
"""
import math

import numpy as np
import pytest

from spec_echem.acquisition import acquire_pitt
from spec_echem.fakes import FakeGittPotentiostat, FakeSpectrometer
from spec_echem.gitt import (END_E_LIMIT, END_RELAXED, RELAX_SKIP_S, ROLE_REST,
                             ROLE_TITRATION, GittStep, GittStepEnd,
                             autolab_offset_concern, gitt_duration_bounds,
                             gitt_plan, gitt_planned_charge_c, gitt_problems,
                             gitt_range_problem, gitt_spectrum_due, gitt_step_end_for)
from spec_echem.pitt import END_ABORTED, END_FIXED, END_MAX_HOLD, END_STOPPED
from spec_echem.settings import DEFAULT_SETTINGS


def settings(**over):
    s = dict(DEFAULT_SETTINGS)
    s.update(gitt_enabled=True, gitt_current_ua=10.0, gitt_pulse_s=2.0,
             gitt_n_pulses=3, gitt_relax_s=5.0, gitt_initial_rest_s=0.0,
             gitt_e_min_v=-1.0, gitt_e_max_v=1.0, gitt_max_charge_mc=1.0,
             gitt_relax_settle_enabled=False, gitt_relax_dvdt_uv_s=10.0,
             gitt_relax_window_s=2.0, gitt_fast_s=1.0, gitt_slow_interval_s=0.5,
             chrono_delta_time=0.1)
    s.update(over)
    return s


# --- the plan and what can stop it at Start -----------------------------------

def test_the_plan_is_n_identical_titrations_after_an_optional_rest():
    plan = gitt_plan(settings(gitt_initial_rest_s=30.0))
    assert [st.role for st in plan] == [ROLE_REST] + [ROLE_TITRATION] * 3
    assert plan[0].current == 0.0 and plan[0].pulse_s == 0.0
    assert [st.current for st in plan[1:]] == pytest.approx([10e-6] * 3)
    assert [st.index for st in plan] == [0, 1, 2, 3]
    assert [st.role for st in gitt_plan(settings())] == [ROLE_TITRATION] * 3


def test_a_negative_current_dedopes_and_is_kept_as_given():
    assert gitt_plan(settings(gitt_current_ua=-2.5))[0].current == pytest.approx(-2.5e-6)


def test_a_step_reports_its_current_not_a_potential():
    assert GittStep(0, 1e-6, 10.0, ROLE_TITRATION).fields() == {
        "current_set_A": 1e-6, "pulse_s": 10.0}


def test_the_planned_charge_is_exact():
    assert gitt_planned_charge_c(settings()) == pytest.approx(10e-6 * 2.0 * 3)


def test_the_duration_bounds_include_the_rest():
    lo, hi = gitt_duration_bounds(settings(gitt_initial_rest_s=10.0))
    assert lo == hi == pytest.approx(10.0 + 3 * (2.0 + 5.0))
    lo, hi = gitt_duration_bounds(settings(gitt_relax_settle_enabled=True))
    assert lo == pytest.approx(3 * (2.0 + RELAX_SKIP_S + 2.0))
    assert hi == pytest.approx(3 * 7.0)


def test_the_defaults_can_run():
    assert gitt_problems(dict(DEFAULT_SETTINGS)) == []


@pytest.mark.parametrize("over, words", [
    (dict(gitt_current_ua=0.0), "must not be 0"),
    (dict(gitt_pulse_s=0.0), "pulse must last"),
    (dict(gitt_relax_s=0.0), "relaxation must last"),
    (dict(gitt_n_pulses=0), "at least one pulse"),
    (dict(gitt_e_min_v=0.5, gitt_e_max_v=0.5), "window is empty"),
    (dict(gitt_max_charge_mc=0.0), "cap must be larger"),
    (dict(gitt_slow_interval_s=0.05), "slow spectrum interval"),
    (dict(gitt_relax_settle_enabled=True, gitt_relax_dvdt_uv_s=0.0), "dE/dt threshold"),
    (dict(gitt_relax_settle_enabled=True, gitt_relax_window_s=10.0), "does not fit"),
])
def test_each_impossible_setting_is_named(over, words):
    problems = gitt_problems(settings(**over))
    assert any(words in p for p in problems), problems


def test_a_units_slip_is_caught_by_the_charge_cap():
    """10 uA typed as 10 mA: 60 mC planned against a 1 mC cap."""
    problems = gitt_problems(settings(gitt_current_ua=10_000.0))
    assert any("more than the 1 mC cap" in p for p in problems), problems
    assert not gitt_problems(settings(gitt_max_charge_mc=0.06))      # exactly at it


def test_the_settle_rule_settings_are_ignored_when_it_is_off():
    assert gitt_problems(settings(gitt_relax_dvdt_uv_s=0.0,
                                  gitt_relax_window_s=99.0)) == []


def test_a_pulse_larger_than_the_range_is_a_problem():
    assert gitt_range_problem(2e-3, 1e-3)
    assert gitt_range_problem(-2e-3, 1e-3)
    assert gitt_range_problem(1e-3, 1e-3) is None
    assert gitt_range_problem(1e-3, None) is None


def test_the_autolab_offset_warns_against_the_pulse_and_the_relaxation():
    # CR11_100uA: ~16 nA at 0 A.
    assert autolab_offset_concern(10e-6, 1e-4, 10.0, 10.0) is None
    # 8% of the pulse, with a relaxation too short to drain much (0.8%).
    near = autolab_offset_concern(0.2e-6, 1e-4, 10.0, 1.0)
    assert near and "of the pulse" in near
    # A big pulse, but a relaxation 100x the pulse drains 16% of its charge.
    drain = autolab_offset_concern(10e-6, 1e-4, 1.0, 100.0)
    assert drain and "16.0%" in drain
    assert autolab_offset_concern(0.0, 1e-4, 1.0, 1.0) is None
    assert autolab_offset_concern(1e-6, None, 1.0, 1.0) is None


# --- the step-end rule ---------------------------------------------------------

def test_a_fixed_relaxation_ends_at_pulse_plus_relax():
    rule = GittStepEnd(pulse_s=2.0, relax_s=5.0, e_min=-1, e_max=1)
    assert rule.feed(6.9, 0.0, 0.1) is None
    assert rule.feed(7.0, 0.0, 0.1) == END_FIXED


def test_leaving_the_window_ends_the_step_at_once_even_mid_pulse():
    rule = GittStepEnd(pulse_s=2.0, relax_s=5.0, e_min=-0.5, e_max=0.5)
    assert rule.feed(0.5, 1e-6, 0.49) is None
    assert rule.feed(0.6, 1e-6, 0.51) == END_E_LIMIT
    assert rule.feed(0.7, 1e-6, 0.0) == END_E_LIMIT              # latched
    low = GittStepEnd(pulse_s=2.0, relax_s=5.0, e_min=-0.5, e_max=0.5)
    assert low.feed(0.1, -1e-6, -0.51) == END_E_LIMIT


def test_a_nan_potential_is_skipped_not_judged():
    rule = GittStepEnd(pulse_s=2.0, relax_s=5.0, e_min=-0.5, e_max=0.5)
    assert rule.feed(0.1, float("nan"), float("nan")) is None
    assert rule.feed(0.2, 1e-6, None) is None


def _relax_rule(**kw):
    kw.setdefault("settle", True)
    return GittStepEnd(pulse_s=2.0, relax_s=60.0, e_min=-1, e_max=1,
                       dvdt_v_s=10e-6, window_s=2.0, **kw)


def test_a_relaxed_potential_ends_the_step_once_dedt_is_small_for_long_enough():
    rule = _relax_rule()
    t, reason = 0.0, None
    while reason is None and t < 60:
        t = round(t + 0.1, 6)
        # relaxing as exp(-t/1 s): dE/dt < 10 uV/s after ~9 s of relaxation
        v = 0.3 + (0.05 * math.exp(-(t - 2.0)) if t >= 2.0 else 0.0)
        reason = rule.feed(t, 0.0, v)
    assert reason == END_RELAXED
    assert 2.0 + RELAX_SKIP_S + 2.0 < t < 15.0
    assert abs(rule.slope) <= 10e-6


def test_a_drifting_relaxation_runs_to_its_maximum_and_says_so():
    rule = _relax_rule()
    t, reason = 0.0, None
    while reason is None:
        t = round(t + 0.1, 6)
        reason = rule.feed(t, 0.0, 0.3 + 100e-6 * t)                # 100 uV/s forever
    assert reason == END_MAX_HOLD and t == pytest.approx(62.0)


def test_one_quiet_sample_is_not_enough():
    """SETTLE_SAMPLES consecutive fits below the threshold, as the PITT cutoff."""
    rule = _relax_rule(settle_samples=5)
    t = 0.0
    for _ in range(int((2.0 + RELAX_SKIP_S + 2.0) / 0.1) + 3):
        t = round(t + 0.1, 6)
        assert rule.feed(t, 0.0, 0.3) is None or t >= 5.4


def test_the_pulse_itself_never_counts_as_relaxed():
    """A flat potential during the pulse (a resistor) must not end the step early."""
    rule = GittStepEnd(pulse_s=20.0, relax_s=60.0, e_min=-1, e_max=1, settle=True,
                       dvdt_v_s=10e-6, window_s=2.0)
    t = 0.0
    while t < 20.0 + RELAX_SKIP_S + 2.0:
        t = round(t + 0.1, 6)
        assert rule.feed(t, 1e-6, 0.3) is None


def test_the_rule_records_the_last_potential_and_peak_current():
    rule = GittStepEnd(pulse_s=1.0, relax_s=1.0, e_min=-1, e_max=1)
    rule.feed(0.5, 3e-6, 0.2)
    rule.feed(1.5, 0.0, 0.1)
    assert rule.peak == pytest.approx(3e-6)
    assert rule.row_fields()["e_last_V"] == pytest.approx(0.1)
    assert rule.unresolved is None


def test_the_rest_step_relaxes_for_its_own_time():
    s = settings(gitt_initial_rest_s=30.0)
    rest, first = gitt_plan(s)[:2]
    assert gitt_step_end_for(rest, s).relax_s == 30.0
    assert gitt_step_end_for(rest, s).pulse_s == 0.0
    assert gitt_step_end_for(first, s).relax_s == 5.0


def test_a_rest_too_short_for_the_window_just_runs_its_time():
    s = settings(gitt_initial_rest_s=1.0, gitt_relax_settle_enabled=True)
    assert gitt_step_end_for(gitt_plan(s)[0], s).settle is False


# --- when a spectrum is due ----------------------------------------------------

STEP = GittStep(0, 1e-6, 10.0, ROLE_TITRATION)


def test_spectra_run_at_full_rate_after_the_pulse_starts_and_after_it_ends():
    due = lambda t, last: gitt_spectrum_due(STEP, t, last, 0.1, 2.0, 1.0)  # noqa: E731
    assert due(0.0, None)
    assert due(1.0, 0.9)                         # in the first 2 s of the pulse
    assert not due(5.0, 4.9)                     # mid-pulse: slow
    assert due(5.0, 4.0)
    assert due(10.05, 9.5)                       # the first after the pulse ends
    assert due(10.01, 9.99)                      # ...even a tick after the last one
    assert due(11.0, 10.9)                       # full rate again after the switch
    assert not due(13.0, 12.9)


def test_the_rest_step_samples_like_a_pitt_step():
    rest = GittStep(0, 0.0, 0.0, ROLE_REST)
    assert gitt_spectrum_due(rest, 1.0, 0.9, 0.1, 2.0, 1.0)
    assert not gitt_spectrum_due(rest, 5.0, 4.9, 0.1, 2.0, 1.0)


# --- the loop, on a fake RC cell ----------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class ClockedSpectrometer(FakeSpectrometer):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def set_trigger_mode(self, mode, measconfig=None):
        pass

    def measure(self, abort_event=None, on_armed=None):
        if abort_event is not None and abort_event.is_set():
            return None
        if on_armed is not None:
            on_armed()
        self.clock.sleep(0.03)
        return self.clock() * 1e5, self._window(self._synthetic_spectrum())


class CostlyPot(FakeGittPotentiostat):
    def pitt_sample(self):
        self.clock.sleep(0.05)
        return super().pitt_sample()


def run(s, stop_event=None, abort_event=None, **cell):
    clock = Clock()
    pot = CostlyPot(clock=clock, **cell)
    record = acquire_pitt(ClockedSpectrometer(clock), pot, gitt_plan(s), s,
                          clock=clock, sleep=clock.sleep, kind="gitt",
                          stop_event=stop_event, abort_event=abort_event)
    return record, pot


def test_a_titration_runs_every_step_with_the_cell_held_on():
    record, pot = run(settings(gitt_initial_rest_s=3.0))
    assert record.completed
    assert [st["end_reason"] for st in record.steps] == [END_FIXED] * 4
    assert [ev for ev, _ in pot.cell_events] == ["on", "off"]
    assert [c for c, _p, _t in pot.steps] == pytest.approx([0.0, 10e-6, 10e-6, 10e-6])
    rows = record.steps
    assert rows[1]["current_set_A"] == pytest.approx(10e-6)
    assert rows[1]["pulse_s"] == 2.0 and rows[1]["role"] == ROLE_TITRATION
    assert "potential_set" not in rows[1] and "cutoff_unresolved" not in rows[1]
    for st in rows[1:]:
        assert st["hold_s"] == pytest.approx(7.0, abs=0.11)


def test_an_rc_dummy_ramps_at_i_over_c_and_drops_by_ir_at_the_pulse_end():
    """The check the bench will repeat on a capacitor dummy: during a pulse
    dE/dt = I / C, and at its end E steps down by I x R_s and then holds."""
    r_s, c, i = 1e3, 1e-3, 10e-6
    record, _pot = run(settings(gitt_n_pulses=1), r_series=r_s, c=c)
    t = np.asarray(record.echem_t_in_step)
    v = np.asarray(record.echem_potential)
    pulse = (t > 0.2) & (t < 1.8)
    slope = np.polyfit(t[pulse], v[pulse], 1)[0]
    assert slope == pytest.approx(i / c, rel=0.02)
    relax = t > 2.2
    assert np.ptp(v[relax]) < 1e-9                         # a capacitor holds
    assert v[relax][0] == pytest.approx(i * 2.0 / c, rel=0.03)


def test_a_randles_dummy_relaxes_back_toward_its_start():
    record, _pot = run(settings(gitt_n_pulses=1, gitt_relax_s=20.0),
                       r_series=100.0, c=1e-3, r_parallel=1e3)        # tau 1 s
    t = np.asarray(record.echem_t_in_step)
    v = np.asarray(record.echem_potential)
    # I (R_s + R_p (1 - e^-2)) at the pulse's end
    assert v[(t > 1.8) & (t < 2.0)].max() == pytest.approx(
        10e-6 * (100.0 + 1e3 * (1 - math.exp(-2.0))), rel=0.02)
    assert v[t > 21.0].max() < 1e-4


def test_the_settle_rule_ends_relaxations_early_on_a_randles_dummy():
    s = settings(gitt_relax_s=60.0, gitt_relax_settle_enabled=True)
    record, _pot = run(s, r_series=100.0, c=1e-3, r_parallel=1e3)
    assert [st["end_reason"] for st in record.steps] == [END_RELAXED] * 3
    assert all(st["hold_s"] < 20.0 for st in record.steps)
    assert all(abs(st["relax_dvdt_V_s"]) <= 10e-6 for st in record.steps)


def test_leaving_the_window_stops_the_run_and_switches_the_cell_off():
    """A capacitor charged past the window: E = I t / C crosses 0.25 V mid-pulse."""
    record, pot = run(settings(gitt_e_max_v=0.25, gitt_current_ua=100.0,
                               gitt_max_charge_mc=10.0), r_series=10.0, c=1e-3)
    assert not record.completed
    assert [st["end_reason"] for st in record.steps] == [END_FIXED, END_E_LIMIT]
    assert record.steps[-1]["e_last_V"] > 0.25
    assert [ev for ev, _ in pot.cell_events] == ["on", "off"]
    assert len(pot.steps) == 2                            # the third never applied


def test_stop_keeps_the_interrupted_step():
    import threading
    stop = threading.Event()
    stop.set()
    record, pot = run(settings(), stop_event=stop)
    assert [st["end_reason"] for st in record.steps] == [END_STOPPED]
    assert pot.cell_events[-1][0] == "off"


def test_abort_before_anything_ran_leaves_an_empty_record():
    import threading
    abort = threading.Event()
    abort.set()
    record, _pot = run(settings(), abort_event=abort)
    assert record.steps == [] and not record.completed
    assert END_ABORTED == "aborted"


def test_the_instruments_own_stop_ends_the_run_even_inside_the_window():
    """The Gamry's CiivCurve can end a pulse before any sample beyond the window
    reaches the loop; the driver's gitt_e_stopped() must end the run on its own."""
    class Stopped(CostlyPot):
        def gitt_e_stopped(self):
            return self.clock() - self._t_on > 1.0

    clock = Clock()
    pot = Stopped(clock=clock)
    s = settings()
    record = acquire_pitt(ClockedSpectrometer(clock), pot, gitt_plan(s), s,
                          clock=clock, sleep=clock.sleep, kind="gitt")
    assert [st["end_reason"] for st in record.steps] == [END_E_LIMIT]
    assert record.steps[0]["hold_s"] < 1.2 and not record.completed
    assert pot.cell_events[-1][0] == "off"
