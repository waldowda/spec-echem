"""
Absorbance computation and file writing.
No Qt imports. No vendor SDK imports.
"""
import json
import re
from datetime import datetime
from typing import NamedTuple
import numpy as np
import pandas as pd
from pathlib import Path

from spec_echem.build_info import build_id
from spec_echem.gamry_data import (
    POTENTIAL_COL, CURRENT_COL, CV_COLUMNS, CHRONO_COLUMNS,
)

DATA_TYPE_CV = 1
DATA_TYPE_DOPING = 2
DATA_TYPE_DEDOPING = 3
DATA_TYPE_PREDEDOPING = 4


def compute_absorbance(spectra, dark, ref, wavelengths, timestamps):
    """
    Compute absorbance matrix from raw spectra.

    Args:
        spectra: list of 1D arrays, shape (n_pixels,) each
        dark: 1D array, dark spectrum
        ref: 1D array, reference/100%T spectrum
        wavelengths: 1D array of wavelength values
        timestamps: list of floats, Avantes timestamps in seconds

    Returns:
        absorb7: DataFrame indexed by wavelength (rows), relative timestamps (columns)
    """
    spectra_arr = np.array(spectra)  # (n_times, n_pixels)
    transmittance = (spectra_arr - dark) / (ref - dark)
    absorbance = -1 * np.log10(transmittance)

    absorb3_df = pd.DataFrame(absorbance)
    absorb4 = absorb3_df.T
    absorb5 = absorb4.set_index(wavelengths)

    initial = timestamps[0]
    timestamp_diff = [t - initial for t in timestamps]
    absorb6 = absorb5.T
    absorb6.index = timestamp_diff
    absorb7 = absorb6.T

    return absorb7


def _filename_for(data_type, run_number):
    return {
        DATA_TYPE_CV:          'CVspectra.txt',
        DATA_TYPE_DOPING:      f'spectra({run_number}).txt',
        DATA_TYPE_DEDOPING:    f'dedopingspectra({run_number}).txt',
        DATA_TYPE_PREDEDOPING: f'prededopingspectra({run_number}).txt',
    }[data_type]


# Reverse of _filename_for: recognize a saved spectra filename → (data_type, label base).
# Used to reload a past run for review (see discover_run_segments).
_SPECTRA_FILE_PATTERNS = [
    (re.compile(r'^CVspectra\.txt$'),                  DATA_TYPE_CV,          "CV"),
    (re.compile(r'^spectra\((\d+)\)\.txt$'),           DATA_TYPE_DOPING,      "Doping"),
    (re.compile(r'^dedopingspectra\((\d+)\)\.txt$'),   DATA_TYPE_DEDOPING,    "Dedoping"),
    (re.compile(r'^prededopingspectra\((\d+)\)\.txt$'), DATA_TYPE_PREDEDOPING, "Pre-dedoping"),
]


def read_spectra_absorbance(path):
    """Reconstruct the absorbance matrix from a saved 8-column spectra .txt.

    Inverse of the layout written by write_spectra_file: the file stacks one
    n-wavelength block per time point, with the already-computed Absorbance in
    column 2 and that block's elapsed time in 'Corrected time (s)'. Returns a
    DataFrame shaped exactly like compute_absorbance's absorb7 — wavelength index,
    corrected-time columns — so show_absorbance can plot a past run unchanged.
    No recomputation: the saved absorbance is used as-is.
    """
    df = pd.read_csv(path, sep='\t')
    n = df['Wavelength (nm)'].nunique()          # wavelengths per time block
    if n == 0:
        raise ValueError(f"{Path(path).name}: no wavelength data")
    n_times = len(df) // n
    if n_times == 0:
        raise ValueError(f"{Path(path).name}: fewer than one full "
                         f"{n}-wavelength block — not a spec-echem spectra file?")
    # Use only complete blocks; a truncated/aborted file may end mid-block, and a
    # partial trailing block is dropped rather than refusing the whole run.
    full = n_times * n
    wavelengths = df['Wavelength (nm)'].to_numpy()[:n]
    absorb = df['Absorbance'].to_numpy()[:full].reshape(n_times, n).T   # (n_wl, n_times)
    # First row of each block carries a non-NaN corrected time (only the last row
    # of a block is NaN), so column 0 of the reshaped time gives per-block times.
    corr = df['Corrected time (s)'].to_numpy()[:full].reshape(n_times, n)[:, 0]
    return pd.DataFrame(absorb, index=wavelengths, columns=corr)


