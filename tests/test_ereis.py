"""ER-EIS: impedance sweeps as a box on the PITT -- which steps sweep, the sweep,
the loop, the PITT's HDF5 and Start's checks (fakes only)."""
import json
import threading

import numpy as np
import pytest

from spec_echem import ereis
from spec_echem.acquisition import acquire_pitt
from spec_echem.data import (DATA_TYPE_PITT, discover_run_h5, h5_path,
                             read_impedance_h5, read_segment_h5)
from spec_echem.experiment import (build_segments, ereis_live, ereis_live_nyquist,
                                   ereis_start_problems,
                                   pitt_start_problems, pitt_step_segments,
                                   run_duration_bounds, run_pitt_segment)
from spec_echem.fakes import FakeEreisPotentiostat, FakeSpectrometer
from spec_echem.pitt import (ROLE_DEDOPE, ROLE_RETURN, PittRecord,
                             pitt_duration_bounds, pitt_plan)
from spec_echem.settings import DEFAULT_SETTINGS


def quick(**over):
    """A three-step PITT with a sweep after every hold. Real time: about a second."""
    s = dict(DEFAULT_SETTINGS, cv_enabled=False, prededoping_enabled=False,
             doping_enabled=False, gitt_enabled=False, pitt_enabled=True,
             pitt_eis_enabled=True, pitt_start_v=0.0, pitt_stop_v=0.1,
             pitt_step_mv=50.0, pitt_cutoff_enabled=False, pitt_min_hold_s=0.05,
             pitt_max_hold_s=0.15, pitt_fast_s=0.05, pitt_slow_interval_s=0.05,
             pitt_eis_f_high_hz=1e4, pitt_eis_f_low_hz=1.0,
             pitt_eis_points_per_decade=2, chrono_delta_time=0.02,
             potentiostat_mode="python")
    s.update(over)
    return s


# --- which steps sweep, the frequencies, the time ---------------------------------

def test_every_step_sweeps_by_default_and_none_without_the_box():
    assert ereis.ereis_sweep_steps(quick()) == {0, 1, 2}
    assert ereis.ereis_sweep_steps(quick(pitt_eis_enabled=False)) == set()


def test_every_nth_potential_is_swept_the_same_on_the_way_back_never_the_dedope():
    s = quick(pitt_stop_v=0.2, pitt_return=True, pitt_end_dedope=True,
              pitt_end_dedope_v=-0.15,          # 3 steps from the start: due a sweep
              pitt_eis_every_n=3)
    plan = pitt_plan(s)
    swept = ereis.ereis_sweep_steps(s, plan)
    by_index = {p.index: p for p in plan}
    assert sorted(round(by_index[k].potential, 3) for k in swept
                  if by_index[k].role != ROLE_RETURN) == [0.0, 0.15]
    assert sorted(round(by_index[k].potential, 3) for k in swept
                  if by_index[k].role == ROLE_RETURN) == [0.0, 0.15]
    assert not any(by_index[k].role == ROLE_DEDOPE for k in swept)


def test_frequencies_run_high_to_low_with_both_ends_included():
    f = ereis.ereis_frequencies(quick(pitt_eis_f_high_hz=1e4, pitt_eis_f_low_hz=0.1,
                                      pitt_eis_points_per_decade=5))
    assert len(f) == 26
    assert f[0] == pytest.approx(1e4) and f[-1] == pytest.approx(0.1)
    assert all(a > b for a, b in zip(f, f[1:]))
    assert f[5] == pytest.approx(1e3)                   # 5 per decade, exactly


def test_the_sweeps_add_their_measured_seconds_to_the_pitts_time():
    # 1 Hz took 3.09 s and 100 Hz 0.32 s on the Reference 600 (2026-10-23).
    assert ereis.ereis_point_s(1.0) == pytest.approx(3.1)
    assert ereis.ereis_point_s(100.0) == pytest.approx(0.328)
    s = quick()
    sweep = sum(ereis.ereis_point_s(f) for f in ereis.ereis_frequencies(s))
    assert ereis.ereis_duration_s(s) == pytest.approx(3 * sweep)
    assert ereis.ereis_duration_s(quick(pitt_eis_every_n=2)) == pytest.approx(2 * sweep)
    lo, hi = pitt_duration_bounds(s)
    assert lo == hi == pytest.approx(3 * 0.15 + 3 * sweep)
    assert run_duration_bounds(s)[:2] == pytest.approx((lo, hi))
    assert pitt_duration_bounds(quick(pitt_eis_enabled=False)) == pytest.approx(
        (0.45, 0.45))


