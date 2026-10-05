"""The PITT loop and its per-step HDF5, end to end on fakes with a simulated clock.

The clock is simulated so a staircase of many minutes runs in milliseconds, and so
every timing assertion is exact rather than at the mercy of a busy test machine.
"""
import numpy as np
import pytest

from spec_echem.acquisition import acquire_pitt
from spec_echem.data import (DATA_TYPE_PITT, discover_run_h5, h5_path,
                             read_segment_h5, write_pitt_h5)
from spec_echem.fakes import FakePittPotentiostat, FakeSpectrometer
from spec_echem.pitt import (END_ABORTED, END_CUTOFF, END_FIXED, END_MAX_HOLD,
                             END_STOPPED, ROLE_DEDOPE, ROLE_RETURN, pitt_plan)
from spec_echem.settings import DEFAULT_SETTINGS

h5py = pytest.importorskip("h5py")

SAMPLE_COST = 0.050      # an Ei pump, measured on the Autolab
EXPOSURE_COST = 0.030    # one spectrum


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class ClockedSpectrometer(FakeSpectrometer):
    """The fake spectrometer, on the simulated clock, with an exposure cost."""

    def __init__(self, clock, abort_after=None):
        super().__init__()
        self.clock = clock
        self.calls = 0
        self.abort_after = abort_after

    def set_trigger_mode(self, mode, measconfig=None):
        pass

    def measure(self, abort_event=None, on_armed=None):
        self.calls += 1
        if self.abort_after is not None and self.calls > self.abort_after:
            abort_event.set()
        if abort_event is not None and abort_event.is_set():
            return None
        if on_armed is not None:
            on_armed()
        self.clock.sleep(EXPOSURE_COST)
        return self.clock() * 1e5, self._window(self._synthetic_spectrum())


class CostlyPot(FakePittPotentiostat):
    def pitt_sample(self):
        self.clock.sleep(SAMPLE_COST)
        return super().pitt_sample()


def settings(**over):
    s = dict(DEFAULT_SETTINGS)
    s.update(pitt_start_v=0.0, pitt_stop_v=0.2, pitt_step_mv=100.0,
             pitt_cutoff_pct=1.0, pitt_min_hold_s=2.0, pitt_max_hold_s=60.0,
             pitt_fast_s=2.0, pitt_slow_interval_s=1.0, pitt_return=False,
             pitt_end_dedope=False, chrono_delta_time=0.1, pitt_end_dedope_v=-0.5)
    s.update(over)
    return s


def run(s, **pot_kw):
    clock = Clock()
    pot = CostlyPot(clock=clock, **pot_kw)
    spec = ClockedSpectrometer(clock)
    record = acquire_pitt(spec, pot, pitt_plan(s), s, clock=clock, sleep=clock.sleep)
    return record, pot, clock


# --- the waveform -----------------------------------------------------------

def test_the_cell_stays_on_for_the_whole_staircase():
    """The reason PITT is one segment: no open circuit between steps."""
    record, pot, _ = run(settings())
    assert [e for e, _t in pot.cell_events] == ["on", "off"]
    assert [v for v, _t in pot.setpoints] == pytest.approx([0.0, 0.1, 0.2])
    assert record.completed


def test_a_settling_step_ends_at_its_cutoff():
    record, _, _ = run(settings(), r_series=1e3, c=1e-3)     # tau = 1 s
    later = record.steps[1:]                                  # step 0 has no dV
    assert all(s["end_reason"] == END_CUTOFF for s in later)
    # 1% of the peak at tau ln 100 = 4.6 s -- but the peak is the largest SAMPLED
    # current, and the first sample lands a little after the change, so the cutoff
    # comes that much later.
    for s in later:
        assert s["first_sample_s"] == pytest.approx(SAMPLE_COST, abs=0.01)
        assert s["hold_s"] == pytest.approx(4.6 + s["first_sample_s"], abs=0.12)


def test_a_step_with_no_current_ends_at_its_minimum_hold():
    """Peak zero means nothing relaxed, so there is nothing to wait for. On a real
    cell this is the noise floor, which is what the Start check warns about."""
    record, _, _ = run(settings(pitt_stop_v=0.0), r_series=200.0, c=1e-6,
                       r_leak=3210.0)                        # one step, at 0 V
    assert record.steps[0]["end_reason"] == END_CUTOFF
    assert record.steps[0]["hold_s"] == pytest.approx(2.0, abs=0.12)


