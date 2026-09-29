"""
Rebuild a run's HDF5 from the ascii it already has.

In the PACKAGE rather than in examples/ because two callers need it: the
`examples/ascii_to_h5.py` command line and the Results tab's "Convert to HDF5"
button. Every run recorded before 2026-09-29 is ascii-only, so this is how they stop
being stranded.

The 8-column format carries everything the schema needs — absorbance, the dark and
reference (cols 3-4, which read_spectra_absorbance deliberately never reads), raw
counts (col 5) and corrected time — and the echem comes from the separate
CV.txt / steps(N).txt / dedoping(N).txt / prededoping(N).txt.

**`time_spectrometer` cannot be recovered.** write_spectra_file computes `Time (s)`
and `Corrected time (s)` identically, so the spectrometer's own clock was thrown
away at write time. Backfilled files OMIT that dataset and record
`time_spectrometer_recovered = False` rather than store a copy of `time` under a
name that would then be wrong.

Read-only with respect to the ascii: it never modifies or deletes a .txt.
"""
import os

import numpy as np
import pandas as pd

from spec_echem.data import (
    DATA_TYPE_CV, EchemData, discover_run_segments, echem_txt_path, h5_path,
    write_segment_h5,
)
from spec_echem.gamry_data import (
    CURRENT_COL, POTENTIAL_COL, read_chrono, read_cv,
)
from spec_echem.logging_config import get_run_logger


def _report(*args):
    """Progress goes to the log, not to stdout: the GUI calls this too."""
    get_run_logger().info(" ".join(str(a) for a in args))


def read_spectra_full(path):
    """All of an 8-column spectra file: (absorbance, counts, dark, ref, wl, times).

    read_spectra_absorbance() deliberately reads only three columns, because on the
    32-bit env reading all seven numeric ones raised MemoryError. This reads them
    all, so it is for a 64-bit machine doing a migration, not for the acquisition
    path.
    """
    df = pd.read_csv(path, sep="\t")
    wl_all = df["Wavelength (nm)"].to_numpy()
    n_wl = df["Wavelength (nm)"].nunique()
    if n_wl == 0:
        raise ValueError(f"{os.path.basename(path)}: no wavelength data")
    n_t = len(df) // n_wl
    if n_t == 0:
        raise ValueError(f"{os.path.basename(path)}: not a spec-echem spectra file")
    full = n_t * n_wl

    wl = wl_all[:n_wl]
    absorb = df["Absorbance"].to_numpy()[:full].reshape(n_t, n_wl).T
    counts = df["Measured value (a.u.)"].to_numpy()[:full].reshape(n_t, n_wl).T
    # Dark and reference are written only into the FIRST time block.
    dark = df["Column 3 (a. u.)"].to_numpy()[:n_wl]
    ref = df["Column 4 (a. u.)"].to_numpy()[:n_wl]
    times = df["Corrected time (s)"].to_numpy()[:full].reshape(n_t, n_wl)[:, 0]

    absorb_df = pd.DataFrame(absorb, index=wl, columns=times)
    # write_segment_h5 wants spectra as (n_times, n_wl), the shape acquisition hands it.
    return absorb_df, list(counts.T), dark, ref, wl, list(times)

def read_echem(run_folder, data_type, run_number):
    """The segment's echem as EchemData, or None if it was never written."""
    path = echem_txt_path(run_folder, data_type, run_number)
    if not os.path.exists(path):
        return None
    try:
        if data_type == DATA_TYPE_CV:
            df = read_cv(path)
            # A CV.txt carries no time column -- it is potential vs current. Index
            # stands in, and is marked as such by the caller.
            return EchemData(time=np.arange(len(df), dtype=float),
                             potential=df[POTENTIAL_COL].to_numpy(),
                             current=df[CURRENT_COL].to_numpy())
        df = read_chrono(path)
        return EchemData(time=df["Corrected time (s)"].to_numpy(),
                         potential=df[POTENTIAL_COL].to_numpy(),
                         current=df[CURRENT_COL].to_numpy())
    except (OSError, ValueError, KeyError) as exc:
        pass
        return None

def backfill_run(folder, compression=0, keep=False):
    """Rebuild every segment's HDF5 from the ascii. -> dict summary.

    Returns {"name", "segments", "ascii_bytes", "h5_bytes", "files"} so a caller can
    report it however it likes — the command line prints, the GUI shows a dialog.
    """
    folder = os.path.abspath(os.path.expanduser(str(folder)))
    name = os.path.basename(folder.rstrip(os.sep))
    segments = discover_run_segments(folder)
    if not segments:
        return {"name": name, "segments": 0, "ascii_bytes": 0, "h5_bytes": 0,
                "files": []}

    ascii_bytes = 0
    written = []
    for label, data_type, run_number, path in segments:
        ascii_bytes += os.path.getsize(path)
        target = h5_path(folder, data_type, name)
        if target.exists() and target not in written and not keep:
            os.remove(target)          # rebuild rather than append to a stale file

        absorb, spectra, dark, ref, wl, times = read_spectra_full(path)
        echem = read_echem(folder, data_type, run_number)
        seg = type("Seg", (), {"label": label, "num_points": len(times),
                               "delta_time": None, "trigger": None})()
        out = write_segment_h5(absorb, spectra, dark, ref, wl, times, echem,
                               data_type, run_number, os.path.dirname(folder), name,
                               segment=seg, compression=compression)
        if out is None:                # no h5py — write_segment_h5 has said so
            return {"name": name, "segments": 0, "ascii_bytes": 0, "h5_bytes": 0,
                    "files": []}
        if target not in written:
            written.append(target)
        _report(f"{name}: {label} -> {target.name}"
                f"{' + echem' if echem is not None else ''}")

    _mark_backfilled(written)
    h5_bytes = sum(os.path.getsize(p) for p in written)
    _report(f"{name}: {ascii_bytes/1e6:.1f} MB ascii -> {h5_bytes/1e6:.1f} MB h5")
    return {"name": name, "segments": len(segments), "ascii_bytes": ascii_bytes,
            "h5_bytes": h5_bytes, "files": written}


def _mark_backfilled(paths):
    """Say in the file that it came from ascii, and that one axis is missing.

    Better than silently omitting time_spectrometer: a reader finding no such dataset
    should be able to tell "this run never had it" from "this writer forgot".
    """
    import h5py
    for path in paths:
        with h5py.File(path, "a") as f:
            f.attrs["backfilled_from_ascii"] = True
            f.attrs["time_spectrometer_recovered"] = False
            f.attrs["backfill_note"] = (
                "Rebuilt from the 8-column ascii. The spectrometer's own clock was "
                "not recoverable: write_spectra_file computes 'Time (s)' and "
                "'Corrected time (s)' identically, so only the rebased axis "
                "survived. time_spectrometer is therefore ABSENT from these files "
                "rather than holding a copy of `time`.")
            # REMOVE it rather than leave a copy of `time` under that name: the
            # writer is handed the rebased axis (it is all the ascii kept), so the
            # dataset would otherwise exist and be wrong. An attribute nobody reads
            # does not fix a lie in the data. Absent is checkable; wrong is not.
            for key in list(f):
                node = f[key]
                if isinstance(node, h5py.Group) and "time_spectrometer" in node:
                    del node["time_spectrometer"]
