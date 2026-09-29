"""
What the HDF5 output costs in bytes and in time, on THIS machine's disk.

Two numbers matter and they pull against each other:

- **Bytes.** The whole reason the format exists. A 14-segment run is ~645 MB of
  ascii.
- **Write time.** It is paid per segment, between segments, with the cell off — so
  it lengthens the gap between steps. That gap already holds a 300-1100 ms wait for
  the GUI to finish drawing, so a slow write is not dangerous, merely wasteful.

MEASURED on a Mac 2026-09-29 (20260925_test10's CV, 1261 x 721): ascii 83.6 MB ->
H5 plain 7.29 MB (11.5x) -> gzip4 5.74 MB (14.6x), 7 ms against 126 ms. So gzip
buys 21%, NOT the 2-4x first estimated, and level 9 buys nothing over 4 — which is
why compression ships OFF and is an archiving option. Run this on the rig to
confirm that holds on its disk and its segment sizes.

    python examples/bench_h5_size.py ~/specechem_data/20260925_test10

Reads an existing run folder; writes only into a temporary directory, which it
removes. It never touches the run.
"""
import argparse
import os
import shutil
import sys
import tempfile
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from spec_echem.data import (                                         # noqa: E402
    H5PY_AVAILABLE, H5PY_IMPORT_ERROR, discover_run_segments, h5_path,
    write_segment_h5,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ascii_to_h5 import read_spectra_full, read_echem                 # noqa: E402


def bench(folder, levels=(0, 4, 9)):
    folder = os.path.abspath(os.path.expanduser(folder))
    name = os.path.basename(folder.rstrip(os.sep))
    segments = discover_run_segments(folder)
    if not segments:
        print(f"{name}: no spec-echem spectra files here")
        return

    print(f"\n{name}: {len(segments)} segment(s)\n")
    print(f"{'segment':18s} {'ascii':>10s} " +
          "".join(f"{'gzip ' + str(g) if g else 'plain':>12s}" for g in levels))
    print("-" * (29 + 12 * len(levels)))

    totals = {"ascii": 0}
    times = {g: 0.0 for g in levels}
    for label, data_type, run_number, path in segments:
        ascii_bytes = os.path.getsize(path)
        totals["ascii"] += ascii_bytes
        row = f"{label:18s} {ascii_bytes/1e6:9.1f}M "

        absorb, spectra, dark, ref, wl, stamps = read_spectra_full(path)
        echem = read_echem(folder, data_type, run_number)
        for gz in levels:
            tmp = tempfile.mkdtemp()
            try:
                t0 = time.perf_counter()
                write_segment_h5(absorb, spectra, dark, ref, wl, stamps, echem,
                                 data_type, run_number, tmp, name, compression=gz)
                dt = time.perf_counter() - t0
                size = os.path.getsize(h5_path(os.path.join(tmp, name), data_type, name))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            times[gz] += dt
            totals.setdefault(gz, 0)
            totals[gz] += size
            row += f"{size/1e6:8.2f}M{dt*1000:4.0f}ms"
        print(row)

    print("-" * (29 + 12 * len(levels)))
    row = f"{'TOTAL':18s} {totals['ascii']/1e6:9.1f}M "
    for gz in levels:
        row += f"{totals[gz]/1e6:8.2f}M{times[gz]*1000:4.0f}ms"
    print(row)
    print()
    for gz in levels:
        tag = f"gzip {gz}" if gz else "plain"
        saving = 100 * (1 - totals[gz] / totals[levels[0]]) if levels[0] == 0 else 0
        print(f"  {tag:8s} {totals['ascii']/totals[gz]:5.1f}x smaller than the ascii"
              + (f", {saving:4.1f}% below plain" if gz else "")
              + f"   ({times[gz]/len(segments)*1000:.0f} ms a segment)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="an existing run folder holding the ascii")
    ap.add_argument("--levels", default="0,4,9",
                    help="gzip levels to compare (default 0,4,9)")
    args = ap.parse_args()
    if not H5PY_AVAILABLE:
        print(f"h5py is not importable here: {H5PY_IMPORT_ERROR}")
        return 1
    bench(args.folder, tuple(int(x) for x in args.levels.split(",")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
