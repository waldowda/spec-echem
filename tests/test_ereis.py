"""ER-EIS: the plan, the sweep, the loop, its HDF5 and Start's checks (fakes only)."""
import json
import threading

import numpy as np
import pytest

from spec_echem import ereis
from spec_echem.acquisition import acquire_pitt
from spec_echem.data import (DATA_TYPE_EREIS, discover_run_h5, h5_path,
                             read_ereis_impedance_h5, read_segment_h5,
                             segment_potential)
from spec_echem.experiment import (build_segments, ereis_live, ereis_start_problems,
                                   ereis_step_segments, run_duration_bounds,
                                   run_ereis_segment)
from spec_echem.fakes import FakeEreisPotentiostat, FakeSpectrometer
from spec_echem.pitt import ROLE_FORWARD, PittRecord
from spec_echem.settings import DEFAULT_SETTINGS


def quick(**over):
    """Real time, so kept to about a second."""
    s = dict(DEFAULT_SETTINGS, ereis_enabled=True, cv_enabled=False,
             prededoping_enabled=False, doping_enabled=False, pitt_enabled=False,
             gitt_enabled=False, ereis_start_v=0.0, ereis_stop_v=0.1,
             ereis_step_mv=50.0, ereis_hold_s=0.15, ereis_f_high_hz=1e4,
             ereis_f_low_hz=1.0, ereis_points_per_decade=2, ereis_fast_s=0.05,
             ereis_slow_interval_s=0.05, chrono_delta_time=0.02,
             potentiostat_mode="python")
    s.update(over)
    return s


# --- the plan --------------------------------------------------------------------

def test_the_plan_is_a_staircase_whose_stop_is_a_ceiling():
    plan = ereis.ereis_plan(quick(ereis_stop_v=0.12))
    assert [p.potential for p in plan] == [0.0, 0.05, 0.1]
    assert {p.role for p in plan} == {ROLE_FORWARD}
    down = ereis.ereis_plan(quick(ereis_start_v=0.1, ereis_stop_v=0.0))
    assert [p.potential for p in down] == [0.1, 0.05, 0.0]


def test_repeat_first_sweeps_the_first_potential_again_at_the_end():
    plan = ereis.ereis_plan(quick(ereis_repeat_first=True))
    assert [p.potential for p in plan] == [0.0, 0.05, 0.1, 0.0]
    assert plan[-1].role == ereis.ROLE_REPEAT and plan[-1].index == 3


def test_frequencies_run_high_to_low_with_both_ends_included():
    f = ereis.ereis_frequencies(quick(ereis_f_high_hz=1e4, ereis_f_low_hz=0.1,
                                      ereis_points_per_decade=5))
    assert len(f) == 26
    assert f[0] == pytest.approx(1e4) and f[-1] == pytest.approx(0.1)
    assert all(a > b for a, b in zip(f, f[1:]))
    assert f[5] == pytest.approx(1e3)                   # 5 per decade, exactly


def test_the_duration_follows_the_measured_seconds_per_frequency():
    # 1 Hz took 3.09 s and 100 Hz 0.32 s on the Reference 600 (2026-10-23).
    assert ereis.ereis_point_s(1.0) == pytest.approx(3.1)
    assert ereis.ereis_point_s(100.0) == pytest.approx(0.328)
    s = quick()
    expected = 3 * (0.15 + sum(ereis.ereis_point_s(f)
                               for f in ereis.ereis_frequencies(s)))
    assert ereis.ereis_duration_s(s) == pytest.approx(expected)
    lo, hi, _n = run_duration_bounds(s)
    assert lo == hi == pytest.approx(expected)


@pytest.mark.parametrize("over, words", [
    (dict(ereis_step_mv=0.0), "larger than 0 mV"),
    (dict(ereis_hold_s=0.0), "hold"),
    (dict(ereis_f_low_hz=0.0), "larger than 0 Hz"),
    (dict(ereis_f_low_hz=1e4, ereis_f_high_hz=10.0), "high to low"),
    (dict(ereis_f_high_hz=2e6), "Reference 600 reaches"),
    (dict(ereis_points_per_decade=0), "point per decade"),
    (dict(ereis_vac_mv=0.0), "AC amplitude"),
    (dict(ereis_slow_interval_s=0.01), "slow spectrum interval"),
])
def test_settings_that_cannot_run_are_named(over, words):
    problems = ereis.ereis_problems(quick(**over))
    assert len(problems) == 1 and words in problems[0]


