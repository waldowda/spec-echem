"""
bench_autolab_driver.py — the ACTUAL AutolabPotentiostat class, on the instrument.

Every other bench script exercised the SDK directly. This one drives
`spec_echem.potentiostat.AutolabPotentiostat` exactly the way the run pipeline
does — open / prepare / fire / pump* / finish / last_data — so the first time the
real driver class touches the Autolab is here, on a dummy resistor, not on a
sample.

No spectrometer: fire() still pulses P1.A (nothing catches it, which is fine).
The point is the echem lifecycle and the trace that comes back.

    >> 10 kOhm dummy resistor, never a real sample. <<

Usage:
    python bench_autolab_driver.py            # CV segment
    python bench_autolab_driver.py doping     # a doping hold (3-step CA template)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spec_echem import potentiostat                              # noqa: E402
from spec_echem.data import (                                    # noqa: E402
    DATA_TYPE_CV, DATA_TYPE_DOPING,
)
from spec_echem.experiment import Segment                        # noqa: E402
from spec_echem.settings import DEFAULT_SETTINGS                  # noqa: E402

SDK_BASE = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1"
DEFAULTS = {
    "autolab_sdk": rf"{SDK_BASE}\EcoChemie.Autolab.Sdk",
    "autolab_adx": rf"{SDK_BASE}\Hardware Setup Files\Adk.x",
    "autolab_hdw": rf"{SDK_BASE}\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml",
    "autolab_nox_cv": rf"{SDK_BASE}\Standard Nova Procedures\Cyclic voltammetry.nox",
    "autolab_nox_ca": rf"{SDK_BASE}\Standard Nova Procedures\Chrono amperometry.nox",
    "autolab_dio_port": 0,
    "autolab_pulse_delay_s": 5.95,      # measured; align the pulse to the staircase
    # CV vertices — the driver reads these from settings
    "cv_initial_v": 0.0, "cv_limit1_v": 1.0, "cv_limit2_v": -1.0, "cv_final_v": 0.0,
    "cv_step_size": 2.44, "cv_scan_rate": 500.0, "cv_cycles": 1,   # mV, mV/s
    # chrono
    "chrono_time": 8.0, "prededoping_time": 5.0,
    "doping_potential_start": 0.30, "doping_potential_step": 0.05,
    "dedoping_potential": -0.30, "prededoping_potential": -0.20,
}


def main():
    kind = sys.argv[1].lower() if len(sys.argv) > 1 else "cv"
    settings = dict(DEFAULT_SETTINGS)          # code defaults for every key the driver reads
    settings.update(DEFAULTS)                  # ...then the Autolab paths + CV/chrono values

    if kind.startswith("dop"):
        seg = Segment("Doping 0", DATA_TYPE_DOPING, 0, num_points=80,
                      delta_time=0.1, trigger=True)
    else:
        seg = Segment("CV", DATA_TYPE_CV, 0, num_points=1640,
                      delta_time=0.01, trigger=True)

    print("=" * 70)
    print(f"AutolabPotentiostat driver smoke test — {seg.label}")
    print("=" * 70)
    if not potentiostat.AUTOLAB_AVAILABLE:
        print("pythonnet (clr) not importable — cannot run.")
        return 1

    p = potentiostat.AutolabPotentiostat(settings)
    print("open() ...")
    p.open()
    try:
        print(f"prepare({seg.label}) ...")
        p.prepare(seg)
        print(f"  procedure loaded, pulse delay = {p._pulse_delay:.2f} s")

        print("fire() — cell ON, Measure(), pulse P1.A ...")
        t0 = time.time()
        p.fire()

        print("pump() loop until the run finishes ...")
        polls = 0
        while True:
            try:
                measuring = bool(p._proc.IsMeasuring)
            except Exception:  # noqa: BLE001
                measuring = False
            if not measuring:
                break
            p.pump()
            polls += 1
            time.sleep(0.1)
            if time.time() - t0 > 180:
                print("  TIMEOUT — stopping.")
                p.stop()
                break
        print(f"  {polls} pump() calls over {time.time() - t0:.1f} s")

        print("finish() ...")
        p.finish()

        data = p.last_data()
        if data is None:
            print("  last_data() is None — NOTHING CAME BACK. Check the log above.")
            return 1
        n = len(data.current)
        import numpy as np
        peak = float(np.nanmax(np.abs(data.current))) if n else 0.0
        print(f"  EchemData: {n} points")
        print(f"    time    {data.time[0]:.3f} .. {data.time[-1]:.3f} s  (rebased to 0)")
        print(f"    potential {data.potential.min():+.3f} .. {data.potential.max():+.3f} V")
        print(f"    current  max|I| = {peak:.3e} A "
              f"(expect ~1e-4 for 1 V / 10 kOhm)")
        live = p.live_data()
        print(f"  live_data(): {0 if live is None else len(live.current)} scalar samples")
        print(f"  device_lost(): {p.device_lost()}")

        if seg.data_type == DATA_TYPE_CV:
            expect_i = settings["cv_limit1_v"] / 10_000.0      # 10 kOhm dummy
            want_pts = 200
        else:
            expect_i = abs(settings["doping_potential_start"]) / 10_000.0
            want_pts = int(0.5 * settings["chrono_time"] / seg.delta_time)
        i_ok = 0.5 * expect_i < peak < 2.0 * expect_i
        print(f"    (expected max|I| ~ {expect_i:.1e} A for this potential / 10 kOhm)")
        ok = n >= want_pts and i_ok
        print("\n" + ("PASS — the driver ran a segment and got a trace that matches "
                      "Ohm's law on the dummy."
                      if ok else "SUSPECT — point count or current is off; see the log."))
        return 0 if ok else 1
    finally:
        print("close() ...")
        p.close()


if __name__ == "__main__":
    sys.exit(main())
