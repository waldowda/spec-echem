"""
Tests for spec_echem.data.write_echem_file — the Python-mode clean-txt echem
writer. Feeds synthetic acq_data() structured arrays (no hardware) and checks the
output satisfies the reader contract in gamry_data.py. No Qt, no toolkitpy.
"""
import numpy as np
import pytest

from spec_echem.data import (
    write_echem_file,
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
)
from spec_echem.gamry_data import read_cv, read_chrono, CV_COLUMNS, CHRONO_COLUMNS


def chrono_acq(n=5, t0=10.0):
    """Synthetic chrono acq_data() — note the non-zero start time t0 to prove the
    writer rebases both time columns to 0 (drops the vestigial +100)."""
    dt = np.dtype([('point', 'i4'), ('time', 'f8'), ('vf', 'f8'), ('im', 'f8')])
    arr = np.zeros(n, dtype=dt)
    arr['point'] = np.arange(n)
    arr['time'] = t0 + np.arange(n) * 0.1
    arr['vf'] = 0.2
    arr['im'] = np.linspace(1e-6, 5e-6, n)
    return arr


def cv_acq(n=6):
    """Synthetic CV acq_data() — includes the extra `cycle` field CV returns."""
    dt = np.dtype([('point', 'i4'), ('time', 'f8'), ('vf', 'f8'),
                   ('im', 'f8'), ('cycle', 'i4')])
    arr = np.zeros(n, dtype=dt)
    arr['vf'] = np.linspace(0.0, 0.5, n)
    arr['im'] = np.linspace(-1e-6, 1e-6, n)
    return arr


# --- CV ---

def test_cv_file_name_columns_and_roundtrip(tmp_path):
    path = write_echem_file(cv_acq(6), DATA_TYPE_CV, 0, tmp_path, "20250715_Test")
    assert path.name == "CV.txt"
    assert path.parent == tmp_path / "20250715_Test"
    df = read_cv(path)  # raises if the columns don't match the reader contract
    assert list(df.columns) == CV_COLUMNS
    assert len(df) == 6


# --- chrono (doping / dedoping / pre-dedoping) ---

@pytest.mark.parametrize("data_type,expected", [
    (DATA_TYPE_DOPING, "steps(0).txt"),
    (DATA_TYPE_DEDOPING, "dedoping(0).txt"),
    (DATA_TYPE_PREDEDOPING, "prededoping(0).txt"),
])
def test_chrono_filenames(tmp_path, data_type, expected):
    path = write_echem_file(chrono_acq(), data_type, 0, tmp_path, "20250715_Test")
    assert path.name == expected


def test_chrono_columns_and_roundtrip(tmp_path):
    path = write_echem_file(chrono_acq(n=5), DATA_TYPE_DOPING, 0, tmp_path, "20250715_Test")
    df = read_chrono(path)  # raises if columns don't match CHRONO_COLUMNS
    assert list(df.columns) == CHRONO_COLUMNS
    assert len(df) == 5


def test_time_columns_start_at_zero_no_plus_100(tmp_path):
    # acq_data starts at t=10.0; both time columns must be rebased to 0 (no +100).
    path = write_echem_file(chrono_acq(n=5, t0=10.0), DATA_TYPE_DOPING, 0,
                            tmp_path, "20250715_Test")
    df = read_chrono(path)
    assert df["Time (s)"].iloc[0] == pytest.approx(0.0)
    assert df["Corrected time (s)"].iloc[0] == pytest.approx(0.0)
    assert df["Time (s)"].iloc[-1] == pytest.approx(0.4)  # 4 * 0.1
    assert list(df["Index"]) == [0, 1, 2, 3, 4]


def test_run_number_in_filename(tmp_path):
    path = write_echem_file(chrono_acq(), DATA_TYPE_DEDOPING, 3, tmp_path, "20250715_Test")
    assert path.name == "dedoping(3).txt"


def test_missing_field_raises(tmp_path):
    # A structured array without 'vf' should fail loudly, not write garbage.
    bad = np.zeros(3, dtype=np.dtype([('time', 'f8'), ('im', 'f8')]))
    with pytest.raises(ValueError, match="vf"):
        write_echem_file(bad, DATA_TYPE_DOPING, 0, tmp_path, "20250715_Test")
