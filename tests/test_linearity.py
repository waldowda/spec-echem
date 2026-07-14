"""Tests for the detector linearity check (pure analysis + a fake-hardware ramp)."""
import numpy as np
import pytest

from spec_echem.fakes import FakeSpectrometer
from spec_echem.linearity import (
    FULL_SCALE_COUNTS,
    LinearityError,
    analyze_linearity,
    find_saturation_time,
    measure_linearity_series,
)


def _linear_then_knee(times, slope=600000.0, offset=300.0, knee_t=0.10):
    """Counts linear in t up to knee_t, then compressing toward full scale."""
    counts = offset + slope * np.asarray(times, float)
    knee_counts = offset + slope * knee_t
    headroom = FULL_SCALE_COUNTS - knee_counts
    hot = counts > knee_counts
    counts[hot] = knee_counts + headroom * (1.0 - np.exp(-(counts[hot] - knee_counts) / headroom))
    return counts


def test_perfectly_linear_reports_no_limit():
    times = np.linspace(0.02, 0.06, 15)
    counts = 300.0 + 400000.0 * times           # never approaches full scale
    result = analyze_linearity(times, counts)

    assert result["limit_found"] is False
    assert result["saturated"] is False
    assert result["t_limit"] is None
    # Falls back to 5% below the highest time tested.
    assert result["t_recommended"] == pytest.approx(0.95 * times[-1], rel=1e-6)
    assert "increase stop" in result["summary"].lower()


def test_knee_is_found_and_recommendation_takes_the_tighter_constraint():
    times = np.linspace(0.02, 0.16, 20)
    counts = _linear_then_knee(times, knee_t=0.10)
    result = analyze_linearity(times, counts, tolerance_pct=2.0)

    assert result["limit_found"] is True
    # The limit is the last still-linear point, so it must sit at/just below the knee.
    assert 0.08 <= result["t_limit"] <= 0.105
    # Whichever of (5% below the limit) / (max fill) is tighter wins.
    assert result["t_recommended"] == pytest.approx(
        min(0.95 * result["t_limit"], result["t_fill"]), rel=1e-3)
    # The fit recovered the true linear region, not the compressed part.
    assert result["slope"] == pytest.approx(600000.0, rel=0.05)
    assert result["offset"] == pytest.approx(300.0, abs=500.0)


def test_fill_cap_binds_when_the_detector_stays_linear_to_the_clip():
    """Dean's real hardware (2026-07-13): the response tracks the fit to within ~1%
    right up to the hard ADC clip, so the deviation test never fires. Linearity alone
    would put the working point at ~94% of full scale — the fill cap is the only thing
    providing headroom. Regression guard for that."""
    slope, offset = 605771.0, 2120.0          # measured fit from the real run
    times = np.linspace(0.022, 0.1112, 12)
    counts = np.minimum(offset + slope * times, FULL_SCALE_COUNTS)

    result = analyze_linearity(times, counts, tolerance_pct=2.0, max_fill_frac=0.85)

    # The ramp ended at the clip, not at a deviation.
    assert result["saturated"] is True
    assert result["bound_by"] == "fill"

    expected_t = (0.85 * FULL_SCALE_COUNTS - offset) / slope     # ~0.0885 ms
    assert result["t_recommended"] == pytest.approx(expected_t, rel=0.02)
    assert result["counts_recommended"] == pytest.approx(0.85 * FULL_SCALE_COUNTS, rel=0.02)

    # And it is genuinely more conservative than the linearity-only answer.
    assert result["t_recommended"] < 0.95 * result["t_limit"]
    assert result["counts_recommended"] < 0.90 * FULL_SCALE_COUNTS


def test_tighter_tolerance_finds_the_knee_earlier():
    times = np.linspace(0.02, 0.16, 20)
    counts = _linear_then_knee(times, knee_t=0.10)

    strict = analyze_linearity(times, counts, tolerance_pct=1.0)
    loose = analyze_linearity(times, counts, tolerance_pct=5.0)

    assert strict["t_limit"] <= loose["t_limit"]


def test_saturated_at_the_lowest_time_is_an_error():
    times = np.linspace(0.02, 0.16, 10)
    counts = np.full(10, FULL_SCALE_COUNTS, float)
    with pytest.raises(LinearityError, match="Already saturated"):
        analyze_linearity(times, counts)


def test_saturated_at_start_reports_saturation_not_too_few_points():
    """A ramp that saturates immediately stops after one point. The advice must be
    'lower Start / attenuate', NOT the misleading 'increase Steps'."""
    with pytest.raises(LinearityError, match="Already saturated"):
        analyze_linearity([5.0], [FULL_SCALE_COUNTS])


def test_flat_response_means_no_signal():
    times = np.linspace(0.02, 0.16, 10)
    counts = 300.0 + np.zeros(10)               # lamp off / shutter closed
    with pytest.raises(LinearityError, match="No response"):
        analyze_linearity(times, counts)


def test_too_few_points_to_fit():
    times = np.linspace(0.02, 0.05, 3)
    counts = 300.0 + 400000.0 * times
    with pytest.raises(LinearityError, match="at least"):
        analyze_linearity(times, counts)


def test_ramp_against_the_fake_tracks_one_pixel_and_stops_at_saturation():
    spec = FakeSpectrometer()
    spec.init()
    times = np.linspace(0.005, 0.20, 30)

    used, counts, peak_px = measure_linearity_series(spec, times)

    # Early-stopped at saturation rather than running the whole ramp.
    assert len(used) < len(times)
    assert peak_px is not None
    # One fixed pixel, monotonically brighter with integration time (up to the clip).
    assert counts[0] < counts[-1]
    assert counts[-1] >= FULL_SCALE_COUNTS * 0.99

    result = analyze_linearity(used, counts)
    assert result["limit_found"] is True
    assert result["t_recommended"] < result["t_limit"]


def test_find_saturation_time_bisects_to_a_tight_bracket():
    spec = FakeSpectrometer()
    spec.init()

    sat = find_saturation_time(spec, start=0.005)

    # Saturates above t_sat, does not below it — and the bracket is tight, which is
    # the whole point: plain doubling could only ever report a power-of-two multiple.
    assert sat["t_below"] < sat["t_sat"]
    assert (sat["t_sat"] - sat["t_below"]) / sat["t_sat"] < 0.05
    assert sat["counts_below"] < FULL_SCALE_COUNTS * 0.99

    spec.set_integration_time(sat["t_sat"])
    _, spectrum = spec.measure()
    assert spectrum.max() >= FULL_SCALE_COUNTS * 0.99

    spec.set_integration_time(sat["t_below"])
    _, spectrum = spec.measure()
    assert spectrum.max() < FULL_SCALE_COUNTS * 0.99


def test_find_saturation_time_rejects_a_saturated_start():
    spec = FakeSpectrometer()
    spec.init()
    with pytest.raises(LinearityError, match="Already saturated at Start"):
        find_saturation_time(spec, start=5.0)
