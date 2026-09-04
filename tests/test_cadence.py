"""Cadence advice: one spectrum's real cost against a segment's delta_time.

The failure this guards is not a crash — it is a file that looks completely
normal. On 2026-09-04 a CV segment asked for a spectrum every 10 ms, got one
every 29.9 ms, and ran 14.3 s against a 4.8 s CV: two thirds of the spectra
recorded a cell that had already stopped. The old warning stayed silent because
it compared integration x averages (2.6 ms) with the step and ignored the ~30 ms
of per-spectrum overhead.
"""
import logging

import numpy as np
import pytest

from spec_echem.acquisition import (SPECTRUM_OVERHEAD_S, _warn_if_cadence_unachievable,
                                    next_deadline, spectrum_cost_seconds,
                                    suggest_scan_averages)
from spec_echem.fakes import FakeSpectrometer


def test_a_spectrum_costs_more_than_its_exposure():
    # 2.6439 ms x 20 = 52.9 ms of exposure, but ~83 ms of wall clock.
    cost = spectrum_cost_seconds(2.6439, 20)
    assert cost == 2.6439 * 20 / 1000.0 + SPECTRUM_OVERHEAD_S
    assert 0.082 < cost < 0.084


def test_the_suggestion_leaves_room_for_the_overhead():
    # A 100 ms step has 70 ms of room once overhead is paid: 70 / 2.6439 = 26.
    assert suggest_scan_averages(2.6439, 0.100) == 26
    assert spectrum_cost_seconds(2.6439, 26) <= 0.100
    assert spectrum_cost_seconds(2.6439, 27) > 0.100


def test_no_averaging_count_rescues_a_step_shorter_than_the_overhead():
    # A 10 ms step cannot hold one spectrum at any averaging — the step itself
    # has to change, and the advice must say so rather than suggest 0 averages.
    assert suggest_scan_averages(2.6439, 0.010) == 0


def test_the_metrohm_case_that_slipped_through_now_warns(caplog):
    spec = FakeSpectrometer()
    spec.set_integration_time(2.6439)
    spec.set_scan_averages(1)          # 2.6 ms of exposure — the old test passed
    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_unachievable(spec, delta_time=0.010, num_points=481)
    assert "cannot keep the requested cadence" in caplog.text
    assert "overhead" in caplog.text


def test_the_gamry_rig_stays_silent(caplog):
    # 0.088 ms x 200 + 30 ms = 47.6 ms inside a 100 ms step. This rig works; the
    # warning must not start crying wolf on it.
    spec = FakeSpectrometer()
    spec.set_integration_time(0.088)
    spec.set_scan_averages(200)
    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_unachievable(spec, delta_time=0.100, num_points=241)
    assert caplog.text == ""


def test_the_warning_names_an_averaging_count_that_would_fit(caplog):
    spec = FakeSpectrometer()
    spec.set_integration_time(2.6439)
    spec.set_scan_averages(200)        # 559 ms against a 100 ms step
    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_unachievable(spec, delta_time=0.100, num_points=241)
    assert "About 26 scan averages would fit" in caplog.text
    assert "currently 200" in caplog.text


def test_a_spectrometer_that_cannot_answer_does_not_break_the_run(caplog):
    class Mute:
        def per_spectrum_seconds(self):
            raise RuntimeError("no measconfig")

    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_unachievable(Mute(), delta_time=0.100, num_points=10)
    assert caplog.text == ""


def test_advice_is_optional_but_the_warning_is_not(caplog):
    """A spectrometer that reports its cost but not its factors still warns."""
    class Partial:
        def per_spectrum_seconds(self):
            return 0.500

    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_unachievable(Partial(), delta_time=0.100, num_points=10)
    assert "cannot keep the requested cadence" in caplog.text
    assert "Reduce scan averages" in caplog.text


# ---------------------------------------------------------------- pacing ----
# The spectra land on a grid, and the grid does not slide. Both failures below
# were MEASURED on the Metrohm rig 2026-09-04 in files that looked entirely
# normal, which is why they are pinned with a fake clock rather than wall time.

class _Clock:
    """A clock that only moves when something asks it to — sleep() and the
    stand-in spectrometer below. Makes the schedule deterministic."""

    def __init__(self, start=1000.0):
        self.t = start

    def time_ns(self):
        return int(self.t * 1e9)

    def sleep(self, seconds):
        self.t += max(0.0, seconds)


class _ClockSpectrometer:
    """A spectrometer whose measurement takes exactly `cost` of the fake clock."""

    def __init__(self, clock, cost=0.040, trigger_wait=0.0):
        self.clock, self.cost, self.trigger_wait = clock, cost, trigger_wait
        self.trigger_mode = None

    def set_trigger_mode(self, mode):
        self.trigger_mode = mode

    def per_spectrum_seconds(self):
        # Exposure only; the loop adds SPECTRUM_OVERHEAD_S to estimate the rest,
        # so its pre-first-measurement estimate comes out exactly `cost` here.
        return self.cost - SPECTRUM_OVERHEAD_S

    def integration_and_averages(self):
        return max(self.cost - SPECTRUM_OVERHEAD_S, 0.0) * 1000.0, 1

    def measure(self, abort_event=None, on_armed=None):
        if on_armed is not None:
            on_armed()                      # the potentiostat fires here...
            self.clock.t += self.trigger_wait   # ...and the edge is late
        self.clock.t += self.cost
        return self.clock.t * 1e5, [0.0]