def test_good_settings_have_no_problems():
    assert ereis.ereis_problems(quick()) == []
    assert ereis.ereis_problems(dict(DEFAULT_SETTINGS)) == []


# --- Start --------------------------------------------------------------------------

def test_only_the_gamry_can_run_one_for_now():
    assert ereis_start_problems(quick()) == []
    autolab = ereis_start_problems(quick(potentiostat_mode="autolab"))
    assert len(autolab) == 1 and "FRA32M" in autolab[0]
    external = ereis_start_problems(quick(potentiostat_mode="external"))
    assert len(external) == 1 and "External" in external[0]
    assert ereis_start_problems(quick(ereis_enabled=False,
                                      potentiostat_mode="autolab")) == []


def test_it_runs_after_everything_as_one_segment_and_registers_each_step():
    segs = build_segments(quick(gitt_enabled=True, pitt_enabled=True))
    assert [seg.label for seg in segs][-3:] == ["PITT", "GITT", "ER-EIS"]
    assert segs[-1].data_type == DATA_TYPE_EREIS and segs[-1].num_points == 3
    assert [seg.label for seg in ereis_step_segments(quick())] == [
        "ER-EIS 0", "ER-EIS 1", "ER-EIS 2"]
    assert ereis_step_segments(quick(ereis_enabled=False)) == []
    assert segment_potential(quick(), DATA_TYPE_EREIS, 1) == 0.05
    assert segment_potential(quick(), DATA_TYPE_EREIS, 9) is None


# --- the loop -----------------------------------------------------------------------

def _loop(s, pot=None, **kw):
    pot = pot or FakeEreisPotentiostat(s, point_s=0.02)
    record = acquire_pitt(FakeSpectrometer(), pot, ereis.ereis_plan(s), s,
                          kind="ereis", **kw)
    return pot, record


def test_each_hold_is_followed_by_its_sweep_with_the_cell_left_on():
    s = quick()
    pot, record = _loop(s)
    assert record.completed
    assert pot.cell_events[0][0] == "on" and pot.cell_events[-1][0] == "off"
    assert len(pot.cell_events) == 2                       # on once, off once
    assert [v for v, _t, on in pot.sweeps] == [0.0, 0.05, 0.1]
    assert all(on for _v, _t, on in pot.sweeps)
    n = len(ereis.ereis_frequencies(s))
    for st in record.steps:
        assert st["end_reason"] == "fixed"
        assert st["sweep_end"] == ereis.SWEEP_COMPLETE and st["n_freq"] == n
        mine = [p["frequency_set"] for p in record.impedance if p["step"] == st["index"]]
        assert mine == pytest.approx(ereis.ereis_frequencies(s))
    # the sweep starts only once the hold is over: the setpoints change after it
    t_sweep = [t for _v, t, _on in pot.sweeps]
    t_step = [t for _v, t in pot.setpoints]
    assert t_sweep[0] < t_step[1] and t_sweep[1] < t_step[2]


def test_spectra_carry_on_through_the_sweep():
    s = quick()
    _pot, record = _loop(s, FakeEreisPotentiostat(s, point_s=0.05))
    hold = s["ereis_hold_s"]
    for st in record.steps:
        assert st["sweep_s"] >= 0.05 * st["n_freq"] * 0.9
        # spectra counted after the sweep began: the step's n_spectra includes them
        assert st["n_spectra"] == sum(1 for k in record.spectrum_step
                                      if k == st["index"])
    # roughly one spectrum per slow interval during each sweep
    total = sum(st["n_spectra"] for st in record.steps)
    no_sweep = sum(1 for _ in range(3)) * (hold / s["chrono_delta_time"] + 1)
    assert total > no_sweep


def test_stop_during_a_sweep_finishes_the_frequency_in_hand_then_ends_the_run():
    s = quick()
    pot = FakeEreisPotentiostat(s, point_s=0.05)
    stop = threading.Event()
    threading.Timer(s["ereis_hold_s"] + 0.12, stop.set).start()
    _pot, record = _loop(s, pot, stop_event=stop)
    assert not record.completed and pot.cancelled == 1
    assert len(record.steps) == 1
    st = record.steps[0]
    assert st["sweep_end"] == ereis.SWEEP_STOPPED
    assert 1 <= st["n_freq"] < len(ereis.ereis_frequencies(s))
    assert pot.cell_events[-1][0] == "off"


