"""The PITT section on the Parameters tab, and Start refusing a PITT it cannot run."""
import os

import numpy as np
import pytest

pytest.importorskip("qtpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication, QGroupBox, QMessageBox   # noqa: E402

from gui.main_window import MainWindow                              # noqa: E402

PITT_KEYS = ("pitt_enabled", "pitt_start_v", "pitt_stop_v", "pitt_step_mv",
             "pitt_return", "pitt_cutoff_pct", "pitt_min_hold_s", "pitt_max_hold_s",
             "pitt_fast_s", "pitt_slow_interval_s", "pitt_end_dedope",
             "pitt_end_dedope_v", "pitt_end_dedope_time_s")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    import gui.main_window as _mw
    from unittest.mock import patch
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        win = MainWindow()
    win.show()
    yield win
    win.close()


def test_every_pitt_setting_has_a_field_and_round_trips(window):
    tab = window.parameters_tab
    assert all(key in tab._widgets for key in PITT_KEYS)
    chosen = {"pitt_enabled": True, "pitt_start_v": -0.4, "pitt_stop_v": 0.6,
              "pitt_step_mv": 20.0, "pitt_return": True, "pitt_cutoff_pct": 2.5,
              "pitt_min_hold_s": 3.0, "pitt_max_hold_s": 90.0, "pitt_fast_s": 4.0,
              "pitt_slow_interval_s": 2.0, "pitt_end_dedope": True,
              "pitt_end_dedope_v": -0.6, "pitt_end_dedope_time_s": 45.0}
    tab.populate_from(chosen)
    out = {}
    tab.collect_into(out)
    for key, value in chosen.items():
        assert out[key] == pytest.approx(value), key


def test_the_section_says_it_is_python_mode_only_and_hdf5_only(window):
    group = next(b for b in window.parameters_tab.findChildren(QGroupBox)
                 if b.title().startswith("PITT"))
    assert "Python mode only" in group.title()
    assert "External = reference" not in group.title()
    from qtpy.QtWidgets import QLabel
    assert any("HDF5 only" in lab.text() for lab in group.findChildren(QLabel))


def test_the_estimate_counts_steps_and_brackets_the_time(window):
    tab = window.parameters_tab
    tab.populate_from({"pitt_start_v": 0.0, "pitt_stop_v": 0.2, "pitt_step_mv": 100.0,
                       "pitt_min_hold_s": 60.0, "pitt_max_hold_s": 120.0,
                       "pitt_return": False, "pitt_end_dedope": False})
    text = tab.pitt_estimate.text()
    assert text.startswith("3 steps") and "3 min" in text and "6 min" in text
    assert "⚠" not in text


def test_stepping_back_down_warns_with_what_it_adds(window):
    tab = window.parameters_tab
    tab.populate_from({"pitt_start_v": 0.0, "pitt_stop_v": 0.2, "pitt_step_mv": 100.0,
                       "pitt_min_hold_s": 60.0, "pitt_max_hold_s": 120.0,
                       "pitt_end_dedope": False})
    tab._widgets["pitt_return"].setChecked(True)
    text = tab.pitt_estimate.text()
    assert "⚠" in text and "5 steps" in text
    assert "adds 2 min to 4 min" in text                 # the two extra steps


def test_impossible_settings_are_said_live_not_at_start(window):
    tab = window.parameters_tab
    tab._widgets["pitt_max_hold_s"].setValue(10.0)
    tab._widgets["pitt_min_hold_s"].setValue(50.0)
    assert "longer than its maximum" in tab.pitt_problems_label.text()
    assert tab.pitt_problems_label.isVisibleTo(tab)
    tab._widgets["pitt_min_hold_s"].setValue(5.0)
    assert not tab.pitt_problems_label.isVisibleTo(tab)


def test_the_end_dedope_fields_are_only_editable_when_it_will_run(window):
    tab = window.parameters_tab
    tab._widgets["pitt_end_dedope"].setChecked(False)
    for key in ("pitt_end_dedope_v", "pitt_end_dedope_time_s"):
        assert not tab._widgets[key].isEnabled(), key
    tab._widgets["pitt_end_dedope"].setChecked(True)
    for key in ("pitt_end_dedope_v", "pitt_end_dedope_time_s"):
        assert tab._widgets[key].isEnabled(), key


def test_the_end_dedope_potential_defaults_to_minus_half_a_volt(window):
    tab = window.parameters_tab
    tab.populate_from(dict(window.settings))
    assert tab._widgets["pitt_end_dedope_v"].value() == -0.5


@pytest.mark.parametrize("mode", ["external", "python"])
def test_start_refuses_a_pitt_this_potentiostat_cannot_run(window, tmp_path,
                                                          monkeypatch, mode):
    """Refused at Start with the reason, never discovered mid-staircase -- and the
    run does not begin."""
    from qtpy.QtCore import QThread
    from spec_echem.fakes import FakeSpectrometer
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    window.spec, window.wavelengths = spec, wl
    window.dark, window.ref = np.full(len(wl), 100.0), np.full(len(wl), 5000.0)
    window.settings.update(data_root=str(tmp_path), data_folder="20261004_pitt",
                           potentiostat_mode=mode, pitt_enabled=True)
    monkeypatch.setattr(window, "collect_settings", lambda: dict(window.settings))
    started, warnings = [], []
    monkeypatch.setattr(QThread, "start", lambda self, *a, **k: started.append(self))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text, *a, **k: warnings.append((title, text)))

    window.run_tab.on_start()

    assert started == []
    assert warnings and warnings[0][0] == "PITT cannot run"
    assert "cannot run a PITT yet" in warnings[0][1]
    assert "Untick PITT" in warnings[0][1]


