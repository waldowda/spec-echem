"""
Tests for spec_echem.gamry_data — reading converted Gamry echem .txt files.
Uses the real converted golden files in tests/golden/.
"""
import os
import pytest

from spec_echem.gamry_data import (
    read_cv, read_chrono, CV_COLUMNS, CHRONO_COLUMNS, POTENTIAL_COL, CURRENT_COL,
)

GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "20250715_P3HT9505_KPF6")


# --- CV ---

def test_read_cv_shape_and_columns():
    df = read_cv(os.path.join(GOLDEN, "CV.txt"))
    assert list(df.columns) == CV_COLUMNS
    assert df.shape == (721, 2)


def test_read_cv_value_ranges():
    df = read_cv(os.path.join(GOLDEN, "CV.txt"))
    # CV swept roughly -0.5 .. 0.7 V, current on the order of hundreds of uA
    assert df[POTENTIAL_COL].min() < -0.4
    assert df[POTENTIAL_COL].max() > 0.6
    assert df[CURRENT_COL].abs().max() < 1e-2  # well under 10 mA


# --- chrono (doping / dedoping) ---

@pytest.mark.parametrize("fname", ["steps(7).txt", "dedoping(7).txt"])
def test_read_chrono_shape_and_columns(fname):
    df = read_chrono(os.path.join(GOLDEN, fname))
    assert list(df.columns) == CHRONO_COLUMNS
    assert df.shape == (601, 5)


@pytest.mark.parametrize("fname", ["steps(7).txt", "dedoping(7).txt"])
def test_read_chrono_corrected_time_starts_at_zero(fname):
    df = read_chrono(os.path.join(GOLDEN, fname))
    assert df["Corrected time (s)"].iloc[0] == pytest.approx(0.0)
    assert df["Corrected time (s)"].is_monotonic_increasing


def test_doping_vs_dedoping_polarity():
    # doping held near +0.8 V, dedoping near -0.5 V
    dope = read_chrono(os.path.join(GOLDEN, "steps(7).txt"))
    dedope = read_chrono(os.path.join(GOLDEN, "dedoping(7).txt"))
    assert dope[POTENTIAL_COL].mean() > 0.5
    assert dedope[POTENTIAL_COL].mean() < -0.3


# --- error handling ---

def test_missing_columns_raises(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("foo\tbar\n1\t2\n")
    with pytest.raises(ValueError, match="missing expected column"):
        read_cv(bad)