def test_the_udc4_case_ends_every_step_at_the_max_hold():
    """A DC path keeps the current up, so only the cap ends a step."""
    # Starts at +0.1 V: at 0 V the leak carries nothing, the peak is zero, and the
    # cutoff is met trivially -- correct, there is nothing to wait for.
    record, _, _ = run(settings(pitt_start_v=0.1, pitt_stop_v=0.3, pitt_max_hold_s=10.0),
                       r_series=200.0, c=1e-6, r_leak=3210.0)
    assert {s["end_reason"] for s in record.steps} == {END_MAX_HOLD}
    assert all(s["hold_s"] == pytest.approx(10.0, abs=0.15) for s in record.steps)


def test_the_current_is_sampled_every_tick_even_when_spectra_slow_down():
    """In Ei mode the sample IS the electrochemistry: thinning it with the spectra
    would leave dQ and the cutoff working from one point a second."""
    record, _, _ = run(settings(pitt_start_v=0.1, pitt_stop_v=0.3, pitt_max_hold_s=10.0),
                       r_series=200.0, c=1e-6, r_leak=3210.0)
    for s in record.steps:
        assert s["n_echem"] == pytest.approx(10.0 / 0.1, abs=3)
        # 2 s at full rate, then one a second for the other 8 s
        assert s["n_spectra"] == pytest.approx(20 + 8, abs=2)


def test_the_charge_of_each_step_is_recovered():
    """dQ = C * dV for an RC step held to completion. A check on the sampling, the
    tagging and the integration together -- the UDC4 gives the same kind of check
    on the bench, with a known resistor instead of a known capacitor."""
    s = settings(pitt_cutoff_pct=0.1)                      # hold to ~7 tau
    record, _, _ = run(s, r_series=1e3, c=1e-3)
    t = np.asarray(record.echem_t_in_step)
    i = np.asarray(record.echem_current)
    k = np.asarray(record.echem_step)
    for step in (1, 2):
        sel = k == step
        ti, ii = t[sel], i[sel]
        q = float(np.sum((ii[1:] + ii[:-1]) / 2 * np.diff(ti)))   # trapezoid; np.trapz is gone in numpy 2
        # The first sample lands one sample-cost after the setpoint change, so the
        # integral misses the start of the transient: exp(-0.05) ~ 5% of dQ.
        assert q == pytest.approx(1e-3 * 0.1 * np.exp(-SAMPLE_COST), rel=0.03)


def test_the_return_leg_and_the_end_dedope_run_in_order():
    s = settings(pitt_return=True, pitt_end_dedope=True, pitt_end_dedope_time_s=5.0)
    record, pot, _ = run(s)
    assert [v for v, _t in pot.setpoints] == pytest.approx(
        [0.0, 0.1, 0.2, 0.1, 0.0, -0.5])
    assert record.steps[-1]["role"] == ROLE_DEDOPE
    assert record.steps[-1]["end_reason"] == END_FIXED
    assert record.steps[-1]["hold_s"] == pytest.approx(5.0, abs=0.15)
    assert [s["role"] for s in record.steps[3:5]] == [ROLE_RETURN, ROLE_RETURN]


def test_stop_ends_the_staircase_keeps_the_data_and_switches_the_cell_off():
    import threading
    stop = threading.Event()
    clock = Clock()
    pot = CostlyPot(clock=clock)
    spec = ClockedSpectrometer(clock)
    s = settings()
    steps_seen = []

    def on_step(k, _step):
        steps_seen.append(k)
        if k == 1:
            stop.set()

    record = acquire_pitt(spec, pot, pitt_plan(s), s, stop_event=stop,
                          clock=clock, sleep=clock.sleep, on_step=on_step)
    assert steps_seen == [0, 1]
    assert record.steps[-1]["end_reason"] == END_STOPPED
    assert not record.completed
    assert record.spectra                                   # kept
    assert [e for e, _t in pot.cell_events] == ["on", "off"]


