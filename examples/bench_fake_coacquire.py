"""
Locate WHY the Gamry curve stops almost immediately in our capture seam.

Earlier finding: driving one CV segment through the real run_one_segment path,
curve.running() was True on only the FIRST pump of 41 — the curve dies in ~0.1s
though the waveform should take ~4s. Both main and worker thread behaved the
same, so it is NOT a threading issue.

Two modes to pin it down (run on SpecEchem32, Gamry connected):

    python examples/bench_fake_coacquire.py seam
        Real seam (run_one_segment + ToolkitPotentiostat + FakeSpectrometer),
        with a per-pump timeline: elapsed-since-run, running(), acq_data points.
        Shows exactly WHEN the curve stops and whether data ever accumulates.

    python examples/bench_fake_coacquire.py bench
        The known-good bench_gamry_cv pattern (build curve, set signal, init,
        set_cell, run, tight `while running(): sleep(0.1)` loop) but using OUR
        CV params (scan 0.1 V/s, the ones the seam uses). If THIS runs ~4s and
        gets data, the params are fine and the bug is our prepare/fire SPLIT
        (init_signal in prepare, run in fire, with arming + DIGOUT between). If
        this ALSO dies instantly, the CV params/signal are the problem.
"""
import sys
import time
import tempfile
from pathlib import Path

import numpy as np

from spec_echem.fakes import FakeSpectrometer
from spec_echem.experiment import Segment, run_one_segment
from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.data import DATA_TYPE_CV

try:
    import toolkitpy as tkp
    from spec_echem.potentiostat import (
        ToolkitPotentiostat, TOOLKITPY_AVAILABLE, initialize_pstat, MAX_CURVE_SIZE,
        _FIRE_ARM_MARGIN_S,
    )
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"could not import toolkitpy path: {exc}")

if not TOOLKITPY_AVAILABLE:
    raise SystemExit("toolkitpy not importable — run this on SpecEchem32.")

# The exact CV params the GUI/seam uses (100 mV/s, 10 mV, 0/-0.1/0.1/0, 1 cycle)
CV_VERTICES = [0.0, -0.1, 0.1, 0.0]
SCAN_RATE_VPS = 0.1
STEP_V = 0.01
CYCLES = 1
N_POINTS = 41
DELTA_S = 0.1


class TimelinePstat(ToolkitPotentiostat):
    """Records (elapsed, running, n_points) at each pump, timed from run().

    fire_mode controls where DIGOUT0 is raised relative to curve.run():
        "before" — set_cell, DIGOUT high, run   (the current potentiostat.fire)
        "none"   — set_cell, run                 (no DIGOUT — isolates the split)
        "after"  — set_cell, run, DIGOUT high    (candidate fix)
    """
    def __init__(self, settings, fire_mode="before"):
        super().__init__(settings)
        self.fire_mode = fire_mode
        self.timeline = []
        self._t_run = None

    def fire(self):
        if self.fire_mode == "reinit":
            # prepare() already built+set+init'd the signal; re-init it here so
            # init_signal() is contiguous with run() (like bench).
            self._pstat.init_signal()
            self._set_trigger_line(high=True)
            self._pstat.set_cell(True)
            self._curve.run(True)
        elif self.fire_mode == "allinfire":
            # Rebuild the whole signal+curve here so build→init→run is one
            # contiguous sequence, exactly like the working bench pattern.
            self._curve = self._build_and_arm_signal(self._segment)
            self._set_trigger_line(high=True)
            self._pstat.set_cell(True)
            time.sleep(0.010)
            self._curve.run(True)
        else:
            time.sleep(_FIRE_ARM_MARGIN_S)
            self._pstat.set_cell(True)
            if self.fire_mode == "before":
                self._set_trigger_line(high=True)
                self._curve.run(True)
            elif self.fire_mode == "after":
                self._curve.run(True)
                self._set_trigger_line(high=True)
            else:  # "none"
                self._curve.run(True)
        self._t_run = time.perf_counter()

    def pump(self):
        elapsed = None if self._t_run is None else time.perf_counter() - self._t_run
        running = None
        n = None
        if self._curve is not None:
            try:
                running = self._curve.running()
                n = len(self._curve.acq_data())
            except Exception as exc:  # noqa: BLE001
                running = f"ERR:{exc}"
        self.timeline.append((elapsed, running, n))


def run_seam(fire_mode):
    settings = DEFAULT_SETTINGS.copy()
    settings.update(dict(
        cv_initial_v=CV_VERTICES[0], cv_limit1_v=CV_VERTICES[1],
        cv_limit2_v=CV_VERTICES[2], cv_final_v=CV_VERTICES[3],
        cv_step_size=STEP_V * 1000, cv_scan_rate=SCAN_RATE_VPS * 1000, cv_cycles=CYCLES,
        save_dta=False, data_root=tempfile.mkdtemp(), data_folder="fake_seam",
    ))
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()
    seg = Segment("CV", DATA_TYPE_CV, 0, num_points=N_POINTS, delta_time=DELTA_S, trigger=False)

    pstat = TimelinePstat(settings, fire_mode=fire_mode)
    pstat.open()
    try:
        run_one_segment(spec, seg, dark, ref, wl,
                        settings["data_root"], settings["data_folder"], potentiostat=pstat)
    finally:
        pstat.close()

    print("  pump#   elapsed(s)  running   acq_points")
    for i, (el, run, n) in enumerate(pstat.timeline):
        els = "  n/a " if el is None else f"{el:7.3f}"
        print(f"  {i:5d}   {els}    {str(run):7s}  {n}")
    data = pstat.last_data()
    print(f"[seam] final acq_data points: {0 if data is None else len(data)}")


def run_bench():
    """Direct bench_gamry_cv pattern with OUR CV params — no seam, no split."""
    tkp.toolkitpy_init("bench_fake_coacquire")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)

    sample_time = STEP_V / SCAN_RATE_VPS
    curve = tkp.RcvCurve(pstat, MAX_CURVE_SIZE)
    signal = pstat.signal_r_up_dn_new(
        CV_VERTICES, [SCAN_RATE_VPS] * 3, [0.0, 0.0, 0.0], sample_time, CYCLES, tkp.PSTATMODE)
    pstat.set_signal_r_up_dn(signal)
    pstat.init_signal()

    pstat.set_cell(True)
    time.sleep(0.010)
    t0 = time.perf_counter()
    curve.run(True)
    print(f"  running() right after run(True): {curve.running()}  (expect True, ~4s waveform)")
    polls = trues = 0
    while tkp.pstat_is_valid(pstat) and curve.running():
        polls += 1
        trues += 1
        time.sleep(0.1)
    elapsed = time.perf_counter() - t0
    if tkp.pstat_is_valid(pstat):
        pstat.set_cell(False)
    data = curve.acq_data()
    n = len(data)
    print(f"[bench] run window: {elapsed:.2f}s | polls with running()==True: {trues}")
    print(f"[bench] acq_data points: {n}  (sample_time={sample_time:.3f}s)")
    tkp.toolkitpy_close()


_SEAM_MODES = {"seam": "before", "nodig": "none", "digafter": "after",
               "reinit": "reinit", "allinfire": "allinfire"}


def main():
    mode = (sys.argv[1].lower() if len(sys.argv) > 1 else "seam")
    print(f"== mode: {mode} ==")
    if mode == "bench":
        run_bench()
    elif mode in _SEAM_MODES:
        run_seam(_SEAM_MODES[mode])
    else:
        raise SystemExit(f"mode must be 'bench' or one of {list(_SEAM_MODES)}")


if __name__ == "__main__":
    main()
