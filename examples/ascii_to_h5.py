"""
Build HDF5 files from a run folder that already exists as ascii.

Every run recorded before 2026-09-29 is ascii-only; this is how they stop being
stranded when the HDF5 becomes the primary format. The same conversion is available
in the GUI as the Results tab's "Convert to HDF5" button — the logic lives in
`spec_echem.h5_backfill` so both callers share it.

    python examples/ascii_to_h5.py ~/specechem_data/20260925_test10
    python examples/ascii_to_h5.py ~/specechem_data/*/ --gzip 4

Read-only with respect to the ascii: it never modifies or deletes a .txt. Existing
.h5 files are rebuilt unless --keep is given.

**`time_spectrometer` cannot be recovered** — write_spectra_file threw the device
clock away by writing both time columns identically — so backfilled files omit that
dataset and say so in an attribute rather than storing a wrong one.
"""
import argparse
import logging
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from spec_echem.data import H5PY_AVAILABLE, H5PY_IMPORT_ERROR      # noqa: E402
from spec_echem.h5_backfill import backfill_run                    # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="run folder(s) holding the ascii")
    ap.add_argument("--gzip", type=int, default=0, metavar="N",
                    help="gzip level 1-9 (default 0 = off). MEASURED: buys ~21%% "
                         "for ~120 ms a segment, so it is for archiving")
    ap.add_argument("--keep", action="store_true",
                    help="append to existing .h5 instead of rebuilding them")
    args = ap.parse_args()

    if not H5PY_AVAILABLE:
        print(f"h5py is not importable here: {H5PY_IMPORT_ERROR}")
        print("Install it with `pip install h5py` (SpecEchem32 must pin 2.10.0).")
        return 1

    # backfill_run reports progress through the run logger; show it here.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    total = 0
    for folder in args.folders:
        try:
            result = backfill_run(folder, compression=args.gzip, keep=args.keep)
        except Exception as exc:                       # noqa: BLE001
            print(f"{folder}: FAILED — {exc}")
            continue
        if not result["segments"]:
            print(f"{os.path.basename(str(folder).rstrip(os.sep))}: nothing to convert")
            continue
        total += result["segments"]
        print(f"{result['name']}: {result['segments']} segment(s), "
              f"{result['ascii_bytes']/1e6:.1f} MB ascii -> "
              f"{result['h5_bytes']/1e6:.1f} MB h5 "
              f"({result['ascii_bytes']/max(result['h5_bytes'], 1):.1f}x)")
    print(f"\n{total} segment(s) converted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
