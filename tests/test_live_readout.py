"""The live-plot readout: which CV cycle is running, and which way it is sweeping.

Requested 2026-10-07 at the bench: at 1 mV/s a CV point arrives every 20 s, and with
nothing on the plot but a slowly growing line it was hard to tell the run was moving,
or which cycle it was on.
"""
import numpy as np

from spec_echem.experiment import cv_progress


def _cv(n_cycles, lo=0.0, hi=1.0, step=0.02, start=None):
    """A staircase CV, as the potentiostat measures it."""
    up = np.arange(lo, hi + step / 2, step)
    one = np.concatenate([up, up[-2::-1]])
    e = np.concatenate([one] + [one[1:]] * (n_cycles - 1))
    return e if start is None else e[start:]


def test_the_first_sweep_is_cycle_one_going_up():
    e = _cv(3)
    assert cv_progress(e[:10], 3) == (1, +1)


def test_after_the_top_vertex_it_is_still_cycle_one_going_down():
    e = _cv(3)
    assert cv_progress(e[:60], 3) == (1, -1)


def test_back_at_the_bottom_the_next_cycle_starts():
    e = _cv(3)
    assert cv_progress(e[:110], 3) == (2, +1)
    assert cv_progress(e[:250], 3) == (3, +1)


def test_the_count_never_passes_the_cycles_asked_for():
    e = _cv(2)
    assert cv_progress(np.concatenate([e, e[-1:] + [0.0, 0.05, 0.1]]), 2)[0] == 2


def test_wobble_bigger_than_a_small_step_is_not_a_reversal():
    """The 20261007 1 mV/s run: the measured potential wobbled by up to +-2.5 mV. On
    the 2 mV steps suggested for slow CVs that wobble is bigger than a step, so the
    potential goes backwards between points without the sweep reversing."""
    e = _cv(2, step=0.002)
    rng = np.random.default_rng(1)
    noisy = e + rng.uniform(-0.0025, 0.0025, e.size)
    assert cv_progress(noisy[:300], 2) == (1, +1)
    assert cv_progress(noisy[:700], 2) == (1, -1)
    assert cv_progress(noisy[:1100], 2) == (2, +1)


def test_nothing_yet_is_cycle_one_direction_unknown():
    assert cv_progress([], 3) == (1, 0)
    assert cv_progress([0.0], 3) == (1, 0)


from spec_echem.experiment import format_current, live_status   # noqa: E402


def test_the_cv_readout_names_cycle_direction_potential_and_current():
    e = _cv(3)[:60]
    text = live_status(True, np.arange(e.size) * 20.0, e, np.full(e.size, 3.41e-6), 3)
    assert text.splitlines() == ["Cycle 1 of 3  ↓ down", f"E = {e[-1]:+.3f} V",
                                 "I = +3.41 µA"]


def test_a_hold_readout_shows_time_since_the_segment_began():
    t = 100.0 + np.arange(124) * 0.1
    text = live_status(False, t, np.full(t.size, 0.7), np.full(t.size, -2.5e-8))
    assert text.splitlines() == ["t = 12.3 s", "E = +0.700 V", "I = -25 nA"]


def test_no_data_no_readout():
    assert live_status(True, [], [], [], 3) == ""


def test_currents_are_written_in_a_readable_unit():
    assert format_current(1.2e-3) == "+1.2 mA"
    assert format_current(-6.81e-5) == "-68.1 µA"
    assert format_current(3.05e-12) == "+3.05 pA"
    assert format_current(float("nan")) == "--"