def discover_run_segments(run_folder):
    """Scan a run folder for saved spectra files and return, in run order,
    (label, data_type, run_number, path) tuples — the inverse of _filename_for.

    Lets the GUI reload a completed run for review without re-running it. Only
    files directly in the folder are considered (not the dta/ subfolder).
    """
    folder = Path(run_folder)
    found = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        for rx, data_type, base in _SPECTRA_FILE_PATTERNS:
            m = rx.match(p.name)
            if not m:
                continue
            run_number = int(m.group(1)) if m.groups() else 0
            label = f"{base} {run_number}" if m.groups() else base
            found.append((label, data_type, run_number, p))
            break

    def sort_key(item):
        _, data_type, run_number, _ = item
        if data_type == DATA_TYPE_CV:
            return (0, 0, 0)
        if data_type == DATA_TYPE_PREDEDOPING:
            return (1, run_number, 0)
        sub = 0 if data_type == DATA_TYPE_DOPING else 1   # doping before dedoping
        return (2, run_number, sub)

    found.sort(key=sort_key)
    return found


def write_spectra_file(absorb7, spectra, dark, ref, wavelengths, timestamps,
                       data_type, run_number, data_root, added_path):
    """
    Write spectra data to a tab-separated file in the UW 8-column format.

    Column 6 is 'Spectrum number' for doping (DATA_TYPE_DOPING=2), 'Index' for all others.
    Dark and ref columns are populated only for time_point 0; NaN elsewhere.
    The last row of each time block has NaN in the time columns — this matches the
    original notebook behavior (range(1, spectrum_points) produces n-1 time entries).

    Args:
        absorb7: DataFrame from compute_absorbance()
        spectra: list of 1D arrays (raw spectra, one per time point)
        dark: 1D array, dark spectrum
        ref: 1D array, reference spectrum
        wavelengths: 1D array of wavelength values
        timestamps: list of floats, Avantes timestamps in seconds
        data_type: int, one of DATA_TYPE_* constants
        run_number: int, cycle counter for filename
        data_root: str or Path, base data directory
        added_path: str, subfolder name (format: YYYYMMDD_Description)

    Returns:
        Path: path to the file written
    """
    spectra_arr = np.array(spectra)
    n = len(wavelengths)
    n_times = len(timestamps)
    col6_name = 'Spectrum number' if data_type == DATA_TYPE_DOPING else 'Index'

    initial = timestamps[0]
    timestamp_diff = [t - initial for t in timestamps]

    output_df_all = None

    for time_point in range(n_times):
        time_value = timestamp_diff[time_point]
        corrected_value = time_value - timestamp_diff[0]  # timestamp_diff[0] == 0

        # Time columns: n-1 entries, last row NaN (preserves original range(1, n) behavior)
        time_col = np.empty(n)
        time_col[:n - 1] = time_value
        time_col[n - 1] = np.nan

        corrected_col = np.empty(n)
        corrected_col[:n - 1] = corrected_value
        corrected_col[n - 1] = np.nan

        output_df = pd.DataFrame({
            'Wavelength (nm)':       wavelengths,
            'Absorbance':            absorb7.iloc[:, time_point].values,
            'Column 3 (a. u.)':      dark if time_point == 0 else np.full(n, np.nan),
            'Column 4 (a. u.)':      ref  if time_point == 0 else np.full(n, np.nan),
            'Measured value (a.u.)': spectra_arr[time_point],
            col6_name:               time_point + 1,
            'Time (s)':              time_col,
            'Corrected time (s)':    corrected_col,
        })

        if output_df_all is None:
            output_df_all = output_df
        else:
            output_df_all = pd.concat([output_df_all, output_df], axis=0, ignore_index=True)

    path = Path(data_root) / added_path / _filename_for(data_type, run_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    output_df_all.to_csv(path, header=True, index=False, sep='\t')

    return path


def _echem_filename_for(data_type, run_number):
    """Clean-txt echem filename — the names the converter/Raj already expect."""
    return {
        DATA_TYPE_CV:          'CV.txt',
        DATA_TYPE_DOPING:      f'steps({run_number}).txt',
        DATA_TYPE_DEDOPING:    f'dedoping({run_number}).txt',
        DATA_TYPE_PREDEDOPING: f'prededoping({run_number}).txt',
    }[data_type]


def echem_txt_path(run_folder, data_type, run_number):
    """Full path to a segment's clean echem .txt inside an existing run folder.
    Public accessor so the GUI can locate the file that write_echem_file wrote."""
    return Path(run_folder) / _echem_filename_for(data_type, run_number)


def _echem_dta_path(data_type, run_number, data_root, added_path):
    """Native-.dta path — parallel to the clean txt, in a `dta/` subfolder.
    Lowercase .dta extension matches the Gamry/toolkitpy convention."""
    name = {
        DATA_TYPE_CV:          'CV.dta',
        DATA_TYPE_DOPING:      f'steps({run_number}).dta',
        DATA_TYPE_DEDOPING:    f'dedoping({run_number}).dta',
        DATA_TYPE_PREDEDOPING: f'prededoping({run_number}).dta',
    }[data_type]
    return Path(data_root) / added_path / 'dta' / name


class EchemData(NamedTuple):
    """One segment's electrochemistry, in vendor-neutral terms.

    Every potentiostat driver returns THIS from last_data()/live_data(), whatever
    its SDK hands back. The writer below used to read the toolkitpy field names
    (`vf`, `im`, `time`) straight out of a Gamry structured array, which meant a
    non-Gamry driver had to fabricate Gamry field names to be writable. Naming the
    three quantities once, here, is what lets a second driver exist.

    Arrays are parallel and equal length; `time` is the device clock in seconds
    (the writer rebases it), potential in volts, current in amperes.
    """
    time: np.ndarray
    potential: np.ndarray
    current: np.ndarray


def write_echem_file(echem, data_type, run_number, data_root, added_path):
    """
    Write the clean analysis .txt for one Python-mode segment from an EchemData,
    matching the exact column contract the reader (gamry_data.py) enforces.

      CV                       -> CV.txt            [potential, current] (cycles concatenated)
      doping/dedoping/prededope -> steps/dedoping/prededoping(N).txt
                                  [Time (s), Corrected time (s), potential, current, Index]

    Time (s) and Corrected time (s) both start at 0 (device `time` minus its first
    sample) — no vestigial +100 offset (downstream keys off Corrected time by name).

    Args:
        echem: EchemData — parallel time/potential/current arrays
        data_type: int, one of DATA_TYPE_* constants
        run_number: int, cycle counter for the filename
        data_root: str or Path, base data directory
        added_path: str, subfolder name (format: YYYYMMDD_Description)

    Returns:
        Path: path to the file written
    """
    potential = np.asarray(echem.potential)
    current = np.asarray(echem.current)

    if data_type == DATA_TYPE_CV:
        df = pd.DataFrame({POTENTIAL_COL: potential, CURRENT_COL: current})[CV_COLUMNS]
    else:
        t = np.asarray(echem.time)
        rel = t - t[0] if len(t) else t
        df = pd.DataFrame({
            'Time (s)':           rel,
            'Corrected time (s)': rel,
            POTENTIAL_COL:        potential,
            CURRENT_COL:          current,
            'Index':              range(len(current)),
        })[CHRONO_COLUMNS]

    path = Path(data_root) / added_path / _echem_filename_for(data_type, run_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, header=True, index=False, sep='\t')

    return path


def write_run_metadata(settings, data_root, added_path, instruments=None):
    """
    Write a metadata JSON file to the run folder at experiment start.
    Captures sample info, notes, and all settings used — making the data
    folder self-documenting.

    File written: {data_root}/{added_path}/{added_path}_metadata.json

    Args:
        settings: dict from load_settings() or DEFAULT_SETTINGS
        data_root: str or Path, base data directory
        added_path: str, subfolder name (format: YYYYMMDD_Description)
        instruments: optional dict of instrument identities (spectrometer /
            potentiostat serials) as reported at Connect. Omitted when unknown —
            the settings say how the run was configured, not what it ran on.

    Returns:
        Path: path to the metadata file written
    """
    folder = Path(data_root) / added_path
    folder.mkdir(parents=True, exist_ok=True)

    metadata = {
        # Which code wrote this folder. Settings alone don't say — and behaviour has
        # changed across versions (the wavelength crop, for one).
        "spec_echem_version": build_id(),
        "run_started": datetime.now().isoformat(timespec="seconds"),
        "data_folder": added_path,
        "sample_name": settings.get("sample_name", ""),
        "electrolyte": settings.get("electrolyte", ""),
        "notes": settings.get("notes", ""),
        "settings": settings,
    }
    if instruments:
        metadata["instruments"] = instruments

    path = folder / f"{added_path}_metadata.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return path