def test_start_is_unaffected_when_the_pitt_is_not_ticked(window, tmp_path, monkeypatch):
    from qtpy.QtCore import QThread
    from spec_echem.fakes import FakeSpectrometer
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    window.spec, window.wavelengths = spec, wl
    window.dark, window.ref = np.full(len(wl), 100.0), np.full(len(wl), 5000.0)
    window.settings.update(data_root=str(tmp_path), data_folder="20261004_plain",
                           potentiostat_mode="external", pitt_enabled=False)
    monkeypatch.setattr(window, "collect_settings", lambda: dict(window.settings))
    started = []
    monkeypatch.setattr(QThread, "start", lambda self, *a, **k: started.append(self))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Ok)
    window.run_tab.on_start()
    assert len(started) == 1


def test_a_text_run_still_shows_its_pitt_steps(window, monkeypatch):
    """A PITT exists ONLY in HDF5. When Load Run prefers the text files -- data_format
    'ascii', or a run written that way -- the ladder comes from text, and the PITT
    must still come from its .h5 rather than silently vanish."""
    from pathlib import Path
    import gui.tabs.results_tab as rt
    from spec_echem.data import (DATA_TYPE_DEDOPING, DATA_TYPE_DOPING,
                                 DATA_TYPE_PITT)
    txt, h5 = Path("spectra(0).txt"), Path("run_doping.h5")
    pitt = Path("run_pitt.h5")
    monkeypatch.setattr(rt, "discover_run_segments", lambda folder: [
        ("Doping 0", DATA_TYPE_DOPING, 0, txt),
        ("Dedoping 0", DATA_TYPE_DEDOPING, 0, Path("dedopingspectra(0).txt"))])
    monkeypatch.setattr(rt, "discover_run_h5", lambda folder: [
        ("Doping 0", DATA_TYPE_DOPING, 0, h5),
        ("Dedoping 0", DATA_TYPE_DEDOPING, 0, Path("run_dedoping.h5")),
        ("PITT 0", DATA_TYPE_PITT, 0, pitt), ("PITT 1", DATA_TYPE_PITT, 1, pitt)])
    window.settings["data_format"] = "ascii"

    segs, source = window.results_tab._discover(Path("."))

    assert source == "text"
    assert [s[0] for s in segs] == ["Doping 0", "Dedoping 0", "PITT 0", "PITT 1"]
    assert segs[0][3] == txt                            # the ladder stays text
    assert segs[2][3] == pitt                           # the PITT comes from .h5


