"""PITT: the step plan, when a step ends, and when a spectrum is due.

Pure logic, so every rule is checked here without an instrument. The drivers only
feed these functions; they do not re-decide any of it.
"""
import pytest

from spec_echem.pitt import (
    END_CUTOFF, END_MAX_HOLD, ROLE_DEDOPE, ROLE_FORWARD, ROLE_RETURN, SETTLE_SAMPLES,
    StepEnd,
    pitt_duration_bounds, pitt_plan, pitt_problems, pitt_step_potential,
    spectrum_due)
from spec_echem.settings import DEFAULT_SETTINGS


def settings(**over):
    s = dict(DEFAULT_SETTINGS)
    s.update(pitt_start_v=-0.5, pitt_stop_v=0.7, pitt_step_mv=10.0,
             pitt_return=False, pitt_end_dedope=False)
    s.update(over)
    return s


# --- the plan --------------------------------------------------------------

def test_the_staircase_reaches_its_stop_and_no_further():
    plan = pitt_plan(settings())
    assert len(plan) == 121                        # -0.5 .. +0.7 in 10 mV
    assert plan[0].potential == -0.5
    assert plan[-1].potential == pytest.approx(0.7)
    assert all(s.role == ROLE_FORWARD for s in plan)
    assert [s.index for s in plan] == list(range(121))


def test_the_stop_is_a_ceiling_never_passed():
    """Rounds DOWN, the rule the doping ladder learned on the bench: rounding to
    nearest once held a film 50 mV past the end the user set."""
    # 0.28 / 0.1 = 2.8: rounding to nearest would add a step at +0.30, past the stop.
    # (0.25 would not catch it -- Python rounds 2.5 to 2.)
    plan = pitt_plan(settings(pitt_start_v=0.0, pitt_stop_v=0.28, pitt_step_mv=100.0))
    assert [s.potential for s in plan] == [0.0, 0.1, 0.2]
    assert max(s.potential for s in plan) <= 0.28


def test_float_error_does_not_drop_the_last_step():
    plan = pitt_plan(settings(pitt_start_v=0.1, pitt_stop_v=0.3, pitt_step_mv=100.0))
    assert [s.potential for s in plan] == [0.1, 0.2, 0.3]     # not 0.30000000000000004


def test_a_start_above_the_stop_runs_downward():
    plan = pitt_plan(settings(pitt_start_v=0.2, pitt_stop_v=0.0, pitt_step_mv=100.0))
    assert [s.potential for s in plan] == [0.2, 0.1, 0.0]


def test_the_return_leg_retraces_without_repeating_the_turn():
    plan = pitt_plan(settings(pitt_start_v=0.0, pitt_stop_v=0.3, pitt_step_mv=100.0,
                              pitt_return=True))
    assert [s.potential for s in plan] == [0.0, 0.1, 0.2, 0.3, 0.2, 0.1, 0.0]
    assert [s.role for s in plan] == [ROLE_FORWARD] * 4 + [ROLE_RETURN] * 3


def test_the_end_dedope_has_its_own_potential_not_the_ladders():
    """Its own setting, defaulting to -0.5 V -- not the doping ladder's dedoping
    potential, which is a different step with its own default (0.0 V)."""
    plan = pitt_plan(settings(pitt_start_v=0.0, pitt_stop_v=0.1, pitt_step_mv=100.0,
                              pitt_end_dedope=True, pitt_end_dedope_v=-0.4,
                              dedoping_potential=0.0))
    assert plan[-1].role == ROLE_DEDOPE
    assert plan[-1].potential == -0.4
    assert plan[-1].index == len(plan) - 1


def test_the_end_dedope_defaults_to_minus_half_a_volt():
    assert DEFAULT_SETTINGS["pitt_end_dedope_v"] == -0.5
    plan = pitt_plan(settings(pitt_end_dedope=True))
    assert plan[-1].potential == -0.5


def test_a_step_potential_is_read_from_the_plan():
    s = settings(pitt_start_v=0.0, pitt_stop_v=0.2, pitt_step_mv=100.0)
    assert pitt_step_potential(s, 1) == pytest.approx(0.1)
    assert pitt_step_potential(s, 3) is None
    assert pitt_step_potential(s, -1) is None


def test_the_duration_bounds_bracket_every_outcome():
    s = settings(pitt_start_v=0.0, pitt_stop_v=0.2, pitt_step_mv=100.0,
                 pitt_min_hold_s=5.0, pitt_max_hold_s=120.0,
                 pitt_end_dedope=True, pitt_end_dedope_time_s=30.0)
    lo, hi = pitt_duration_bounds(s)
    assert (lo, hi) == (3 * 5.0 + 30.0, 3 * 120.0 + 30.0)


def test_stepping_back_down_roughly_doubles_the_time():
    """What the time warning beside the checkbox has to say."""
    up = pitt_duration_bounds(settings())[1]
    both = pitt_duration_bounds(settings(pitt_return=True))[1]
    # 121 steps up, then 120 back -- the turning point is not held twice.
    assert both == pytest.approx(up * 241 / 121)


