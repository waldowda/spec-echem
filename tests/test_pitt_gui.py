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
             "pitt_end_dedope_time_s")


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
              "pitt_end_dedope_time_s": 45.0}
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


def test_the_end_dedope_hold_is_only_editable_when_it_will_run(window):
    tab = window.parameters_tab
    tab._widgets["pitt_end_dedope"].setChecked(False)
    assert not tab._widgets["pitt_end_dedope_time_s"].isEnabled()
    tab._widgets["pitt_end_dedope"].setChecked(True)
    assert tab._widgets["pitt_end_dedope_time_s"].isEnabled()


@pytest.mark.parametrize("mode", ["external", "python", "autolab"])
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