def test_a_saved_pitt_loads_and_every_step_is_labelled_with_its_potential(
        window, tmp_path):
    """End to end: a staircase written by the real writer, opened the way Load Run
    opens a folder, and each step named in the dropdown with its setpoint."""
    from spec_echem.acquisition import acquire_pitt
    from spec_echem.data import write_pitt_h5
    from spec_echem.fakes import FakePittPotentiostat, FakeSpectrometer
    from spec_echem.pitt import pitt_plan
    from spec_echem.settings import DEFAULT_SETTINGS
    from gui.segment_labels import segment_display

    s = dict(DEFAULT_SETTINGS, pitt_enabled=True, pitt_start_v=0.1, pitt_stop_v=0.3,
             pitt_step_mv=100.0, pitt_min_hold_s=0.1, pitt_max_hold_s=0.2,
             pitt_fast_s=0.1, pitt_slow_interval_s=0.1, chrono_delta_time=0.05)
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]
    record = acquire_pitt(spec, FakePittPotentiostat(r_leak=3210.0), pitt_plan(s), s)
    write_pitt_h5(record, np.full(wl.size, 100.0), np.full(wl.size, 40000.0), wl,
                  tmp_path, "20261004_pitt", settings=s)
    folder = tmp_path / "20261004_pitt"

    tab = window.results_tab
    segs, source = tab._discover(folder)
    results, by_label, errors, _stopped = tab._read_segments(segs)
    assert errors == []
    assert list(results) == ["PITT 0", "PITT 1", "PITT 2"]

    window.results = results
    window.segments_by_label = by_label
    window.run_folder = folder
    window.loaded_run_settings = s
    window._potential_cache.clear()
    labels = [segment_display(window, label) for label in results]
    assert labels == ["PITT 0  (+0.10 V)", "PITT 1  (+0.20 V)", "PITT 2  (+0.30 V)"]


def test_a_pitt_steps_current_is_drawn_from_its_hdf5(window, tmp_path):
    """A PITT has no text echem file, ever. Its current is in the step's .h5 group,
    and the Results tab draws it from there -- the same fallback an 'HDF5 only' run
    of the ordinary segments now gets, which had no echem plot at all before."""
    from spec_echem.acquisition import acquire_pitt
    from spec_echem.data import (DATA_TYPE_PITT, echem_txt_path,
                                 read_segment_echem_h5, write_pitt_h5, h5_path)
    from spec_echem.experiment import Segment
    from spec_echem.fakes import FakePittPotentiostat, FakeSpectrometer
    from spec_echem.pitt import pitt_plan
    from spec_echem.settings import DEFAULT_SETTINGS

    s = dict(DEFAULT_SETTINGS, pitt_enabled=True, pitt_start_v=0.1, pitt_stop_v=0.2,
             pitt_step_mv=100.0, pitt_min_hold_s=0.1, pitt_max_hold_s=0.2,
             pitt_fast_s=0.1, pitt_slow_interval_s=0.1, chrono_delta_time=0.05)
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]
    record = acquire_pitt(spec, FakePittPotentiostat(r_leak=3210.0), pitt_plan(s), s)
    write_pitt_h5(record, np.full(wl.size, 100.0), np.full(wl.size, 40000.0), wl,
                  tmp_path, "20261004_pitt", settings=s)
    folder = tmp_path / "20261004_pitt"

    assert echem_txt_path(folder, DATA_TYPE_PITT, 1) is None      # never text
    df = read_segment_echem_h5(h5_path(folder, DATA_TYPE_PITT), 1)
    assert list(df.columns) == ["Time (s)", "Corrected time (s)",
                                "WE(1).Potential (V)", "WE(1).Current (A)", "Index"]
    # Still decaying toward the leak's 0.2 V / 3210 Ohm: the fake's RC (tau = 1 s)
    # has had only the 0.2 s hold.
    current = df["WE(1).Current (A)"].to_numpy()
    assert np.all(current > 0.2 / 3210.0) and np.all(np.diff(current) < 0)

    window.run_folder = folder
    window.segments_by_label = {"PITT 1": Segment("PITT 1", DATA_TYPE_PITT, 1, 0,
                                                  0.05, False)}
    tab = window.results_tab
    tab._has_echem = False
    tab._plot_echem("PITT 1")
    assert tab._has_echem