# --- validation --------------------------------------------------------------

def test_the_defaults_are_runnable():
    assert pitt_problems(settings()) == []


@pytest.mark.parametrize("over, words", [
    ({"pitt_step_mv": 0.0}, "larger than 0 mV"),
    ({"pitt_cutoff_pct": 0.0}, "between 0 and 100%"),
    ({"pitt_cutoff_pct": 100.0}, "between 0 and 100%"),
    ({"pitt_min_hold_s": 200.0, "pitt_max_hold_s": 120.0}, "longer than its maximum"),
    ({"pitt_max_hold_s": 0.0}, "must be positive"),
    ({"pitt_slow_interval_s": 0.01, "chrono_delta_time": 0.1}, "would not slow"),
    ({"pitt_end_dedope": True, "pitt_end_dedope_time_s": 0.0}, "longer than 0 s"),
])
def test_impossible_settings_are_named(over, words):
    problems = pitt_problems(settings(**over))
    assert any(words in p for p in problems), problems


def test_a_high_ceiling_is_the_scientists_call_not_a_problem():
    """Not everything is aqueous, and +0.8 V vs Ag/AgCl can be fine even in water."""
    assert pitt_problems(settings(pitt_stop_v=1.2)) == []


# --- when a step ends --------------------------------------------------------

def _decaying(peak, tau, dt, until):
    import math
    t = 0.0
    while t <= until + 1e-12:
        yield t, peak * math.exp(-t / tau)
        t += dt


def test_a_settling_step_ends_at_the_cutoff():
    end = StepEnd(cutoff_pct=1.0, min_hold_s=5.0, max_hold_s=120.0)
    t_end = None
    for t, i in _decaying(peak=1e-4, tau=2.0, dt=0.05, until=120.0):
        if end.feed(t, i):
            t_end = t
            break
    assert end.reason == END_CUTOFF
    # 1% of the peak is reached at t = tau * ln(100) = 9.21 s, and the step ends once
    # SETTLE_SAMPLES in a row have stayed there: (N - 1) samples later.
    assert t_end == pytest.approx(9.21 + (SETTLE_SAMPLES - 1) * 0.05, abs=0.06)


def test_the_cutoff_waits_for_the_minimum_hold():
    """A fast step must not end at its second sample, before any spectrum."""
    end = StepEnd(cutoff_pct=1.0, min_hold_s=5.0, max_hold_s=120.0)
    for t, i in _decaying(peak=1e-4, tau=0.05, dt=0.05, until=10.0):
        if end.feed(t, i):
            break
    assert end.reason == END_CUTOFF
    assert t >= 5.0


def test_a_current_that_never_decays_ends_at_the_max_hold():
    """The UDC4's Randles side: its 0.2 ms transient is invisible at 50 ms sampling,
    so every step looks like a DC floor and only the cap ends it."""
    end = StepEnd(cutoff_pct=1.0, min_hold_s=5.0, max_hold_s=30.0)
    t = 0.0
    while not end.feed(t, 0.01 / 3210.0):
        t += 0.05
    assert end.reason == END_MAX_HOLD
    assert t == pytest.approx(30.0, abs=0.06)


def test_the_cutoff_is_a_fraction_the_scientist_sets():
    ends = {}
    for pct in (1.0, 10.0):
        end = StepEnd(cutoff_pct=pct, min_hold_s=0.0, max_hold_s=120.0)
        for t, i in _decaying(peak=1e-4, tau=2.0, dt=0.01, until=120.0):
            if end.feed(t, i):
                ends[pct] = t
                break
    settle = (SETTLE_SAMPLES - 1) * 0.01
    assert ends[10.0] == pytest.approx(2.0 * 2.302585 + settle, abs=0.02)   # tau ln 10
    assert ends[1.0] == pytest.approx(2.0 * 4.605170 + settle, abs=0.02)    # tau ln 100


def test_a_negative_current_is_judged_by_its_size():
    """Stepping DOWN reverses the current; the rule must not care about sign."""
    end = StepEnd(cutoff_pct=1.0, min_hold_s=0.0, max_hold_s=120.0)
    for t, i in _decaying(peak=-1e-4, tau=2.0, dt=0.05, until=120.0):
        if end.feed(t, i):
            break
    assert end.reason == END_CUTOFF
    # At the SAME time as the positive case -- a signed comparison ends it at once.
    assert t == pytest.approx(2.0 * 4.605170 + (SETTLE_SAMPLES - 1) * 0.05, abs=0.06)


def test_a_nan_sample_neither_ends_the_step_nor_poisons_the_peak():
    end = StepEnd(cutoff_pct=1.0, min_hold_s=0.0, max_hold_s=120.0)
    assert end.feed(0.0, 1e-4) is None
    assert end.feed(0.05, float("nan")) is None
    assert end.peak == 1e-4