@pytest.mark.parametrize("over, words", [
    (dict(pitt_eis_f_low_hz=0.0), "larger than 0 Hz"),
    (dict(pitt_eis_f_low_hz=1e4, pitt_eis_f_high_hz=10.0), "high to low"),
    (dict(pitt_eis_f_high_hz=2e6), "Reference 600 reaches"),
    (dict(pitt_eis_points_per_decade=0), "point per decade"),
    (dict(pitt_eis_vac_mv=0.0), "AC amplitude"),
    (dict(pitt_eis_every_n=0), "N must be 1"),
])
def test_settings_that_cannot_run_are_named(over, words):
    problems = ereis.ereis_problems(quick(**over))
    assert len(problems) == 1 and words in problems[0]


def test_good_settings_have_no_problems_and_an_unticked_box_has_none():
    assert ereis.ereis_problems(quick()) == []
    assert ereis.ereis_problems(quick(pitt_eis_enabled=False,
                                      pitt_eis_f_low_hz=0.0)) == []
    assert ereis.ereis_problems(quick(pitt_enabled=False,
                                      pitt_eis_f_low_hz=0.0)) == []


# --- Start --------------------------------------------------------------------------

def test_only_the_gamry_can_sweep_for_now():
    assert ereis_start_problems(quick()) == []
    autolab = ereis_start_problems(quick(potentiostat_mode="autolab"))
    assert len(autolab) == 1 and "FRA32M" in autolab[0]
    external = ereis_start_problems(quick(potentiostat_mode="external"))
    assert len(external) == 1 and "External" in external[0]
    assert ereis_start_problems(quick(pitt_eis_enabled=False,
                                      potentiostat_mode="autolab")) == []
    # the Autolab still runs a plain PITT
    assert pitt_start_problems(quick(pitt_eis_enabled=False,
                                     potentiostat_mode="autolab")) == []


def test_it_is_part_of_the_pitt_not_a_segment_of_its_own():
    segs = build_segments(quick(gitt_enabled=True))
    assert [seg.label for seg in segs][-2:] == ["PITT", "GITT"]
    assert [seg.label for seg in pitt_step_segments(quick())] == [
        "PITT 0", "PITT 1", "PITT 2"]


# --- the loop -----------------------------------------------------------------------

def _loop(s, pot=None, **kw):
    pot = pot or FakeEreisPotentiostat(s, point_s=0.02)
    record = acquire_pitt(FakeSpectrometer(), pot, pitt_plan(s), s, **kw)
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
        assert st["end_reason"] not in ("stopped", "aborted")
        assert st["sweep_end"] == ereis.SWEEP_COMPLETE and st["n_freq"] == n
        mine = [p["frequency_set"] for p in record.impedance if p["step"] == st["index"]]
        assert mine == pytest.approx(ereis.ereis_frequencies(s))
    # the sweep starts only once the hold is over: the setpoints change after it
    t_sweep = [t for _v, t, _on in pot.sweeps]
    t_step = [t for _v, t in pot.setpoints]
    assert t_sweep[0] < t_step[1] and t_sweep[1] < t_step[2]


def test_only_the_chosen_steps_sweep():
    s = quick(pitt_eis_every_n=2)
    pot, record = _loop(s)
    assert record.completed
    assert [v for v, _t, _on in pot.sweeps] == [0.0, 0.1]
    assert "sweep_end" not in record.steps[1]
    assert {p["step"] for p in record.impedance} == {0, 2}


def test_without_the_box_it_is_a_plain_pitt():
    s = quick(pitt_eis_enabled=False)

    class Plain(FakeEreisPotentiostat):
        def ereis_prepare(self, potential):
            raise AssertionError("a plain PITT must not prepare for sweeps")

    pot, record = _loop(s, Plain(s))
    assert record.completed and pot.sweeps == [] and record.impedance == []
    assert not any("sweep_end" in st for st in record.steps)


