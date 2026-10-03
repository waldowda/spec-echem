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

try:
    import h5py
    H5PY_AVAILABLE = True
    H5PY_IMPORT_ERROR = None
except ImportError as exc:
    h5py = None
    H5PY_AVAILABLE = False
    # Kept for the same reason as AVASPEC_IMPORT_ERROR and TOOLKITPY_IMPORT_ERROR:
    # "not available" is the same words for a missing package and for a 32-bit
    # environment that cannot have one, and they are fixed differently. Discarding
    # the message cost a session on 2026-09-25 when a greyed-out Gamry radio read as
    # a dead instrument and was actually the wrong conda env.
    #
    # h5py is OPTIONAL on purpose: win32 cp37 wheels stop at h5py 2.10.0 (checked
    # against PyPI 2026-09-29), so `SpecEchem32` must pin that version and may not
    # have it at all. Nothing here may require h5py to import or to run.
    H5PY_IMPORT_ERROR = str(exc)

from spec_echem.build_info import build_id
from spec_echem.logging_config import get_run_logger
from spec_echem.gamry_data import (
    POTENTIAL_COL, CURRENT_COL, CV_COLUMNS, CHRONO_COLUMNS,
)

DATA_TYPE_CV = 1
DATA_TYPE_DOPING = 2
DATA_TYPE_DEDOPING = 3
DATA_TYPE_PREDEDOPING = 4


def segment_potential(settings, data_type, run_number):
    """The potential a chrono segment is held at, or None for a CV (which sweeps).

    ONE definition, because the doping ladder's arithmetic was already written out
    in both potentiostat backends and the Results tab wanted a third copy for its
    graph titles. A label that disagrees with what was applied is worse than no
    label, so they all come from here.
    """
    # .get, not [] : a run loaded from disk whose metadata is missing has no ladder
    # to read, and None ("no label") is the honest answer. Returning a potential
    # computed from whatever happens to be in the Parameters tab would name a value
    # that was never applied -- which is exactly the mislabel this function exists
    # to prevent.
    if data_type == DATA_TYPE_PREDEDOPING:
        return settings.get("prededoping_potential")
    if data_type == DATA_TYPE_DOPING:
        start = settings.get("doping_potential_start")
        step = settings.get("doping_potential_step")
        if start is None or step is None:
            return None
        return start + run_number * step
    if data_type == DATA_TYPE_DEDOPING:
        return settings.get("dedoping_potential")
    return None


def segment_potential_text(settings, data_type, run_number, decimals=3):
    """A short potential for a graph title: '+0.600 V', or a CV's swept range.

    `decimals` exists for the segment dropdowns, which ask for 2. The measured
    potential runs a few tenths of a millivolt below setpoint on every rung, so at 3
    decimals whichever rung lands just under the half-millivolt boundary reads one
    millivolt low -- +0.699 beside +0.600, from 0.699498. True, but in a SELECTOR it
    reads as a different rung. Titles, tables and exports keep 3.
    """
    if data_type == DATA_TYPE_CV:
        return (f"{settings['cv_limit1_v']:+.{decimals}f} to "
                f"{settings['cv_limit2_v']:+.{decimals}f} V"
                if "cv_limit1_v" in settings else "")
    v = segment_potential(settings, data_type, run_number)
    return "" if v is None else f"{v:+.{decimals}f} V"




def resolve_data_root(data_root):
    """Expand `~` in a data root, so the default is not one machine's account name.

    `Path("~/specechem_data")` does NOT expand on its own — it would create a literal
    directory called "~" beside wherever the app was launched. Every write path goes
    through here so that cannot happen.
    """
    return Path(data_root).expanduser()


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
    # Only the three columns this needs, of the eight in the file. MEASURED: on the
    # 32-bit SpecEchem32 env a 760265-row file allocated 40.6 MiB reading all seven
    # numeric columns and raised MemoryError on a second run; three columns is ~17.
    # The dark/reference/raw columns are not used to rebuild the absorbance matrix.
    df = pd.read_csv(path, sep='\t',
                     usecols=['Wavelength (nm)', 'Absorbance', 'Corrected time (s)'])
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


