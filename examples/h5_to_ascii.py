"""
Regenerate the 8-column ascii from a run's HDF5 — the inverse of ascii_to_h5.py.

This is the round trip that actually matters. `read_segment_h5` proves the
ABSORBANCE survives; this proves the whole FILE does, by rebuilding it with the same
writer acquisition uses and diffing against the original. Nothing may be
reconstructed by hand: it reads counts, dark, reference, absorbance and the time
axis out of the H5 and calls `write_spectra_file` and `write_echem_file`.

What it is for:

- **Evidence.** Before the ascii can ever stop being written, something has to show
  that nothing in it is lost. Run this against a real run and diff.
- **A way back.** If the H5 became primary and a downstream tool still needed text,
  this regenerates it.

**Exact except for absorbance**, which the schema stores as float32 — about 7
significant figures, against a measurement good to ~1e-3. Everything else (counts,
dark, reference, wavelengths, times) round-trips bit for bit.

    python examples/h5_to_ascii.py <run_folder> --out /tmp/rebuilt
    python examples/h5_to_ascii.py <run_folder> --out /tmp/rebuilt --compare
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
    DATA_TYPE_CV, EchemData, H5PY_AVAILABLE, H5PY_IMPORT_ERROR, discover_run_h5,
    echem_txt_path, h5py, write_echem_file, write_spectra_file,
)


def rebuild_segment(h5_file, cycle, data_type, out_root, added_path):
    """Write one segment's spectra .txt (and echem .txt) from the H5. -> Path"""
    with h5py.File(h5_file, "r") as f:
        wl = np.asarray(f["wavelength"], dtype=float)
        g = f[str(cycle)]
        counts = np.asarray(g["counts_vs_time"], dtype=float)      # (n_wl, n_t)
        absorb = np.asarray(g["absorbance_vs_time"], dtype=float)
        dark = np.asarray(g["dark"], dtype=float)
        ref = np.asarray(g["reference"], dtype=float)
        times = np.asarray(g["time"], dtype=float)
        echem = None
        if "echem" in g:
            e = g["echem"]
            echem = EchemData(time=np.asarray(e["time"], dtype=float),
                              potential=np.asarray(e["potential"], dtype=float),
                              current=np.asarray(e["current"], dtype=float))

    absorb_df = pd.DataFrame(absorb, index=wl, columns=times)
    path = write_spectra_file(absorb_df, list(counts.T), dark, ref, wl, list(times),
                              data_type, cycle, out_root, added_path)
    if echem is not None:
        write_echem_file(echem, data_type, cycle, out_root, added_path)
    return path


def compare(original, rebuilt):
    """Column-by-column, with the numbers rather than a verdict."""
    a = pd.read_csv(original, sep="\t")
    b = pd.read_csv(rebuilt, sep="\t")
    if list(a.columns) != list(b.columns):
        return [f"COLUMN NAMES DIFFER\n   {list(a.columns)}\n   {list(b.columns)}"]
    if len(a) != len(b):
        return [f"ROW COUNT DIFFERS: {len(a)} vs {len(b)}"]
    lines = []
    for col in a.columns:
        x, y = pd.to_numeric(a[col], errors="coerce"), pd.to_numeric(b[col], errors="coerce")
        if not np.array_equal(np.isnan(x), np.isnan(y)):
            lines.append(f"   {col:24s} NaN PATTERN DIFFERS")
            continue
        fin = ~np.isnan(x)
        if not fin.any():
            lines.append(f"   {col:24s} all NaN in both")
            continue
        d = np.abs(x[fin] - y[fin])
        lines.append(f"   {col:24s} max |diff| {d.max():.3e}"
                     f"   exact {100 * (d == 0).mean():5.1f}%")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--out", required=True, help="where to write the rebuilt ascii")
    ap.add_argument("--compare", action="store_true",
                    help="diff each rebuilt file against the original")
    args = ap.parse_args()

    if not H5PY_AVAILABLE:
        print(f"h5py is not importable here: {H5PY_IMPORT_ERROR}")
        return 1

    folder = os.path.abspath(os.path.expanduser(args.folder))
    name = os.path.basename(folder.rstrip(os.sep))
    segments = discover_run_h5(folder)
    if not segments:
        print(f"{name}: no .h5 files here — run examples/ascii_to_h5.py first")
        return 1

    out_root = os.path.abspath(os.path.expanduser(args.out))
    print(f"{name}: {len(segments)} segment(s) -> {out_root}/{name}\n")
    for label, data_type, cycle, h5_file in segments:
        rebuilt = rebuild_segment(h5_file, cycle, data_type, out_root, name)
        print(f"  {label:16s} -> {os.path.basename(rebuilt)}")
        if args.compare:
            original = os.path.join(folder, os.path.basename(rebuilt))
            if not os.path.exists(original):
                print("      (no original to compare)")
                continue
            for line in compare(original, rebuilt):
                print(f"   {line}")
            echem_orig = echem_txt_path(folder, data_type, cycle)
            echem_new = echem_txt_path(os.path.join(out_root, name), data_type, cycle)
            if os.path.exists(echem_orig) and os.path.exists(echem_new):
                print(f"      echem {os.path.basename(echem_orig)}:")
                for line in compare(echem_orig, echem_new):
                    print(f"   {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