def test_spectra_carry_on_through_the_sweep():
    s = quick()
    _pot, record = _loop(s, FakeEreisPotentiostat(s, point_s=0.05))
    for st in record.steps:
        assert st["sweep_s"] >= 0.05 * st["n_freq"] * 0.9
        assert st["n_spectra"] == sum(1 for k in record.spectrum_step
                                      if k == st["index"])
    total = sum(st["n_spectra"] for st in record.steps)
    no_sweep = 3 * (s["pitt_max_hold_s"] / s["chrono_delta_time"] + 1)
    assert total > no_sweep


def test_stop_during_a_sweep_finishes_the_frequency_in_hand_then_ends_the_run():
    s = quick()
    pot = FakeEreisPotentiostat(s, point_s=0.05)
    stop = threading.Event()
    threading.Timer(s["pitt_max_hold_s"] + 0.12, stop.set).start()
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
    threading.Timer(s["pitt_max_hold_s"] + 0.12, abort.set).start()
    _pot, record = _loop(s, pot, abort_event=abort)
    assert not record.completed
    assert record.steps[0]["sweep_end"] == ereis.SWEEP_ABORTED
    assert pot.cell_events[-1][0] == "off"


def test_stop_during_a_hold_runs_no_sweep():
    s = quick(pitt_max_hold_s=1.0)
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
    seg = [seg for seg in build_segments(s) if seg.data_type == DATA_TYPE_PITT][0]
    return run_pitt_segment(spec, seg, np.full(wl.size, 100.0),
                            np.full(wl.size, 40000.0), wl, tmp_path,
                            "20261112_eis", s,
                            pot or FakeEreisPotentiostat(s, point_s=0.02))


def test_the_pitt_file_holds_each_swept_steps_impedance(tmp_path, caplog):
    import logging
    from spec_echem.logging_config import RUN_LOGGER_NAME
    h5py = pytest.importorskip("h5py")
    s = quick(pitt_eis_every_n=2)
    pot = FakeEreisPotentiostat(s, point_s=0.02)
    with caplog.at_level(logging.INFO, logger=RUN_LOGGER_NAME):
        steps, path, _record = _run(tmp_path, s, pot)
    swept = [r.getMessage() for r in caplog.records if "impedance: sweep" in r.getMessage()]
    assert len(swept) == 2 and swept[0].startswith("PITT 0 impedance: sweep complete")
    assert [label for label, _df in steps] == ["PITT 0", "PITT 1", "PITT 2"]
    assert path == h5_path(tmp_path / "20261112_eis", DATA_TYPE_PITT)
    assert sorted(p.name for p in path.parent.glob("*.h5")) == ["20261112_eis_pitt.h5"]
    with h5py.File(path, "r") as f:
        assert f.attrs["data_type_name"] == "PITT"
        table = json.loads(f.attrs["pitt_steps_json"])
        assert [st.get("sweep_end") for st in table] == ["complete", None, "complete"]
        g = f["2"]
        assert g.attrs["label"] == "PITT 2"
        assert g.attrs["potential_set"] == pytest.approx(0.1)
        assert g.attrs["sweep_end"] == "complete"
        assert "peak_current_A" in g.attrs
        assert g["impedance"]["zreal"].attrs["units"] == "Ohm"
        assert "time_potentiostat" in g["echem"]
        assert "impedance" not in f["1"] and "sweep_end" not in f["1"].attrs
    z = read_impedance_h5(path, 2)
    freqs = ereis.ereis_frequencies(s)
    assert list(z["frequency_set"]) == pytest.approx(freqs)
    expected = [pot.z(f) for f in freqs]
    assert list(z["zreal"]) == pytest.approx([e.real for e in expected])
    assert list(z["zimag"]) == pytest.approx([e.imag for e in expected])
    assert set(z["ok"]) == {1}
    assert read_impedance_h5(path, 1) is None


def test_the_steps_are_found_and_read_like_segments(tmp_path):
    pytest.importorskip("h5py")
    _steps, path, _record = _run(tmp_path, quick())
    found = discover_run_h5(path.parent)
    assert [f[0] for f in found] == ["PITT 0", "PITT 1", "PITT 2"]
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
    z = read_impedance_h5(path, 0)
    assert len(z) == n and int(z["ok"].sum()) == n - 1


