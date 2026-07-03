"""
Bench: ONE integrated co-acquisition segment — Gamry + Avantes together, through
the REAL production seam (run_one_segment -> acquire_segment + ToolkitPotentiostat).

This validates the trigger handshake fix on hardware: Python arms the spectrometer
(AVS_Measure), then fires DIGOUT0 + runs a chrono hold, and collects spectra. If the
handshake works, spectrum 0 triggers and ~NUM_POINTS spectra come back; if it were
still broken, the timeout below trips and it reports MISSED instead of hanging.

Run in the 32-bit SpecEchem32 env with both instruments connected. Open leads are
fine — the trigger and spectra don't need a real cell (current will just be ~0).
A small, safe hold potential is applied; edit below.
"""
import tempfile
import threading
import time

try:
    import toolkitpy  # noqa: F401 — presence check
except ImportError:
    raise SystemExit("toolkitpy not importable — run this in the 32-bit SpecEchem32 env.")

from spec_echem.spectrometer import AvantesSpectrometer
from spec_echem.experiment import Segment, run_one_segment
from spec_echem.data import DATA_TYPE_DOPING
from spec_echem.potentiostat import ToolkitPotentiostat

# --- small, safe segment ------------------------------------------------------
USE_TRIGGER = True    # set False first for a no-trigger smoke test (do the two
                      # instruments co-run + produce data?), then True to test the
                      # DIGOUT0 trigger handshake. With False the spectrometer free-
                      # runs instead of waiting for the edge.
POTENTIAL_V = 0.1     # hold potential (safe with open leads / dummy cell)
CHRONO_TIME = 2.0     # s
DELTA_S = 0.1         # s between spectra
NUM_POINTS = int(CHRONO_TIME / DELTA_S) + 1
INTEGRATION_MS = 1.0
AVERAGES = 1
# -----------------------------------------------------------------------------


def main():
    spec = AvantesSpectrometer()
    spec.init()
    spec.set_integration_time(INTEGRATION_MS)
    spec.set_scan_averages(AVERAGES)
    _, wl = spec.wavelengths()

    print("Collecting quick dark/ref (untriggered)...")
    _, dark = spec.measure()
    _, ref = spec.measure()

    settings = {
        "chrono_time": CHRONO_TIME,
        "doping_potential_start": POTENTIAL_V,
        "doping_potential_step": 0.0,
    }
    pstat = ToolkitPotentiostat(settings)
    pstat.open()

    seg = Segment("Doping 0 (bench)", DATA_TYPE_DOPING, 0, NUM_POINTS, DELTA_S, trigger=USE_TRIGGER)
    out = tempfile.mkdtemp()

    # Safety timeout: if spectrum 0 never triggers, don't hang — abort and report.
    abort = threading.Event()
    timer = threading.Timer(CHRONO_TIME + 10.0, abort.set)
    timer.start()

    print(f"Running one chrono ({'TRIGGERED' if USE_TRIGGER else 'free-run, no trigger'}): "
          f"{POTENTIAL_V} V, {CHRONO_TIME}s, expecting ~{NUM_POINTS} spectra...")
    t0 = time.perf_counter()
    try:
        result = run_one_segment(spec, seg, dark, ref, wl, out, "coacquire", abort, pstat)
    finally:
        timer.cancel()
        pstat.close()
        spec.close()
    dt = time.perf_counter() - t0

    print()
    if result is None:
        print(f"MISSED: no data after {dt:.1f}s — spectrum 0 likely never triggered "
              "(handshake/trigger issue).")
    else:
        absorb_df, path = result
        n = absorb_df.shape[1]   # columns = time points (rows = 1265 wavelengths)
        ok = abs(n - NUM_POINTS) <= 1
        print(f"{'OK' if ok else 'CHECK'}: collected {n} spectra in {dt:.2f}s "
              f"(expected ~{NUM_POINTS}, ~{CHRONO_TIME}s).")
        print(f"      wrote {path}")
    print("done.")


if __name__ == "__main__":
    main()