def read_segment_h5(path, run_number=0):
    """One cycle's absorbance from an .h5, shaped exactly like the ascii reader's.

    Deliberately the same return as read_spectra_absorbance(): a DataFrame indexed
    by wavelength with corrected-time columns, so anything that plots a past run
    works unchanged whichever file it came from. That equivalence is what the
    round-trip test pins, and it is what will eventually license retiring the ascii.

    Reads the STORED absorbance rather than recomputing it from counts. The
    2026-09-04 shutter-closed run produced 8982 NaN and 513 inf, and those are
    correct outputs of compute_absorbance -- reproducing them would mean
    reproducing its arithmetic exactly, forever.
    """
    if not H5PY_AVAILABLE:
        raise RuntimeError(f"h5py is not importable: {H5PY_IMPORT_ERROR}")
    key = str(int(run_number))
    with h5py.File(path, "r") as f:
        if key not in f:
            raise ValueError(f"{Path(path).name}: no cycle {key} "
                             f"(has {sorted(k for k in f if k != 'wavelength')})")
        g = f[key]
        absorb = np.asarray(g["absorbance_vs_time"])
        wavelengths = np.asarray(f["wavelength"])
        times = np.asarray(g["time"])
    return pd.DataFrame(absorb, index=wavelengths, columns=times)


def discover_run_h5(run_folder):
    """[(label, data_type, run_number, path)] for the .h5 files in a run folder.

    Mirrors discover_run_segments, in the same run order, so the Results tab can
    eventually open either. One file per TYPE holding several cycles, so a file
    yields several entries -- unlike the ascii, which is one file per segment.
    """
    if not H5PY_AVAILABLE:
        return []
    folder = Path(run_folder)
    found = []
    for data_type, base in (
            (DATA_TYPE_CV, "CV"), (DATA_TYPE_PREDEDOPING, "Pre-dedoping"),
            (DATA_TYPE_DOPING, "Doping"), (DATA_TYPE_DEDOPING, "Dedoping")):
        path = folder / _h5_filename_for(data_type, folder.name)
        if not path.is_file():
            continue
        try:
            with h5py.File(path, "r") as f:
                cycles = sorted((int(k) for k in f if k != "wavelength"))
                labels = {c: f[str(c)].attrs.get("label") for c in cycles}
        except (OSError, ValueError):
            continue          # an unreadable file is skipped, never fatal
        for cycle in cycles:
            label = labels.get(cycle) or (
                base if data_type == DATA_TYPE_CV else f"{base} {cycle}")
            found.append((str(label), data_type, cycle, path))
    found.sort(key=lambda item: segment_sort_key(item[1], item[2]))
    return found


def segment_sort_key(data_type, run_number):
    """Run order: CV, then pre-dedoping, then doping/dedoping pairs by cycle.

    ONE definition, for the same reason segment_potential() is: the H5 discovery
    below needs the identical ordering, and a second copy would be a second thing to
    keep in step.
    """
    if data_type == DATA_TYPE_CV:
        return (0, 0, 0)
    if data_type == DATA_TYPE_PREDEDOPING:
        return (1, run_number, 0)
    sub = 0 if data_type == DATA_TYPE_DOPING else 1       # doping before dedoping
    return (2, run_number, sub)


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

    found.sort(key=lambda item: segment_sort_key(item[1], item[2]))
    return found