def test_the_current_after_a_sweep_is_compared_with_before(caplog):
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
        _check_current_after_sweep(record, st, log)
    assert st["current_before_sweep_A"] == pytest.approx(10e-6)
    assert st["current_after_sweep_A"] == pytest.approx(2e-6)
    assert "not restored" in caplog.text
    caplog.clear()
    record.echem_current[3:] = [9e-6] * 3                  # a film still relaxing
    with caplog.at_level(logging.WARNING, logger="t"):
        _check_current_after_sweep(record, st, log)
    assert caplog.text == ""


def test_the_live_readout_says_how_far_the_sweep_has_got():
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


def test_the_live_nyquist_follows_the_running_step_and_skips_failed_points():
    record = PittRecord()
    assert ereis_live_nyquist(record) is None
    record.echem_step = [0, 0]
    record.echem_t_in_step = [0.1, 0.2]
    assert ereis_live_nyquist(record) is None               # hold: no sweep yet
    record.impedance = [
        {"step": 0, "zreal": 210.0, "zimag": -20.0, "ok": True},
        {"step": 0, "zreal": 900.0, "zimag": -1400.0, "ok": False},
        {"step": 0, "zreal": 1700.0, "zimag": -1500.0, "ok": True}]
    assert ereis_live_nyquist(record) == ([210.0, 1700.0], [20.0, 1500.0])
    record.echem_step.append(1)                             # the next hold began
    record.echem_t_in_step.append(0.0)
    assert ereis_live_nyquist(record) is None


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
                          np.full(wl.size, 40000.0), wl, tmp_path, "20261112_eis",
                          potentiostat=Pot(s, point_s=0.02), settings=s)
    done, finished = [], []
    w.segment_done.connect(lambda label, df: done.append(label))
    w.finished.connect(finished.append)
    w.gui_idle.set()
    w.run()
    assert done == ["PITT 0", "PITT 1", "PITT 2"]
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


def test_the_pitt_box_round_trips_its_settings_and_adds_to_the_estimate(window):
    tab = window.parameters_tab
    s = dict(DEFAULT_SETTINGS, pitt_eis_enabled=True, pitt_eis_f_low_hz=0.5,
             pitt_eis_points_per_decade=7, pitt_eis_every_n=3)
    tab.populate_from(s)
    out = {}
    tab.collect_into(out)
    assert out["pitt_eis_enabled"] is True and out["pitt_eis_every_n"] == 3
    assert out["pitt_eis_f_low_hz"] == pytest.approx(0.5)
    assert out["pitt_eis_points_per_decade"] == 7
    assert tab._widgets["pitt_eis_f_low_hz"].isEnabledTo(tab)
    # -0.5 to +0.7 V in 10 mV = 121 steps, every 3rd = 41 sweeps
    assert "41 impedance sweeps" in tab.pitt_estimate.text()
    tab.populate_from(dict(DEFAULT_SETTINGS))
    assert "impedance" not in tab.pitt_estimate.text()
    assert not tab._widgets["pitt_eis_f_low_hz"].isEnabledTo(tab)


def test_the_live_plot_shows_the_sweep_as_a_nyquist_then_the_next_hold(window):
    """2026-11-12, requested at the rig: watch the semicircle grow during a sweep."""
    from types import SimpleNamespace
    from spec_echem.experiment import Segment
    rec = PittRecord()
    rec.echem_step = [0, 0]
    rec.echem_t_in_step = [0.1, 0.2]
    rec.echem_potential = [0.1, 0.1]
    rec.echem_current = [3e-5, 3e-5]
    rec.impedance = [{"step": 0, "frequency_set": 1e4, "zmod": 202.0,
                      "zreal": 202.0, "zimag": -5.0, "ok": True}]
    tab = window.run_tab
    canvas = tab.live_canvas
    tab._worker = SimpleNamespace(potentiostat=None, pitt_record=rec, settings=quick())
    tab._current_segment = Segment("PITT", DATA_TYPE_PITT, 0, 3, 0.1, True)
    try:
        tab._update_live_echem()
        assert canvas.ax.get_xlabel().startswith("Z\u2032")
        assert canvas.ax.get_aspect() == 1.0
        assert list(canvas._live_line.get_xdata()) == [202.0]
        assert list(canvas._live_line.get_ydata()) == [5.0]
        assert "Sweep 1 of" in canvas._live_text.get_text()
        rec.echem_step.append(1)
        rec.echem_t_in_step.append(0.05)
        rec.echem_potential.append(0.2)
        rec.echem_current.append(6e-5)
        tab._update_live_echem()
        assert canvas.ax.get_xlabel().startswith("Time in step")
        assert canvas.ax.get_aspect() == "auto"
        assert list(canvas._live_line.get_ydata()) == [6e-5]
    finally:
        tab._worker = None
        tab._current_segment = None


