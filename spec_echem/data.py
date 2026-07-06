"""
Absorbance computation and file writing.
No Qt imports. No vendor SDK imports.
"""
import json
from datetime import datetime
import numpy as np
import pandas as pd
from pathlib import Path

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


def _acq_field(acq_data, name):
    """Pull a named field from the toolkit's numpy structured array (defensive)."""
    names = acq_data.dtype.names or ()
    if name not in names:
        raise ValueError(f"acq_data missing field '{name}'; got {list(names)}")
    return np.asarray(acq_data[name])


def write_echem_file(acq_data, data_type, run_number, data_root, added_path):
    """
    Write the clean analysis .txt for one Python-mode segment straight from the
    toolkit's acq_data() structured array — no gamry_parser, matching the exact
    column contract the reader (gamry_data.py) enforces.

      CV                       -> CV.txt            [potential, current] (cycles concatenated)
      doping/dedoping/prededope -> steps/dedoping/prededoping(N).txt
                                  [Time (s), Corrected time (s), potential, current, Index]

    Time (s) and Corrected time (s) both start at 0 (device `time` minus its first
    sample) — no vestigial +100 offset (downstream keys off Corrected time by name).

    Args:
        acq_data: numpy structured array from curve.acq_data() (fields vf, im, time)
        data_type: int, one of DATA_TYPE_* constants
        run_number: int, cycle counter for the filename
        data_root: str or Path, base data directory
        added_path: str, subfolder name (format: YYYYMMDD_Description)

    Returns:
        Path: path to the file written
    """
    potential = _acq_field(acq_data, 'vf')
    current = _acq_field(acq_data, 'im')

    if data_type == DATA_TYPE_CV:
        df = pd.DataFrame({POTENTIAL_COL: potential, CURRENT_COL: current})[CV_COLUMNS]
    else:
        t = _acq_field(acq_data, 'time')
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


def write_run_metadata(settings, data_root, added_path):
    """
    Write a metadata JSON file to the run folder at experiment start.
    Captures sample info, notes, and all settings used — making the data
    folder self-documenting.

    File written: {data_root}/{added_path}/{added_path}_metadata.json

    Args:
        settings: dict from load_settings() or DEFAULT_SETTINGS
        data_root: str or Path, base data directory
        added_path: str, subfolder name (format: YYYYMMDD_Description)

    Returns:
        Path: path to the metadata file written
    """
    folder = Path(data_root) / added_path
    folder.mkdir(parents=True, exist_ok=True)

    metadata = {
        "run_started": datetime.now().isoformat(timespec="seconds"),
        "data_folder": added_path,
        "sample_name": settings.get("sample_name", ""),
        "electrolyte": settings.get("electrolyte", ""),
        "notes": settings.get("notes", ""),
        "settings": settings,
    }

    path = folder / f"{added_path}_metadata.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return path
