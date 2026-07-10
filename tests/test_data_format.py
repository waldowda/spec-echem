"""
Golden-file tests for the spec-echem 8-column output format.

These tests protect the file format that downstream UW analysis tools (OECT_processing) depend on.
Run before and after any refactoring of get_spectra() / data.py to confirm nothing changed.
"""
import os
import pytest
import pandas as pd

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden", "20250715_P3HT9505_KPF6")

# Column 6 is "Index" for CV/doping/dedoping/prededoping — but "Spectrum number" in spectra(N).txt.
# This inconsistency is intentional legacy behavior; OECT_processing depends on it.
COMMON_COLUMNS = [
    "Wavelength (nm)",
    "Absorbance",
    "Column 3 (a. u.)",
    "Column 4 (a. u.)",
    "Measured value (a.u.)",
    # column 6 varies — checked per-file below
    "Time (s)",
    "Corrected time (s)",
]

DARK_REF_PRESENT_ROWS = 1265   # rows 0..1264 have dark/ref data
TOTAL_ROWS = 1275


def load_golden(filename):
    path = os.path.join(GOLDEN_DIR, filename)
    return pd.read_csv(path, sep="\t")


# --- parametrize over all four files ---

@pytest.mark.parametrize("filename", [
    "CVspectra.txt",
    "spectra(7).txt",
    "dedopingspectra(7).txt",
    "prededopingspectra(0).txt",
])
def test_column_count(filename):
    df = load_golden(filename)
    assert df.shape[1] == 8, f"{filename}: expected 8 columns, got {df.shape[1]}"


@pytest.mark.parametrize("filename", [
    "CVspectra.txt",
    "spectra(7).txt",
    "dedopingspectra(7).txt",
    "prededopingspectra(0).txt",
])
def test_row_count(filename):
    df = load_golden(filename)
    assert df.shape[0] == TOTAL_ROWS, f"{filename}: expected {TOTAL_ROWS} rows, got {df.shape[0]}"


@pytest.mark.parametrize("filename", [
    "CVspectra.txt",
    "spectra(7).txt",
    "dedopingspectra(7).txt",
    "prededopingspectra(0).txt",
])
def test_tab_separator(filename):
    path = os.path.join(GOLDEN_DIR, filename)
    with open(path, encoding="utf-8") as f:
        first_line = f.readline()
    assert "\t" in first_line, f"{filename}: header line does not contain tab separator"
    assert "," not in first_line, f"{filename}: header line contains comma (wrong separator?)"


@pytest.mark.parametrize("filename,expected_col6", [
    ("CVspectra.txt",             "Index"),
    ("spectra(7).txt",            "Spectrum number"),
    ("dedopingspectra(7).txt",    "Index"),
    ("prededopingspectra(0).txt", "Index"),
])
def test_column_names(filename, expected_col6):
    df = load_golden(filename)
    cols = list(df.columns)
    expected = COMMON_COLUMNS[:5] + [expected_col6] + COMMON_COLUMNS[5:]
    assert cols == expected, f"{filename}: columns mismatch\n  got:      {cols}\n  expected: {expected}"


# --- dark/ref presence ---

@pytest.mark.parametrize("filename", [
    "CVspectra.txt",
    "spectra(7).txt",
    "dedopingspectra(7).txt",
    "prededopingspectra(0).txt",
])
def test_dark_ref_present_in_first_block(filename):
    df = load_golden(filename)
    dark = df["Column 3 (a. u.)"]
    ref  = df["Column 4 (a. u.)"]
    assert dark.iloc[:DARK_REF_PRESENT_ROWS].notna().all(), \
        f"{filename}: dark column has unexpected NaN in first {DARK_REF_PRESENT_ROWS} rows"
    assert ref.iloc[:DARK_REF_PRESENT_ROWS].notna().all(), \
        f"{filename}: ref column has unexpected NaN in first {DARK_REF_PRESENT_ROWS} rows"


@pytest.mark.parametrize("filename", [
    "CVspectra.txt",
    "spectra(7).txt",
    "dedopingspectra(7).txt",
    "prededopingspectra(0).txt",
])
def test_dark_ref_absent_after_first_block(filename):
    df = load_golden(filename)
    dark = df["Column 3 (a. u.)"]
    ref  = df["Column 4 (a. u.)"]
    assert dark.iloc[DARK_REF_PRESENT_ROWS:].isna().all(), \
        f"{filename}: dark column has unexpected values after row {DARK_REF_PRESENT_ROWS}"
    assert ref.iloc[DARK_REF_PRESENT_ROWS:].isna().all(), \
        f"{filename}: ref column has unexpected values after row {DARK_REF_PRESENT_ROWS}"


# --- numeric columns are actually numeric ---

@pytest.mark.parametrize("filename", [
    "CVspectra.txt",
    "spectra(7).txt",
    "dedopingspectra(7).txt",
    "prededopingspectra(0).txt",
])
def test_numeric_columns(filename):
    df = load_golden(filename)
    for col in ["Wavelength (nm)", "Absorbance", "Measured value (a.u.)", "Time (s)", "Corrected time (s)"]:
        assert pd.api.types.is_numeric_dtype(df[col]), \
            f"{filename}: column '{col}' is not numeric"


# --- filename convention ---

def test_expected_files_exist():
    for name in ["CVspectra.txt", "spectra(7).txt", "dedopingspectra(7).txt", "prededopingspectra(0).txt"]:
        path = os.path.join(GOLDEN_DIR, name)
        assert os.path.exists(path), f"Golden file missing: {name}"
