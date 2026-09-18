"""
bench_autolab_fullrun.py — the whole spec-echem pipeline, Autolab + Avantes, no GUI.

Does exactly what gui/workers.py::AcquisitionWorker.run does — build_segments ->
for each segment run_one_segment(spec, seg, dark, ref, wavelengths, ...,
potentiostat) -> files on disk — but headless, so a full co-acquisition run can be
checked against docs/data-format.md without clicking through the GUI.

    >> 10 kOhm dummy resistor, never a real sample. <<
    The absorbance numbers are meaningless (no real optical path); the point is
    that the pipeline runs end to end and the files have the right shape.

Reads config/bench.ini for the [autolab] section + data_root, then shortens the
experiment (1 CV cycle, 5 s holds, 1 doping/dedoping cycle) so it finishes in a
few minutes.

Usage:
    python bench_autolab_fullrun.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spec_echem.settings import DEFAULT_SETTINGS               # noqa: E402
from spec_echem.bench import load_bench_defaults, apply_bench_defaults  # noqa: E402
from spec_echem.experiment import build_segments, run_one_segment       # noqa: E402
from spec_echem.potentiostat import make_potentiostat                   # noqa: E402
from spec_echem.spectrometer import AvantesSpectrometer                 # noqa: E402
from spec_echem.logging_config import get_run_logger                    # noqa: E402


def short_experiment():
    s = dict(DEFAULT_SETTINGS)
    vals, warns = load_bench_defaults()
    for w in warns:
        print("  bench.ini warning:", w)
    apply_bench_defaults(s, vals)

    # Keep it quick, and make sure every data type appears.
    s.update({
        "trigger": True,
        "cv_enabled": True, "cv_cycles": 1,
        "cv_initial_v": 0.0, "cv_limit1_v": 0.6, "cv_limit2_v": -0.6, "cv_final_v": 0.0,
        "cv_step_size": 5.0, "cv_scan_rate": 500.0,        # mV, mV/s -> ~5 s sweep
        "prededoping_enabled": True, "prededoping_discard": False,
        "prededoping_potential": -0.20, "prededoping_time": 5.0,
        "doping_enabled": True,
        "doping_potential_start": 0.30, "doping_potential_step": 0.05,
        "dedoping_potential": -0.30,
        "chrono_time": 5.0, "chrono_delta_time": 0.25,
        # one doping/dedoping cycle. The key is doping_potential_END — n_doping_cycles
        # reads that; "doping_potential_final" is not a setting and was silently
        # ignored, leaving the 0.8 V default and running ELEVEN cycles (~5 min).
        "doping_potential_end": 0.30,
        # spectrometer: this box's ULS2048L floor is ~1.05 ms; keep averages low
        "integration_time_ms": float(s.get("integration_time_ms", 1.5)),
        "scan_averages": 1,
    })
    return s


def main():
    s = short_experiment()
    data_root = s["data_root"]
    added_path = "autolab_fullrun_smoke"
    out = os.path.join(data_root, added_path)
    os.makedirs(out, exist_ok=True)

    print("=" * 70)
    print("spec-echem FULL RUN — Autolab + Avantes, headless")
    print("=" * 70)
    print(f"  data_root       : {data_root}")
    print(f"  output folder   : {out}")
    print(f"  potentiostat    : {s['potentiostat_mode']}")
    print(f"  integration     : {s['integration_time_ms']} ms x {s['scan_averages']}")

    segments = build_segments(s)
    print(f"  segments        : {[seg.label for seg in segments]}")

    print("\n  opening the spectrometer ...")
    spec = AvantesSpectrometer()
    spec.init()
    spec.set_integration_time(s["integration_time_ms"])
    spec.set_scan_averages(int(s["scan_averages"]))
    _, wavelengths = spec.wavelengths()
    print(f"    {len(wavelengths)} pixels, {wavelengths[0]:.1f}..{wavelengths[-1]:.1f} nm")

    print("  taking dark + reference (free-run; content not meaningful on a resistor) ...")
    spec.set_trigger_mode(0)
    _, dark = spec.measure()
    _, ref = spec.measure()

    pstat = make_potentiostat(s)
    print(f"  potentiostat    : {type(pstat).__name__}")

    abort_event = threading.Event()
    logger = get_run_logger()

    pstat.open()
    try:
        for i, seg in enumerate(segments, 1):
            print(f"\n  [{i}/{len(segments)}] {seg.label}  "
                  f"({seg.num_points} pts @ {seg.delta_time:.3g}s, trigger={seg.trigger})")
            result = run_one_segment(spec, seg, dark, ref, wavelengths,
                                     data_root, added_path, abort_event, pstat)
            if result is None:
                print("    -> aborted / no data")
                continue
            df, path = result
            print(f"    -> {'discarded (no file)' if path is None else path.name}"
                  f"   absorbance df {df.shape}")
            if pstat.device_lost():
                print("    !! potentiostat lost — stopping")
                break
    finally:
        pstat.close()
        spec.close()

    print("\n" + "=" * 70)
    print("FILES WRITTEN:")
    for f in sorted(os.listdir(out)):
        p = os.path.join(out, f)
        print(f"  {f:<34} {os.path.getsize(p):>8} B")
    print("\nCheck a spectra .txt has 8 columns and the echem .txt matches "
          "docs/data-format.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