def test_impossible_settings_show_in_red_before_start(window):
    tab = window.parameters_tab
    tab.populate_from(dict(DEFAULT_SETTINGS, pitt_eis_enabled=True,
                           pitt_eis_f_high_hz=1.0, pitt_eis_f_low_hz=10.0))
    assert "high to low" in tab.pitt_problems_label.text()
    assert tab.pitt_estimate.text() == ""


def test_results_shows_a_swept_steps_nyquist_and_an_unswept_steps_current(
        window, tmp_path):
    pytest.importorskip("h5py")
    s = quick(pitt_eis_every_n=2)
    _steps, path, _record = _run(tmp_path, s)
    for seg in pitt_step_segments(s):
        window.segments_by_label[seg.label] = seg
    window.run_folder = path.parent
    canvas = window.results_tab.echem_canvas
    window.results_tab._plot_echem("PITT 2")
    assert window.results_tab._has_echem
    assert canvas.ax.get_xlabel() == "Z′ (Ω)"
    x = canvas.ax.get_lines()[0].get_xdata()
    assert len(x) == len(ereis.ereis_frequencies(s))
    window.results_tab._plot_echem("PITT 1")
    assert canvas.ax.get_xlabel() == "Time (s)"


def test_start_refuses_impedance_on_the_autolab_and_says_which_box(window, tmp_path,
                                                                   monkeypatch):
    from qtpy.QtCore import QThread
    from qtpy.QtWidgets import QMessageBox
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    window.spec, window.wavelengths = spec, wl
    window.dark, window.ref = np.full(len(wl), 100.0), np.full(len(wl), 5000.0)
    window.settings.update(quick(data_root=str(tmp_path), data_folder="20261112_eis",
                                 potentiostat_mode="autolab"))
    monkeypatch.setattr(window, "collect_settings", lambda: dict(window.settings))
    started, warnings = [], []
    monkeypatch.setattr(QThread, "start", lambda self, *a, **k: started.append(self))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text, *a, **k: warnings.append((title, text)))

    window.run_tab.on_start()

    assert started == []
    assert warnings and warnings[0][0] == "PITT cannot run"
    assert "FRA32M" in warnings[0][1]
    assert "impedance sweep on the Parameters tab" in warnings[0][1]


class _RestoreFails(FakeEreisPotentiostat):
    """The second sweep fails as a failed ie_range restore would: it raises."""

    def ereis_sweep_start(self, step):
        if step.index == 1:
            raise RuntimeError("ie_range not restored")
        return super().ereis_sweep_start(step)


def test_an_error_mid_staircase_still_writes_every_step_before_it(tmp_path):
    h5py = pytest.importorskip("h5py")
    s = quick()
    pot = _RestoreFails(s, point_s=0.02)
    with pytest.raises(RuntimeError, match="ie_range not restored"):
        _run(tmp_path, s, pot)
    assert pot.cell_events[-1][0] == "off"
    path = h5_path(tmp_path / "20261112_eis", DATA_TYPE_PITT)
    with h5py.File(path, "r") as f:
        assert not bool(f.attrs["pitt_completed"])
        assert "ie_range not restored" in f.attrs["pitt_error"]
        table = json.loads(f.attrs["pitt_steps_json"])
        assert [st["index"] for st in table] == [0, 1]
        assert table[0]["sweep_end"] == ereis.SWEEP_COMPLETE
        assert table[1]["sweep_end"] == ereis.SWEEP_ERROR
        assert "impedance" in f["0"]
