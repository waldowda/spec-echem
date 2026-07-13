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


def test_knee_is_found_and_recommendation_is_five_percent_below():
    times = np.linspace(0.02, 0.16, 20)
    counts = _linear_then_knee(times, knee_t=0.10)
    result = analyze_linearity(times, counts, tolerance_pct=2.0)

    assert result["limit_found"] is True
    # The limit is the last still-linear point, so it must sit at/just below the knee.
    assert result["t_limit"] <= 0.105
    assert result["t_limit"] >= 0.08
    assert result["t_recommended"] == pytest.approx(0.95 * result["t_limit"], rel=1e-3)
    # The fit recovered the true linear region, not the compressed part.
    assert result["slope"] == pytest.approx(600000.0, rel=0.05)
    assert result["offset"] == pytest.approx(300.0, abs=500.0)


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


def test_find_saturation_time_brackets_by_doubling():
    spec = FakeSpectrometer()
    spec.init()

    t_sat = find_saturation_time(spec, start=0.005)

    assert t_sat > 0.005
    # It is a doubling of the start value.
    assert np.log2(t_sat / 0.005) == pytest.approx(round(np.log2(t_sat / 0.005)), abs=1e-9)
    # And it really does saturate there.
    spec.set_integration_time(t_sat)
    _, spectrum = spec.measure()
    assert spectrum.max() >= FULL_SCALE_COUNTS * 0.99
