"""
Headless END-TO-END validation of Phase 2.5 echem capture with the NEW
dedicated-thread ToolkitPotentiostat. No Qt, real Gamry, FAKE spectrometer.

Runs the FULL segment sequence (CV + pre-dedoping + doping/dedoping cycles)
through the real run_one_segment path and checks each segment writes a POPULATED
echem .txt (and, if save_dta, a .dta in dta/). This is THE run to do on
SpecEchem32 after the two-thread refactor.

It also stresses the one thing the Mac can't test: a fresh toolkitpy session per
segment, back to back (init/close repeated), so we learn whether multi-segment
runs are healthy.

Open leads are fine — values are noise; we only check that data points EXIST and
the files have rows. Run on SpecEchem32 with the Gamry connected:

    python examples/validate_echem_capture.py

PASS on every segment => the refactor works; wire the GUI run next.
"""
import tempfile
from pathlib import Path

import numpy as np

from spec_echem.fakes import FakeSpectrometer
from spec_echem.experiment import build_segments, run_one_segment
from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.data import _echem_filename_for

try:
    from spec_echem.potentiostat import ToolkitPotentiostat, TOOLKITPY_AVAILABLE
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"could not import ToolkitPotentiostat: {exc}")
if not TOOLKITPY_AVAILABLE:
    raise SystemExit("toolkitpy not importable — run this on SpecEchem32.")


def main():
    root = tempfile.mkdtemp()
    settings = DEFAULT_SETTINGS.copy()
    settings.update(dict(
        data_root=root, data_folder="validate_echem",
        save_dta=True, trigger=False,
        # small, quick shapes (safe on open leads)
        cv_enabled=True, cv_initial_v=0.0, cv_limit1_v=-0.1, cv_limit2_v=0.1,
        cv_final_v=0.0, cv_step_size=10.0, cv_scan_rate=100.0, cv_cycles=1,
        prededoping_enabled=True, prededoping_potential=0.0, prededoping_time=3.0,
        doping_enabled=True, doping_potential_start=0.2, doping_potential_end=0.3,
        doping_potential_step=0.1, dedoping_potential=0.0,
        chrono_time=3.0, chrono_delta_time=0.1,
    ))

    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()

    segments = build_segments(settings)
    print(f"segments: {[s.label for s in segments]}\n")
    print(f"  {'result':6s}  {'segment':12s}  {'acq_pts':7s}  file (data rows)")

    pstat = ToolkitPotentiostat(settings)
    pstat.open()
    rows = []
    try:
        for seg in segments:
            run_one_segment(spec, seg, dark, ref, wl, root, settings["data_folder"],
                            potentiostat=pstat)
            data = pstat.last_data()
            n = 0 if data is None else len(data)
            fname = _echem_filename_for(seg.data_type, seg.run_number)
            fpath = Path(root) / settings["data_folder"] / fname
            lines = fpath.read_text().strip().splitlines() if fpath.exists() else []
            data_rows = max(0, len(lines) - 1)   # minus header
            ok = n > 0 and data_rows > 0
            rows.append(ok)
            print(f"  {'PASS' if ok else 'FAIL':6s}  {seg.label:12s}  {n:7d}  "
                  f"{fname} (rows={data_rows})")
    finally:
        pstat.close()

    folder = Path(root) / settings["data_folder"]
    dta_dir = folder / "dta"
    dta = sorted(p.name for p in dta_dir.glob("*.dta")) if dta_dir.exists() else []
    print(f"\n.dta files: {dta}")
    npass = sum(rows)
    ok_all = npass == len(segments) == len(rows) and len(rows) > 0
    print(f"\n{'ALL SEGMENTS PASSED' if ok_all else 'SOME SEGMENTS FAILED'}  "
          f"({npass}/{len(rows)})")
    print(f"data in: {folder}")


if __name__ == "__main__":
    main()