def test_abort_mid_exposure_still_switches_the_cell_off():
    import threading
    clock = Clock()
    pot = CostlyPot(clock=clock)
    spec = ClockedSpectrometer(clock, abort_after=5)
    s = settings()
    record = acquire_pitt(spec, pot, pitt_plan(s), s, abort_event=threading.Event(),
                          clock=clock, sleep=clock.sleep)
    assert record.steps[-1]["end_reason"] == END_ABORTED
    assert len(record.spectra) == 5                          # what was taken, kept
    assert pot.cell_events[-1][0] == "off"


def test_an_exception_mid_staircase_still_switches_the_cell_off():
    clock = Clock()

    class Breaks(CostlyPot):
        def pitt_set_potential(self, v):
            raise RuntimeError("instrument went away")

    pot = Breaks(clock=clock)
    s = settings()
    with pytest.raises(RuntimeError):
        acquire_pitt(ClockedSpectrometer(clock), pot, pitt_plan(s), s,
                     clock=clock, sleep=clock.sleep)
    assert pot.cell_events[-1][0] == "off"


# --- the file ---------------------------------------------------------------

def _written(tmp_path, s=None, **pot_kw):
    s = s or settings()
    record, _, _ = run(s, **pot_kw)
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]
    dark = np.full(wl.size, 100.0)
    ref = np.full(wl.size, 40000.0)
    path = write_pitt_h5(record, dark, ref, wl, tmp_path, "20261004_pitt", settings=s)
    return record, path


def test_each_step_is_its_own_group_with_its_setpoint(tmp_path):
    record, path = _written(tmp_path)
    assert path == h5_path(tmp_path / "20261004_pitt", DATA_TYPE_PITT)
    with h5py.File(path, "r") as f:
        groups = sorted(int(k) for k in f if k != "wavelength")
        assert groups == [0, 1, 2]
        for k, v in zip(groups, (0.0, 0.1, 0.2)):
            g = f[str(k)]
            assert g.attrs["potential_set"] == pytest.approx(v)
            assert g.attrs["label"] == f"PITT {k}"
            assert g.attrs["end_reason"] in (END_CUTOFF, END_MAX_HOLD)
            assert g.attrs["n_spectra"] == g["absorbance_vs_time"].shape[1]
            assert "time_potentiostat" in g["echem"]
        assert f.attrs["data_type_name"] == "PITT"
        assert bool(f.attrs["pitt_completed"])
        import json
        assert [st["index"] for st in json.loads(f.attrs["pitt_steps_json"])] == [0, 1, 2]


def test_the_potentiostat_clock_runs_on_across_steps(tmp_path):
    _, path = _written(tmp_path)
    with h5py.File(path, "r") as f:
        ends = [f[str(k)]["echem"]["time_potentiostat"][-1] for k in (0, 1)]
        starts = [f[str(k)]["echem"]["time_potentiostat"][0] for k in (1, 2)]
        per_step = [f[str(k)]["echem"]["time"][0] for k in (1, 2)]
    assert all(b > a for a, b in zip(ends, starts))          # continuous, increasing
    assert all(t < 0.2 for t in per_step)                    # rebased per step


def test_no_text_files_are_written(tmp_path):
    """HDF5 only, and nothing named like 'spectra(' for the downstream reader."""
    _written(tmp_path)
    files = sorted(p.name for p in (tmp_path / "20261004_pitt").iterdir())
    assert files == ["20261004_pitt_pitt.h5"]


def test_the_steps_are_found_and_read_like_segments(tmp_path):
    _written(tmp_path)
    found = discover_run_h5(tmp_path / "20261004_pitt")
    assert [(label, n) for label, _t, n, _p in found] == [
        ("PITT 0", 0), ("PITT 1", 1), ("PITT 2", 2)]
    df = read_segment_h5(found[1][3], 1)
    assert df.shape[0] > 1000 and df.shape[1] > 1


def test_a_second_staircase_replaces_rather_than_merges(tmp_path):
    _written(tmp_path, s=settings(pitt_stop_v=0.3))           # 4 steps
    _, path = _written(tmp_path, s=settings(pitt_stop_v=0.1))  # then 2
    with h5py.File(path, "r") as f:
        assert sorted(int(k) for k in f if k != "wavelength") == [0, 1]


