"""
Unit tests for spec_echem.data — compute_absorbance and write_spectra_file.
No hardware required.
"""
import math
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

import json
from spec_echem.data import (
    compute_absorbance,
    write_spectra_file,
    write_run_metadata,
    DATA_TYPE_CV,
    DATA_TYPE_DOPING,
    DATA_TYPE_DEDOPING,
    DATA_TYPE_PREDEDOPING,
)

# --- synthetic fixture data ---

N_PIXELS = 10
N_TIMES = 3
WAVELENGTHS = np.linspace(400.0, 900.0, N_PIXELS)
DARK = np.full(N_PIXELS, 100.0)
REF = np.full(N_PIXELS, 600.0)
# Flat spectra at 350 counts → transmittance = (350-100)/(600-100) = 0.5 → A = log10(2)
SPECTRA = [np.full(N_PIXELS, 350.0) for _ in range(N_TIMES)]
TIMESTAMPS = [0.0, 0.1, 0.2]  # seconds


# --- compute_absorbance ---

class TestComputeAbsorbance:
    def test_shape(self):
        result = compute_absorbance(SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS)
        assert result.shape == (N_PIXELS, N_TIMES)

    def test_index_is_wavelengths(self):
        result = compute_absorbance(SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS)
        np.testing.assert_array_almost_equal(result.index.values, WAVELENGTHS)

    def test_absorbance_value(self):
        result = compute_absorbance(SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS)
        expected = math.log10(2)  # -log10(0.5)
        assert abs(result.iloc[0, 0] - expected) < 1e-10

    def test_columns_are_relative_timestamps(self):
        result = compute_absorbance(SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS)
        # First column should be 0.0 (relative to first timestamp)
        assert result.columns[0] == pytest.approx(0.0)
        assert result.columns[1] == pytest.approx(0.1)
        assert result.columns[2] == pytest.approx(0.2)

    def test_dark_equals_ref_produces_nan(self):
        dark = ref = np.full(N_PIXELS, 500.0)
        # NumPy returns NaN (not raise) when ref == dark (zero denominator)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = compute_absorbance(SPECTRA, dark, ref, WAVELENGTHS, TIMESTAMPS)
        assert not np.isfinite(result.values).all()


# --- write_spectra_file ---

@pytest.fixture
def absorb7():
    return compute_absorbance(SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS)


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path


class TestWriteSpectraFile:

    @pytest.mark.parametrize("data_type,run_number,expected_name", [
        (DATA_TYPE_CV,          0, "CVspectra.txt"),
        (DATA_TYPE_DOPING,      7, "spectra(7).txt"),
        (DATA_TYPE_DEDOPING,    7, "dedopingspectra(7).txt"),
        (DATA_TYPE_PREDEDOPING, 0, "prededopingspectra(0).txt"),
    ])
    def test_filename(self, absorb7, tmp_root, data_type, run_number, expected_name):
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            data_type, run_number, tmp_root, "20250715_Test"
        )
        assert path.name == expected_name

    def test_output_has_8_columns(self, absorb7, tmp_root):
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            DATA_TYPE_CV, 0, tmp_root, "20250715_Test"
        )
        df = pd.read_csv(path, sep='\t')
        assert df.shape[1] == 8

    def test_tab_separator(self, absorb7, tmp_root):
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            DATA_TYPE_CV, 0, tmp_root, "20250715_Test"
        )
        with open(path) as f:
            header = f.readline()
        assert '\t' in header

    @pytest.mark.parametrize("data_type,expected_col6", [
        (DATA_TYPE_CV,          "Index"),
        (DATA_TYPE_DOPING,      "Spectrum number"),
        (DATA_TYPE_DEDOPING,    "Index"),
        (DATA_TYPE_PREDEDOPING, "Index"),
    ])
    def test_column_6_name(self, absorb7, tmp_root, data_type, expected_col6):
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            data_type, 0, tmp_root, "20250715_Test"
        )
        df = pd.read_csv(path, sep='\t')
        assert df.columns[5] == expected_col6

    def test_dark_ref_only_in_first_block(self, absorb7, tmp_root):
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            DATA_TYPE_CV, 0, tmp_root, "20250715_Test"
        )
        df = pd.read_csv(path, sep='\t')
        # First N_PIXELS rows (minus last which has NaN time) — dark/ref should be present
        # In our synthetic data: rows 0..N_PIXELS-1 are time_point 0
        first_block = df.iloc[:N_PIXELS]
        # All non-last rows of block 0 should have dark/ref
        assert first_block.iloc[:N_PIXELS - 1]['Column 3 (a. u.)'].notna().all()
        assert first_block.iloc[:N_PIXELS - 1]['Column 4 (a. u.)'].notna().all()
        # Rows in subsequent blocks should be NaN
        second_block = df.iloc[N_PIXELS: 2 * N_PIXELS]
        assert second_block['Column 3 (a. u.)'].isna().all()
        assert second_block['Column 4 (a. u.)'].isna().all()

    def test_time_column_trailing_nan(self, absorb7, tmp_root):
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            DATA_TYPE_CV, 0, tmp_root, "20250715_Test"
        )
        df = pd.read_csv(path, sep='\t')
        # Last row of each block should have NaN in time columns
        assert pd.isna(df.iloc[N_PIXELS - 1]['Time (s)'])
        assert pd.isna(df.iloc[N_PIXELS - 1]['Corrected time (s)'])

    def test_creates_directory(self, absorb7, tmp_root):
        subdir = "20250715_NewFolder"
        path = write_spectra_file(
            absorb7, SPECTRA, DARK, REF, WAVELENGTHS, TIMESTAMPS,
            DATA_TYPE_CV, 0, tmp_root, subdir
        )
        assert path.exists()
        assert (tmp_root / subdir).is_dir()


# --- write_run_metadata ---

class TestWriteRunMetadata:
    SETTINGS = {
        "sample_name": "P3HT",
        "electrolyte": "0.1M KPF6",
        "notes": "Test run",
        "cv_scan_rate": 100.0,
        "data_folder": "20250715_P3HT_Test",
    }

    def test_file_created(self, tmp_root):
        path = write_run_metadata(self.SETTINGS, tmp_root, "20250715_P3HT_Test")
        assert path.exists()

    def test_filename_convention(self, tmp_root):
        path = write_run_metadata(self.SETTINGS, tmp_root, "20250715_P3HT_Test")
        assert path.name == "20250715_P3HT_Test_metadata.json"

    def test_metadata_fields(self, tmp_root):
        path = write_run_metadata(self.SETTINGS, tmp_root, "20250715_P3HT_Test")
        with open(path) as f:
            meta = json.load(f)
        assert meta["sample_name"] == "P3HT"
        assert meta["electrolyte"] == "0.1M KPF6"
        assert meta["notes"] == "Test run"
        assert meta["data_folder"] == "20250715_P3HT_Test"

    def test_full_settings_saved(self, tmp_root):
        path = write_run_metadata(self.SETTINGS, tmp_root, "20250715_P3HT_Test")
        with open(path) as f:
            meta = json.load(f)
        assert meta["settings"]["cv_scan_rate"] == 100.0

    def test_run_started_present(self, tmp_root):
        path = write_run_metadata(self.SETTINGS, tmp_root, "20250715_P3HT_Test")
        with open(path) as f:
            meta = json.load(f)
        assert "run_started" in meta
        assert "T" in meta["run_started"]  # ISO 8601 format

    def test_creates_directory(self, tmp_root):
        subdir = "20250715_NewRun"
        write_run_metadata(self.SETTINGS, tmp_root, subdir)
        assert (tmp_root / subdir).is_dir()
