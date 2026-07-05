"""
Reproduce the GUI's echem-capture path with a FAKE spectrometer — no Qt, no real
Avantes, just toolkitpy + the REAL spec_echem seam. Isolates whether the empty
echem-data bug is in our acquire/pump/finish logic or is GUI/thread-specific.

Drives ONE CV segment through the exact production path
(run_one_segment -> acquire_segment -> ToolkitPotentiostat.prepare/fire/pump/finish
-> write_echem_file) and reports:
  - pump calls and how many saw curve.running() == True   (is the pump working?)
  - acq_data() point count right after finish()            (did data accumulate?)
  - CV.txt line count                                      (did the file get rows?)

Run ONE mode per invocation on SpecEchem32 with the Gamry connected:

    python examples/bench_fake_coacquire.py main     # run on the main thread
    python examples/bench_fake_coacquire.py thread    # run on a worker thread (like the GUI)

Compare the two: if 'main' gets data but 'thread' doesn't, the bug is that the
Gamry data pump isn't serviced on a secondary (worker) thread — and the fix is
about WHERE the Gamry runs, not the pump call.
"""
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

from spec_echem.fakes import FakeSpectrometer
from spec_echem.experiment import Segment, run_one_segment
from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.data import DATA_TYPE_CV

try:
    from spec_echem.potentiostat import ToolkitPotentiostat, TOOLKITPY_AVAILABLE
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"could not import ToolkitPotentiostat: {exc}")

if not TOOLKITPY_AVAILABLE:
    raise SystemExit("toolkitpy not importable — run this on SpecEchem32.")


class InstrumentedPstat(ToolkitPotentiostat):
    """Same behaviour, but counts pump calls and how often the curve is running."""
    def __init__(self, settings):
        super().__init__(settings)
        self.pump_calls = 0
        self.running_true = 0

    def pump(self):
        self.pump_calls += 1
        super().pump()  # the real pump: curve.running()
        try:
            if self._curve is not None and self._curve.running():
                self.running_true += 1
        except Exception:  # noqa: BLE001
            pass


def do_run(tag):
    settings = DEFAULT_SETTINGS.copy()
    settings.update(dict(
        cv_initial_v=0.0, cv_limit1_v=-0.1, cv_limit2_v=0.1, cv_final_v=0.0,
        cv_step_size=10.0, cv_scan_rate=100.0, cv_cycles=1,
        save_dta=False,                       # focus on the acq_data/txt path first
        data_root=tempfile.mkdtemp(),
        data_folder=f"fake_{tag}",
    ))

    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()

    # CV: path 0.4 V / 10 mV * 1000 + 1 = 41 points, delta = 10/100 = 0.1 s
    seg = Segment("CV", DATA_TYPE_CV, 0, num_points=41, delta_time=0.1, trigger=False)

    pstat = InstrumentedPstat(settings)
    pstat.open()
    try:
        run_one_segment(spec, seg, dark, ref, wl,
                        settings["data_root"], settings["data_folder"],
                        potentiostat=pstat)
    finally:
        pstat.close()

    data = pstat.last_data()
    n = 0 if data is None else len(data)
    folder = Path(settings["data_root"]) / settings["data_folder"]
    cvtxt = folder / "CV.txt"
    lines = cvtxt.read_text().strip().splitlines() if cvtxt.exists() else []

    print(f"[{tag}] pump calls: {pstat.pump_calls}  |  running()==True: {pstat.running_true}")
    print(f"[{tag}] acq_data points after finish: {n}")
    print(f"[{tag}] CV.txt lines: {len(lines)}  (1 = header only)  @ {cvtxt}")
    if n:
        names = data.dtype.names or ()
        print(f"[{tag}] fields: {names}")


def main():
    mode = (sys.argv[1].lower() if len(sys.argv) > 1 else "main")
    if mode not in ("main", "thread"):
        raise SystemExit("mode must be 'main' or 'thread'")
    print(f"== running on the {mode} ==")
    if mode == "main":
        do_run("main")
    else:
        t = threading.Thread(target=do_run, args=("thread",))
        t.start()
        t.join()


if __name__ == "__main__":
    main()