def test_once_ended_a_step_stays_ended():
    end = StepEnd(cutoff_pct=1.0, min_hold_s=0.0, max_hold_s=1.0)
    end.feed(2.0, 1e-4)
    assert end.feed(3.0, 5e-4) == END_MAX_HOLD


# --- when a spectrum is due --------------------------------------------------

def _times(fast_s, slow_s, delta=0.1, until=10.0):
    taken, last, t = [], None, 0.0
    while t <= until + 1e-9:
        if spectrum_due(t, last, delta, fast_s, slow_s):
            taken.append(round(t, 3))
            last = t
        t = round(t + 0.01, 6)
    return taken


def test_full_rate_at_the_start_of_a_step_then_slow():
    taken = _times(fast_s=2.0, slow_s=1.0, until=6.0)
    fast = [t for t in taken if t < 2.0]
    slow = [t for t in taken if t >= 2.0]
    assert len(fast) == 20                      # every 0.1 s for 2 s
    # Then every 1 s, counted from the last spectrum taken -- never a gap shorter
    # than the slow interval, and no grid to fall out of step with.
    assert slow == [2.9, 3.9, 4.9, 5.9]


def test_the_first_spectrum_of_a_step_is_always_due():
    assert spectrum_due(0.0, None, 0.1, 5.0, 1.0)
    assert spectrum_due(50.0, None, 0.1, 5.0, 1.0)


def test_slow_equal_to_fast_is_full_rate_throughout():
    taken = _times(fast_s=0.0, slow_s=0.1, until=1.0)
    assert len(taken) == 11


def test_one_noisy_sample_near_zero_cannot_end_a_step():
    """Found on the PGSTAT302N 2026-10-05: a 1 MOhm dummy on CR10_1mA read ~90 nA
    low (the range's zero offset), so at +0.1 V the reading sat near zero with a
    'peak' of 12 nA -- and ONE sample landing within 0.12 nA of zero met the 1%
    cutoff and ended the step at 4.4 s, on a cell whose current never decays."""
    import numpy as np
    rng = np.random.default_rng(5)
    end = StepEnd(cutoff_pct=1.0, min_hold_s=2.0, max_hold_s=5.0)
    t = 0.0
    reason = None
    while reason is None:
        i = 1.2e-8 * rng.uniform(-1, 1)          # noise straddling zero, ~no signal
        if 3.0 < t < 3.05:
            i = 1e-11                            # the one sample that grazes zero
        reason = end.feed(t, i)
        t = round(t + 0.1, 6)
    assert reason == END_MAX_HOLD


def test_the_cutoff_needs_consecutive_samples_below_it():
    end = StepEnd(cutoff_pct=10.0, min_hold_s=0.0, max_hold_s=60.0)
    end.feed(0.0, 1.0)                              # the peak
    t = 0.1
    for k in range(4):                              # four below -- not yet
        assert end.feed(t, 0.05) is None
        t += 0.1
    assert end.feed(t, 0.5) is None                 # one above resets the count
    t += 0.1
    for k in range(4):
        assert end.feed(t, 0.05) is None
        t += 0.1
    assert end.feed(t, 0.05) == END_CUTOFF          # the fifth in a row


# On the PGSTAT302N 2026-10-05 (test 3), CR10_1mA readings came in whole counts of
# 3.0518 nA (1 mA / 327,680) -- every peak logged on that range was an integer
# multiple. At +0.1 V the range's offset cancelled the real 100 nA, the reading was
# mostly EXACTLY zero, the 'peak' was one count, and five zeros in a row met a 1%
# cutoff that no reading could ever express.

LSB_1MA = 1e-3 / 327_680


def test_a_cutoff_finer_than_one_count_cannot_end_a_step():
    end = StepEnd(cutoff_pct=1.0, min_hold_s=2.0, max_hold_s=5.0,
                  resolution_a=LSB_1MA)
    t, reason = 0.0, None
    while reason is None:
        counts = 1 if int(t * 10) % 7 == 0 else 0        # mostly exactly zero
        reason = end.feed(t, counts * LSB_1MA)
        t = round(t + 0.1, 6)
    assert reason == END_MAX_HOLD
    assert end.unresolved                               # and it says why


def test_a_resolvable_cutoff_still_ends_the_step():
    """A real decay whose 1% level is many counts: unchanged behaviour."""
    end = StepEnd(cutoff_pct=1.0, min_hold_s=0.0, max_hold_s=120.0,
                  resolution_a=LSB_1MA)
    for t, i in _decaying(peak=1e-4, tau=2.0, dt=0.05, until=120.0):
        if end.feed(t, round(i / LSB_1MA) * LSB_1MA):
            break
    assert end.reason == END_CUTOFF and not end.unresolved


def test_without_a_known_resolution_nothing_changes():
    end = StepEnd(cutoff_pct=1.0, min_hold_s=0.0, max_hold_s=120.0)
    for t, i in _decaying(peak=1e-4, tau=2.0, dt=0.05, until=120.0):
        if end.feed(t, i):
            break
    assert end.reason == END_CUTOFF
