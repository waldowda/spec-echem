"""
Export runs into the layout `rajgiriUW/OECT_processing` reads.

The same thing the Results tab's "Export for OECT analysis" button does, from the
command line, so a whole folder of runs can be exported in one go.

**A DERIVED VIEW, not the record.** The target layout has nowhere to put raw counts,
the dark, the reference, the CV, pre-dedoping or any provenance — the complete data
stays in each run's own .h5. Because this is a deliverable rather than a run's own
data, `--out` is the normal case here, not an edge one.

    python examples/export_oect.py ~/specechem_data/20250710_*
    python examples/export_oect.py ~/specechem_data/*/ --out ~/share/oect

Needs the run's own .h5 files to exist: acquisition writes them, and
examples/ascii_to_h5.py rebuilds them for older runs.
"""
import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from spec_echem.data import H5PY_AVAILABLE, H5PY_IMPORT_ERROR       # noqa: E402
from spec_echem.oect_export import export_run                       # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="run folder(s) holding the .h5 files")
    ap.add_argument("--out", metavar="DIR", default=None,
                    help="write the exports to DIR instead of <run>/oect/")
    args = ap.parse_args()

    if not H5PY_AVAILABLE:
        print(f"h5py is not importable here: {H5PY_IMPORT_ERROR}")
        return 1

    total = 0
    for folder in args.folders:
        name = os.path.basename(str(folder).rstrip(os.sep))
        try:
            written = export_run(folder, out_dir=args.out)
        except Exception as exc:                       # noqa: BLE001
            print(f"{name}: FAILED — {exc}")
            continue
        if not written:
            print(f"{name}: no doping or dedoping .h5 — run ascii_to_h5.py first")
            continue
        total += len(written)
        print(f"{name} -> {written[0].parent}")
        for p in written:
            print(f"   {p.name}  {p.stat().st_size/1e6:.1f} MB")
    print(f"\n{total} file(s) written. A DERIVED VIEW — the complete data stays in "
          f"each run's own .h5.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