def write_spectra_file(absorb7, spectra, dark, ref, wavelengths, timestamps,
                       data_type, run_number, data_root, added_path):
    """
    Write spectra data to a tab-separated file in the 8-column format.

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

    path = resolve_data_root(data_root) / added_path / _filename_for(data_type, run_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    output_df_all.to_csv(path, header=True, index=False, sep='\t')

    return path


def _echem_filename_for(data_type, run_number):
    """Clean-txt echem filename — the names the converter and OECT_processing already expect."""
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
    return resolve_data_root(data_root) / added_path / 'dta' / name


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

    path = resolve_data_root(data_root) / added_path / _echem_filename_for(data_type, run_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, header=True, index=False, sep='\t')

    return path


# HDF5 filename per segment TYPE — one file per type, all that type's cycles inside.
# Fourth map beside _filename_for / _echem_filename_for / _echem_dta_path, same idiom.
# The {added_path}_ prefix matches {added_path}_metadata.json and {added_path}_log.log,
# so a file that gets moved still names its own run.
def _h5_filename_for(data_type, added_path):
    return {
        DATA_TYPE_CV:          f'{added_path}_cv.h5',
        DATA_TYPE_DOPING:      f'{added_path}_doping.h5',
        DATA_TYPE_DEDOPING:    f'{added_path}_dedoping.h5',
        DATA_TYPE_PREDEDOPING: f'{added_path}_prededoping.h5',
    }[data_type]


_H5_TYPE_NAME = {
    DATA_TYPE_CV: "CV",
    DATA_TYPE_DOPING: "Doping",
    DATA_TYPE_DEDOPING: "Dedoping",
    DATA_TYPE_PREDEDOPING: "Pre-dedoping",
}

H5_SCHEMA_VERSION = "1"


def h5_path(run_folder, data_type, added_path=None):
    """Full path to a segment type's .h5 inside an existing run folder.
    Public accessor, mirroring echem_txt_path."""
    folder = Path(run_folder)
    return folder / _h5_filename_for(data_type, added_path or folder.name)


def counts_dtype(counts):
    """uint16 when the counts really are integral and in range, float32 otherwise.

    The ADC is 16-bit, so a SINGLE scan is an exact integer — but `scan_averages`
    defaults to 200 and an averaged count is not. MEASURED 2026-09-29 on a real CV
    (20260925_test10): a fractional part of up to exactly 0.5, so a uint16 cast would
    misstate every half-count. This rule is load-bearing, not a nicety; the first
    version of that measurement used uint16 and was quietly lossy.

        KNOWING COMPROMISE, 2026-09-29: the float32 branch is NOT bit-exact. Measured on
    a real run, counts round-trip to 1.9e-03 absolute at ~54,700 counts -- 3.6e-08
    RELATIVE, seven orders below the shot noise of sqrt(54700) ~ 234. float64 would
    be bit-exact at ~+50% file size and was declined. See docs/data-format.md, "The
    one knowing compromise". The uint16 branch IS exact, so a run at one scan average
    loses nothing at all.
    """
    arr = np.asarray(counts, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size and np.all(finite == np.rint(finite)) \
            and finite.min() >= 0 and finite.max() <= np.iinfo(np.uint16).max:
        return np.uint16
    return np.float32


def _h5_dataset(group, name, data, dtype=None, units=None, dims=None,
                compression=0, description=None):
    """One dataset plus the attributes that make it self-describing.

    `units` on everything and `dims` on every 2-D array: attributes are free and
    nobody reads a spec.
    """
    kwargs = {}
    if compression:
        kwargs = dict(compression="gzip", compression_opts=int(compression),
                      chunks=True)
    arr = np.asarray(data)
    if dtype is not None:
        arr = arr.astype(dtype)
    ds = group.create_dataset(name, data=arr, **kwargs)
    if units:
        ds.attrs["units"] = units
    if dims:
        ds.attrs["dims"] = dims
    if description:
        ds.attrs["description"] = description
    return ds


def _h5_root_attrs(f, data_type, added_path, run_folder):
    """Provenance, read from the run's own metadata JSON rather than rebuilt.

    THE ANTI-DIVERGENCE MEASURE: the JSON is the single source, and this copies it
    verbatim instead of reassembling it from a settings dict, so the two cannot drift.
    It also needs no new plumbing — run_one_segment has no settings or instruments,
    but the JSON is guaranteed on disk, written at run start.
    """
    f.attrs["schema_version"] = H5_SCHEMA_VERSION
    f.attrs["run_id"] = added_path
    f.attrs["data_type"] = int(data_type)
    f.attrs["data_type_name"] = _H5_TYPE_NAME[data_type]
    f.attrs["build_id"] = build_id()

    meta_path = Path(run_folder) / f"{added_path}_metadata.json"
    try:
        text = meta_path.read_text(encoding="utf-8")
    except OSError:
        return                      # a run without metadata is still worth writing
    f.attrs["metadata_json"] = text
    try:
        meta = json.loads(text)
    except ValueError:
        return
    # Promote the handful worth seeing at a glance, so HDFView and h5dump show them
    # without anyone parsing JSON. The full document stays above regardless.
    for key in ("run_started", "sample_name", "electrolyte", "notes"):
        if meta.get(key) is not None:
            f.attrs[key] = str(meta[key])
    for key, value in (meta.get("instruments") or {}).items():
        f.attrs[str(key)] = str(value)


_h5py_warning_said = False


def _warn_h5py_missing_once():
    """Say ONCE per process why no .h5 appeared.

    Silence here is the exact failure this project keeps paying for: on 2026-09-29 a
    simulated run wrote its four ascii files and no HDF5 at all, with nothing in any
    log, because the environment running the GUI had no h5py. That is the same shape
    as a greyed-out Gamry radio reading as a dead instrument. A feature that declines
    to run must say so.
    """
    global _h5py_warning_said
    if _h5py_warning_said:
        return
    _h5py_warning_said = True
    get_run_logger().warning(
        "No HDF5 files will be written: h5py is not importable in this environment "
        "(%s). The ascii files are unaffected. Install it with `pip install h5py` "
        "(32-bit SpecEchem32 must pin h5py==2.10.0).", H5PY_IMPORT_ERROR)


def write_segment_h5(absorb7, spectra, dark, ref, wavelengths, timestamps, echem,
                     data_type, run_number, data_root, added_path,
                     segment=None, settings=None, compression=0):
    """Append one segment to its per-type .h5. Returns the Path, or None if h5py
    is unavailable.

    Written IN ADDITION to the ascii, never instead: docs/data-format.md is the
    authority and the downstream reader depends on those names. Retiring the text is
    a later decision resting on the round-trip evidence this writer makes possible.

    One file per segment TYPE, all that type's cycles inside as groups keyed by CYCLE
    NUMBER — never by potential. A potential repeats (every dedoping step shares one),
    the series need not be monotonic, and a future CA scheme need not be an ordered
    ladder at all: potential is an attribute of a cycle, never its address.
    """
    if not H5PY_AVAILABLE:
        _warn_h5py_missing_once()
        return None

    folder = resolve_data_root(data_root) / added_path
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _h5_filename_for(data_type, added_path)

    wl = np.asarray(wavelengths, dtype=float)
    counts = np.asarray(spectra, dtype=float).T          # (n_times, n_px) -> (n_wl, n_t)
    absorbance = np.asarray(absorb7, dtype=float)        # already (n_wl, n_t)
    stamps = np.asarray(timestamps, dtype=float)

    with h5py.File(path, "a") as f:
        if not f.attrs.get("schema_version"):
            _h5_root_attrs(f, data_type, added_path, folder)
        if "wavelength" not in f:
            _h5_dataset(f, "wavelength", wl, units="nm")

        key = str(int(run_number))
        if key in f:
            del f[key]                      # a re-run of one segment replaces it
        g = f.create_group(key)

        g.attrs["run_number"] = int(run_number)
        if segment is not None:
            for attr in ("label", "num_points", "delta_time", "trigger"):
                value = getattr(segment, attr, None)
                if value is not None:
                    g.attrs[attr] = value
        # potential_set, never a bare `potential`: the bare name belongs to the
        # MEASURED trace in echem/ and must mean one thing in one file.
        if settings is not None:
            v = segment_potential(settings, data_type, run_number)
            if v is not None:
                g.attrs["potential_set"] = float(v)
        if echem is not None and len(np.asarray(echem.potential)):
            g.attrs["potential_measured"] = float(
                np.nanmedian(np.asarray(echem.potential, dtype=float)))

        _h5_dataset(g, "counts_vs_time", counts, dtype=counts_dtype(counts),
                    units="counts", dims="wavelength x time",
                    compression=compression)
        _h5_dataset(g, "absorbance_vs_time", absorbance, dtype=np.float32,
                    units="-log10(T)", dims="wavelength x time",
                    compression=compression)
        _h5_dataset(g, "dark", dark, units="counts")
        _h5_dataset(g, "reference", ref, units="counts")
        _h5_dataset(g, "time", stamps - stamps[0] if stamps.size else stamps,
                    units="s",
                    description="seconds from this segment's first spectrum")
        _h5_dataset(g, "time_spectrometer", stamps, units="s",
                    description="the spectrometer's own clock, unrebased; no known "
                                "relation to wall time or to the echem clock")

        if echem is not None:
            e = g.create_group("echem")
            _h5_dataset(e, "time", echem.time, units="s",
                        description="potentiostat clock, rebased to 0; NOT the same "
                                    "length or clock as the spectra axis")
            _h5_dataset(e, "potential", echem.potential, units="V")
            _h5_dataset(e, "current", echem.current, units="A")

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
    folder = resolve_data_root(data_root) / added_path
    folder.mkdir(parents=True, exist_ok=True)

    metadata = {
        # Which code wrote this folder. Settings alone don't say — and behavior has
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