def test_abort_during_a_sweep_ends_it_at_once():
    s = quick()
    pot = FakeEreisPotentiostat(s, point_s=0.05)
    abort = threading.Event()
    threading.Timer(s["ereis_hold_s"] + 0.12, abort.set).start()
    _pot, record = _loop(s, pot, abort_event=abort)
    assert not record.completed
    assert record.steps[0]["sweep_end"] == ereis.SWEEP_ABORTED
    assert pot.cell_events[-1][0] == "off"


def test_stop_during_a_hold_runs_no_sweep():
    s = quick(ereis_hold_s=1.0)
    pot = FakeEreisPotentiostat(s)
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    _pot, record = _loop(s, pot, stop_event=stop)
    assert pot.sweeps == []
    assert record.steps[0]["end_reason"] == "stopped"
    assert "sweep_end" not in record.steps[0]


# --- the file -----------------------------------------------------------------------

def _run(tmp_path, s, pot=None):
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]
    seg = build_segments(s)[-1]
    return run_ereis_segment(spec, seg, np.full(wl.size, 100.0),
                             np.full(wl.size, 40000.0), wl, tmp_path,
                             "20261112_ereis", s,
                             pot or FakeEreisPotentiostat(s, point_s=0.02))


def test_the_file_has_one_group_per_step_with_its_impedance(tmp_path):
    h5py = pytest.importorskip("h5py")
    s = quick()
    pot = FakeEreisPotentiostat(s, point_s=0.02)
    steps, path, record = _run(tmp_path, s, pot)
    assert [label for label, _df in steps] == ["ER-EIS 0", "ER-EIS 1", "ER-EIS 2"]
    assert path == h5_path(tmp_path / "20261112_ereis", DATA_TYPE_EREIS)
    with h5py.File(path, "r") as f:
        assert f.attrs["data_type_name"] == "ER-EIS"
        assert bool(f.attrs["ereis_completed"])
        table = json.loads(f.attrs["ereis_steps_json"])
        assert [st["sweep_end"] for st in table] == ["complete"] * 3
        g = f["1"]
        assert g.attrs["label"] == "ER-EIS 1"
        assert g.attrs["potential_set"] == pytest.approx(0.05)
        assert g.attrs["sweep_end"] == "complete"
        assert g["impedance"]["zreal"].attrs["units"] == "Ohm"
        assert "time_potentiostat" in g["echem"]
    z = read_ereis_impedance_h5(path, 1)
    freqs = ereis.ereis_frequencies(s)
    assert list(z["frequency_set"]) == pytest.approx(freqs)
    expected = [pot.z(f) for f in freqs]
    assert list(z["zreal"]) == pytest.approx([e.real for e in expected])
    assert list(z["zimag"]) == pytest.approx([e.imag for e in expected])
    assert set(z["ok"]) == {1}
    assert not any(p.suffix == ".txt" for p in path.parent.iterdir())


def test_the_steps_are_found_and_read_like_segments(tmp_path):
    pytest.importorskip("h5py")
    _steps, path, _record = _run(tmp_path, quick())
    found = discover_run_h5(path.parent)
    assert [f[0] for f in found] == ["ER-EIS 0", "ER-EIS 1", "ER-EIS 2"]
    assert read_segment_h5(path, 2) is not None


def test_a_failed_frequency_is_kept_and_marked(tmp_path):
    pytest.importorskip("h5py")
    s = quick()

    class Flaky(FakeEreisPotentiostat):
        def ereis_poll(self):
            out = super().ereis_poll()
            for p in out:
                if p["frequency_set"] == pytest.approx(100.0):
                    p.update(ok=False, zreal=float("nan"), zimag=float("nan"))
            return out

    _steps, path, record = _run(tmp_path, s, Flaky(s, point_s=0.02))
    n = len(ereis.ereis_frequencies(s))
    assert all(st["n_freq"] == n - 1 for st in record.steps)
    z = read_ereis_impedance_h5(path, 0)
    assert len(z) == n and int(z["ok"].sum()) == n - 1


