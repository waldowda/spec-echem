"""
Build HDF5 files from a run folder that already exists as ascii.

Two jobs at once:

1. **Migration.** Every run already on disk stays useful when the H5 becomes the
   primary format. Nothing has to be re-measured.
2. **Inspection with REAL data.** A simulated run cannot exercise the `echem/` half
   of the schema — External mode has no potentiostat, so there is no echem to store.
   A past Python-mode run has it, so this produces a complete file.

The 8-column ascii carries everything the schema needs: absorbance (col 2), the dark
and reference (cols 3-4), raw counts (col 5), and corrected time (col 8). The echem
arrives from the separate CV.txt / steps(N).txt / dedoping(N).txt / prededoping(N).txt.

**ONE thing cannot be recovered: `time_spectrometer`.** `write_spectra_file` computes
`Time (s)` and `Corrected time (s)` identically — a leftover from when `Time` carried
a +100 offset — so the spectrometer's own clock was already thrown away at write time.
Backfilled files record that in a `time_spectrometer_recovered = False` attribute
rather than inventing the axis or silently omitting it.

    python examples/ascii_to_h5.py ~/specechem_data/20260925_test10
    python examples/ascii_to_h5.py ~/specechem_data/20260925_test10 --gzip 4
    python examples/ascii_to_h5.py ~/specechem_data/*/ --dry-run

Read-only with respect to the ascii: it never modifies or deletes a .txt. Existing
.h5 files are replaced unless --keep is given.
"""
import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np                                                    # noqa: E402
import pandas as pd                                                   # noqa: E402

from spec_echem.data import (                                         # noqa: E402
    DATA_TYPE_CV, EchemData, discover_run_segments, echem_txt_path, h5_path,
    write_segment_h5, H5PY_AVAILABLE, H5PY_IMPORT_ERROR,
)
from spec_echem.gamry_data import (                                   # noqa: E402
    CURRENT_COL, POTENTIAL_COL, read_chrono, read_cv,
)


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
        print(f"      echem not readable ({exc}) — writing the spectra half only")
        return None


def convert_run(folder, compression=0, dry_run=False, keep=False):
    folder = os.path.abspath(os.path.expanduser(folder))
    name = os.path.basename(folder.rstrip(os.sep))
    segments = discover_run_segments(folder)
    if not segments:
        print(f"{name}: no spec-echem spectra files here — skipped")
        return 0

    print(f"\n{name}   ({len(segments)} segments)")
    ascii_bytes = h5_bytes = 0
    written = set()
    for label, data_type, run_number, path in segments:
        ascii_bytes += os.path.getsize(path)
        target = h5_path(folder, data_type, name)
        if os.path.exists(target) and target not in written and not keep and not dry_run:
            os.remove(target)          # rebuild rather than append to a stale file
        if dry_run:
            print(f"   {label:16s} -> {os.path.basename(target)}")
            written.add(target)
            continue

        absorb, spectra, dark, ref, wl, times = read_spectra_full(path)
        echem = read_echem(folder, data_type, run_number)
        seg = type("Seg", (), {"label": label, "num_points": len(times),
                               "delta_time": None, "trigger": None})()
        out = write_segment_h5(absorb, spectra, dark, ref, wl, times, echem,
                               data_type, run_number, os.path.dirname(folder), name,
                               segment=seg, compression=compression)
        if out is None:
            return 0
        written.add(target)
        print(f"   {label:16s} {absorb.shape[0]}x{absorb.shape[1]}"
              f"{'  + echem' if echem is not None else ''}"
              f" -> {os.path.basename(target)}")

    if not dry_run:
        _mark_backfilled(written)
        h5_bytes = sum(os.path.getsize(p) for p in written)
        print(f"   {ascii_bytes/1e6:8.1f} MB ascii -> {h5_bytes/1e6:.1f} MB h5"
              f"   ({ascii_bytes/max(h5_bytes,1):.1f}x)")
    return len(segments)


def _mark_backfilled(paths):
    """Say in the file that it came from ascii, and that one axis is missing.

    Better than silently omitting time_spectrometer: a reader that finds no such
    dataset should be able to tell "this run never had it" from "this writer forgot".
    """
    import h5py
    for p in paths:
        with h5py.File(p, "a") as f:
            f.attrs["backfilled_from_ascii"] = True
            f.attrs["time_spectrometer_recovered"] = False
            f.attrs["backfill_note"] = (
                "Rebuilt from the 8-column ascii. The spectrometer's own clock was "
                "not recoverable: write_spectra_file computes 'Time (s)' and "
                "'Corrected time (s)' identically, so only the rebased axis survived. "
                "time_spectrometer is therefore ABSENT from these files rather than "
                "holding a copy of `time`."
            )
            # REMOVE it rather than leave a copy of `time` under that name. The
            # writer is handed the rebased axis (it is all the ascii kept), so the
            # dataset would otherwise exist and be wrong -- and an attribute nobody
            # reads does not fix a lie in the data. Absent is checkable; wrong is not.
            for key in list(f):
                node = f[key]
                if isinstance(node, h5py.Group) and "time_spectrometer" in node:
                    del node["time_spectrometer"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="run folder(s) holding the ascii")
    ap.add_argument("--gzip", type=int, default=0, metavar="N",
                    help="gzip level 1-9 (default 0 = off). MEASURED: buys ~21%% "
                         "for ~120 ms a segment, so it is for archiving")
    ap.add_argument("--dry-run", action="store_true", help="say what would be written")
    ap.add_argument("--keep", action="store_true",
                    help="append to existing .h5 instead of rebuilding them")
    args = ap.parse_args()

    if not H5PY_AVAILABLE:
        print(f"h5py is not importable here: {H5PY_IMPORT_ERROR}")
        print("Install it with `pip install h5py` (SpecEchem32 must pin 2.10.0).")
        return 1

    total = 0
    for folder in args.folders:
        try:
            total += convert_run(folder, args.gzip, args.dry_run, args.keep)
        except Exception as exc:                       # noqa: BLE001
            print(f"{folder}: FAILED — {exc}")
    print(f"\n{total} segment(s) converted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
