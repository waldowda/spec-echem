"""
Tests for spec_echem.data.write_echem_file — the Python-mode clean-txt echem
writer. Feeds synthetic EchemData (no hardware) and checks the output satisfies the
reader contract in gamry_data.py. No Qt, no toolkitpy. The Gamry-specific
acq_data->EchemData conversion is tested in test_potentiostat.py.
"""
import numpy as np
import pytest

from spec_echem.data import (
    EchemData, write_echem_file, echem_txt_path,
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
)
from spec_echem.gamry_data import read_cv, read_chrono, CV_COLUMNS, CHRONO_COLUMNS


def chrono_acq(n=5, t0=10.0):
    """Synthetic chrono segment — note the non-zero start time t0 to prove the
    writer rebases both time columns to 0 (drops the vestigial +100)."""
    return EchemData(
        time=t0 + np.arange(n) * 0.1,
        potential=np.full(n, 0.2),
        current=np.linspace(1e-6, 5e-6, n),
    )


def cv_acq(n=6):
    """Synthetic CV segment."""
    return EchemData(
        time=np.arange(n) * 0.1,
        potential=np.linspace(0.0, 0.5, n),
        current=np.linspace(-1e-6, 1e-6, n),
    )


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


# --- echem_txt_path: the public accessor the GUI uses to find a segment's file ---

@pytest.mark.parametrize("data_type,run_number,expected", [
    (DATA_TYPE_CV, 0, "CV.txt"),
    (DATA_TYPE_DOPING, 2, "steps(2).txt"),
    (DATA_TYPE_DEDOPING, 2, "dedoping(2).txt"),
    (DATA_TYPE_PREDEDOPING, 0, "prededoping(0).txt"),
])
def test_echem_txt_path_matches_written_file(tmp_path, data_type, run_number, expected):
    run_folder = tmp_path / "20250715_Test"
    written = write_echem_file(chrono_acq() if data_type != DATA_TYPE_CV else cv_acq(),
                               data_type, run_number, tmp_path, "20250715_Test")
    located = echem_txt_path(run_folder, data_type, run_number)
    assert located.name == expected
    assert located == written  # the accessor points exactly at what the writer wrote