def test_the_current_after_a_sweep_is_compared_with_before(tmp_path, caplog):
    """A clipped hold after the sweep -- the range not restored -- is named, never
    changed."""
    from spec_echem.experiment import _check_current_after_sweep
    import logging
    record = PittRecord()
    record.echem_step = [0] * 6
    record.echem_t_in_step = [0.5, 0.9, 1.0, 3.1, 3.2, 3.3]
    record.echem_current = [10e-6, 10e-6, 10e-6, 2e-6, 2e-6, 2e-6]
    st = {"index": 0, "hold_s": 1.0, "sweep_s": 2.0, "sweep_end": "complete"}
    log = logging.getLogger("t")
    with caplog.at_level(logging.WARNING, logger="t"):
        _check_current_after_sweep(record, st, 1.0, log)
    assert st["current_before_sweep_A"] == pytest.approx(10e-6)
    assert st["current_after_sweep_A"] == pytest.approx(2e-6)
    assert "not restored" in caplog.text
    caplog.clear()
    record.echem_current[3:] = [9e-6] * 3                  # a film still relaxing
    with caplog.at_level(logging.WARNING, logger="t"):
        _check_current_after_sweep(record, st, 1.0, log)
    assert caplog.text == ""


def test_the_live_readout_says_how_far_the_sweep_has_got():
    s = quick()
    record = PittRecord()
    record.echem_step = [0, 0]
    record.echem_t_in_step = [0.1, 0.2]
    record.echem_potential = [0.0, 0.0]
    record.echem_current = [1e-6, 1e-6]
    record.impedance = [{"step": 0, "frequency_set": 1e4, "zmod": 210.0},
                        {"step": 0, "frequency_set": 3162.0, "zmod": 3210.0}]
    _t, _i, status = ereis_live(record, 3, 9)
    assert "Sweep 2 of 9" in status and "3.21 kΩ" in status
    record.impedance = []
    _t, _i, status = ereis_live(record, 3, 9)
    assert "Sweep" not in status


# --- the worker and the GUI ---------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("qtpy")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_worker_emits_each_step_then_finishes(app, tmp_path):
    pytest.importorskip("h5py")
    from gui.workers import AcquisitionWorker
    s = quick()
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]

    class Pot(FakeEreisPotentiostat):
        python_paced = True
        def open(self): pass
        def close(self): pass
        def stop(self): pass
        def device_lost(self): return False

    w = AcquisitionWorker(spec, build_segments(s), np.full(wl.size, 100.0),
                          np.full(wl.size, 40000.0), wl, tmp_path, "20261112_ereis",
                          potentiostat=Pot(s, point_s=0.02), settings=s)
    done, finished = [], []
    w.segment_done.connect(lambda label, df: done.append(label))
    w.finished.connect(finished.append)
    w.gui_idle.set()
    w.run()
    assert done == ["ER-EIS 0", "ER-EIS 1", "ER-EIS 2"]
    assert finished == ["done"]
    assert len(w.pitt_record.impedance) == 3 * len(ereis.ereis_frequencies(s))


@pytest.fixture
def window(app):
    from unittest.mock import patch
    import gui.main_window as _mw
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        win = _mw.MainWindow()
    yield win
    win.close()


def test_the_group_round_trips_its_settings_and_estimates_the_time(window):
    tab = window.parameters_tab
    s = dict(DEFAULT_SETTINGS, ereis_enabled=True, ereis_f_low_hz=0.5,
             ereis_points_per_decade=7, ereis_repeat_first=True)
    tab.populate_from(s)
    out = {}
    tab.collect_into(out)
    assert out["ereis_enabled"] is True and out["ereis_repeat_first"] is True
    assert out["ereis_f_low_hz"] == pytest.approx(0.5)
    assert out["ereis_points_per_decade"] == 7
    tab.populate_from(dict(DEFAULT_SETTINGS))
    # -0.5 to +0.7 V in 50 mV = 25 steps; 10 kHz to 0.1 Hz at 5 per decade = 26
    assert "25 steps x 26 frequencies" in tab.ereis_estimate.text()


def test_impossible_settings_show_in_red_before_start(window):
    tab = window.parameters_tab
    tab.populate_from(dict(DEFAULT_SETTINGS, ereis_f_high_hz=1.0, ereis_f_low_hz=10.0))
    assert "high to low" in tab.ereis_problems_label.text()
    assert tab.ereis_estimate.text() == ""


def test_results_shows_a_steps_nyquist_plot(window, tmp_path):
    pytest.importorskip("h5py")
    s = quick()
    _steps, path, _record = _run(tmp_path, s)
    for seg in ereis_step_segments(s):
        window.segments_by_label[seg.label] = seg
    window.run_folder = path.parent
    window.results_tab._plot_echem("ER-EIS 1")
    canvas = window.results_tab.echem_canvas
    assert window.results_tab._has_echem
    assert canvas.ax.get_xlabel() == "Z′ (Ω)"
    x = canvas.ax.get_lines()[0].get_xdata()
    assert len(x) == len(ereis.ereis_frequencies(s))