# --- wiring into a run -------------------------------------------------------

from spec_echem.experiment import (build_segments, pitt_start_problems,   # noqa: E402
                                   pitt_step_segments, run_pitt_segment)


def test_the_pitt_runs_after_the_whole_ladder_as_one_segment():
    s = settings(pitt_enabled=True, cv_enabled=True, prededoping_enabled=True,
                 doping_enabled=True, doping_potential_start=0.3,
                 doping_potential_end=0.5, doping_potential_step=0.1)
    labels = [seg.label for seg in build_segments(s)]
    assert labels[-1] == "PITT" and labels.count("PITT") == 1
    assert labels.index("PITT") > labels.index("Dedoping 2")
    assert build_segments(s)[-1].num_points == 3           # steps, not spectra


def test_no_pitt_segment_unless_ticked():
    assert "PITT" not in [seg.label for seg in build_segments(settings())]
    assert pitt_step_segments(settings()) == []


def test_each_step_is_registered_by_label():
    segs = pitt_step_segments(settings(pitt_enabled=True))
    assert [seg.label for seg in segs] == ["PITT 0", "PITT 1", "PITT 2"]
    assert [seg.run_number for seg in segs] == [0, 1, 2]
    assert {seg.data_type for seg in segs} == {DATA_TYPE_PITT}


@pytest.mark.parametrize("mode, words", [
    ("external", "sequence file owns the waveform"),
    ("autolab", "Ei setpoint can change with the cell on"),
    ("python", "64-bit toolkit"),
])
def test_start_says_plainly_which_potentiostat_cannot_run_it(mode, words):
    problems = pitt_start_problems(settings(pitt_enabled=True, potentiostat_mode=mode))
    assert any("cannot run a PITT yet" in p and words in p for p in problems), problems


def test_start_refuses_without_h5py_because_nothing_would_be_saved(monkeypatch):
    import spec_echem.data as data
    monkeypatch.setattr(data, "H5PY_AVAILABLE", False)
    problems = pitt_start_problems(settings(pitt_enabled=True))
    assert any("HDF5 only" in p for p in problems)


def test_start_is_untouched_when_the_pitt_is_not_ticked():
    assert pitt_start_problems(settings(potentiostat_mode="external")) == []


def _quick():
    """Real time, so kept to about a second."""
    return settings(pitt_enabled=True, cv_enabled=False, prededoping_enabled=False,
                    doping_enabled=False, pitt_start_v=0.0, pitt_stop_v=0.1,
                    pitt_step_mv=100.0, pitt_min_hold_s=0.2, pitt_max_hold_s=0.4,
                    pitt_fast_s=0.2, pitt_slow_interval_s=0.1, chrono_delta_time=0.05)


def test_run_pitt_segment_writes_the_file_and_returns_every_step(tmp_path):
    s = _quick()
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]
    seg = build_segments(s)[-1]
    steps, path, record = run_pitt_segment(
        spec, seg, np.full(wl.size, 100.0), np.full(wl.size, 40000.0), wl,
        tmp_path, "20261004_pitt", s, FakePittPotentiostat(r_leak=3210.0))
    assert [label for label, _df in steps] == ["PITT 0", "PITT 1"]
    assert path is not None and path.exists()
    assert record.completed


def test_the_worker_emits_each_step_then_finishes(tmp_path):
    pytest.importorskip("qtpy")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from gui.workers import AcquisitionWorker

    s = _quick()
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]

    class Pot(FakePittPotentiostat):
        python_paced = True
        settings = s
        def open(self): pass
        def close(self): pass
        def stop(self): pass
        def device_lost(self): return False

    w = AcquisitionWorker(spec, build_segments(s), np.full(wl.size, 100.0),
                          np.full(wl.size, 40000.0), wl, tmp_path, "20261004_pitt",
                          potentiostat=Pot(r_leak=3210.0), settings=s)
    done, finished = [], []
    w.segment_done.connect(lambda label, df: done.append(label))
    w.finished.connect(finished.append)
    w.gui_idle.set()
    w.run()
    assert done == ["PITT 0", "PITT 1"]
    assert finished == ["done"]
