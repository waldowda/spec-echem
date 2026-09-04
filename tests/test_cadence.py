"""Cadence advice: one spectrum's real cost against a segment's delta_time.

The failure this guards is not a crash — it is a file that looks completely
normal. On 2026-09-04 a CV segment asked for a spectrum every 10 ms, got one
every 29.9 ms, and ran 14.3 s against a 4.8 s CV: two thirds of the spectra
recorded a cell that had already stopped. The old warning stayed silent because
it compared integration x averages (2.6 ms) with the step and ignored the ~30 ms
of per-spectrum overhead.
"""
import logging

from spec_echem.acquisition import (SPECTRUM_OVERHEAD_S, _warn_if_cadence_unachievable,
                                    spectrum_cost_seconds, suggest_scan_averages)
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