def _run(monkeypatch, points, delta_time, cost, trigger_wait):
    from spec_echem import acquisition
    clock = _Clock()
    monkeypatch.setattr(acquisition.time, "time_ns", clock.time_ns)
    monkeypatch.setattr(acquisition.time, "sleep", clock.sleep)
    spec = _ClockSpectrometer(clock, cost=cost, trigger_wait=trigger_wait)
    _, timestamps = acquisition.acquire_segment(
        spec, points, delta_time=delta_time, trigger=True, on_armed=lambda: None)
    return np.diff(np.array(timestamps) / 1e5 * 1e5) if False else \
        np.diff(np.array(timestamps))


def test_spectrum_one_is_a_full_step_after_the_triggered_spectrum(monkeypatch):
    """The Autolab fires ~6 s after arming. That wait must not eat the first gap.

    Before the fix the interval 0->1 was one measurement, not one delta_time:
    37.5 / 48.1 / 71.6 ms against a 100 ms target, once per segment, always at
    index 1.
    """
    gaps = _run(monkeypatch, points=6, delta_time=0.100, cost=0.040,
                trigger_wait=5.95)
    assert gaps[0] == pytest.approx(0.100, abs=1e-9)


def test_only_the_first_gap_can_carry_the_cost_estimate_error(monkeypatch):
    """Spectrum 0's own duration cannot be measured — it contains the trigger
    wait — so the gap to spectrum 1 is placed using the estimate, and is off by
    however wrong that estimate was. From spectrum 2 the loop is using the real
    measured cost, and the grid is exact. This is the one residual of the fix,
    and it is bounded and self-correcting rather than accumulating."""
    from spec_echem import acquisition
    clock = _Clock()
    monkeypatch.setattr(acquisition.time, "time_ns", clock.time_ns)
    monkeypatch.setattr(acquisition.time, "sleep", clock.sleep)

    spec = _ClockSpectrometer(clock, cost=0.040, trigger_wait=5.95)
    spec.per_spectrum_seconds = lambda: 0.040    # estimate 70 ms vs a real 40 ms
    _, ts = acquisition.acquire_segment(spec, 6, delta_time=0.100, trigger=True,
                                        on_armed=lambda: None)
    gaps = np.diff(np.array(ts))
    assert gaps[0] == pytest.approx(0.070, abs=1e-9)   # short by the 30 ms error
    assert gaps[1] == pytest.approx(0.130, abs=1e-9)   # and recovered on the next
    assert np.allclose(gaps[2:], 0.100, atol=1e-9)     # exact from here on


def test_the_grid_does_not_slide_over_a_long_segment(monkeypatch):
    """Every interval is delta_time, so error does not accumulate.

    Before the fix the period was delta_time PLUS the measurement, which put a
    301-point hold 303 ms past its own Autolab clock and grew without bound.
    """
    gaps = _run(monkeypatch, points=301, delta_time=0.100, cost=0.040,
                trigger_wait=5.95)
    assert np.allclose(gaps, 0.100, atol=1e-9)
    total = float(np.sum(gaps))
    assert total == pytest.approx(30.0, abs=1e-6)      # was 30.303 s


def test_a_slow_measurement_is_absorbed_not_pushed_down_the_line(monkeypatch):
    """One late scan must not shift every spectrum after it — the deadlines are
    absolute, so the schedule recovers on the next one."""
    from spec_echem import acquisition
    clock = _Clock()
    monkeypatch.setattr(acquisition.time, "time_ns", clock.time_ns)
    monkeypatch.setattr(acquisition.time, "sleep", clock.sleep)

    spec = _ClockSpectrometer(clock, cost=0.040)
    real_measure = spec.measure
    state = {"n": 0}

    def hiccup(abort_event=None, on_armed=None):
        state["n"] += 1
        if state["n"] == 3:
            clock.t += 0.070        # this one runs long
        return real_measure(abort_event, on_armed)

    spec.measure = hiccup
    _, ts = acquisition.acquire_segment(spec, 6, delta_time=0.100, trigger=True,
                                        on_armed=lambda: None)
    ts = np.array(ts)
    # The late scan lands late; everything after is back on the original grid.
    assert ts[-1] - ts[0] == pytest.approx(0.500, abs=1e-9)


def test_when_a_spectrum_cannot_fit_the_step_it_free_runs(monkeypatch):
    """Cost above delta_time: no negative waiting, no crash — it simply runs as
    fast as it can, which is what the cadence warning is there to announce."""
    gaps = _run(monkeypatch, points=5, delta_time=0.010, cost=0.030,
                trigger_wait=0.0)
    assert np.allclose(gaps, 0.030, atol=1e-9)


def test_deadlines_are_absolute_not_cumulative():
    from spec_echem.acquisition import next_deadline
    # The 100th deadline depends only on the anchor, never on how it got there.
    assert next_deadline(1000.0, 99, 0.1, 0.04) == pytest.approx(1010.0 - 0.04)
    assert next_deadline(1000.0, 0, 0.1, 0.04) == pytest.approx(1000.1 - 0.04)
