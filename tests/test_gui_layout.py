"""
Layout regression tests for the GUI.

The first automated coverage of gui/. It exists because of a real bug: lengthening
the spectrometer connect-failure message widened the whole application window past
its half-column layout. A QLabel in a layout asks for its full text width and the
layout grants it — it does not clip — so any label holding text of uncontrolled
length (hardware error strings) has to wrap, and has to be unable to drive width.

Headless: forces the offscreen Qt platform, so it runs with no display.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtWidgets import QApplication            # noqa: E402
from gui.main_window import MainWindow             # noqa: E402

# The message a student sees with the spectrometer unplugged — the actual case.
LONG_ERROR = ("No Avantes spectrometer found. Check the USB cable, and close "
              "AvaSoft or any other program using the spectrometer.")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    # Isolate from this machine's config/bench.ini — a layout/default test must not
    # depend on whether the rig it runs on has (say) potentiostat_mode = autolab set.
    import gui.main_window as _mw
    from unittest.mock import patch
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        win = MainWindow()
    win.show()          # size hints aren't computed for a window that never laid out
    yield win
    win.close()


def _connection_group_width(tab):
    """Minimum width the Spectrometer Connection group demands of the layout.

    activate() first: a size hint is cached until the layout is told its contents
    changed, so reading it straight after setText returns the stale value.
    """
    group = tab.spec_status.parentWidget()
    group.layout().activate()
    return group.minimumSizeHint().width()


def test_long_connect_error_does_not_widen_the_layout(window):
    tab = window.instrument_tab
    before = _connection_group_width(tab)

    tab.spec_status.setText("● Connect failed")
    tab.spec_detail.setText(LONG_ERROR)

    # Some slack for font differences between machines; the bug was +900 px.
    assert _connection_group_width(tab) <= before + 20


def test_error_text_inline_would_widen_the_layout(window):
    """Guards the test above: prove the failure mode is real, so a future refactor
    that puts the message back inline can't pass by accident."""
    tab = window.instrument_tab
    before = _connection_group_width(tab)
    tab.spec_status.setText(f"● Connect failed: {LONG_ERROR}")
    assert _connection_group_width(tab) > before + 100


def test_detail_labels_wrap_and_cannot_drive_width(window):
    from qtpy.QtWidgets import QSizePolicy
    tab = window.instrument_tab
    for label in (tab.spec_detail, tab.pstat_detail):
        assert label.wordWrap()
        assert label.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored


def test_connect_failure_message_is_still_shown_somewhere(window):
    """Not widening the window is worthless if the fix hid the message — that was
    the original complaint (a failed connect showed nothing the student could see)."""
    tab = window.instrument_tab
    tab.simulated_check.setChecked(False)
    tab.on_connect()   # no hardware in CI -> takes the failure path

    if tab.win.spec is not None:
        pytest.skip("a real spectrometer is attached — the no-hardware "
                    "failure path can't be exercised on this machine")
    assert "failed" in tab.spec_status.text().lower()
    assert tab.spec_detail.text()          # the reason is on screen, not just logged


# --- wavelength spin boxes track the connected spectrometer (options A + C) ----

def test_window_fits_only_rejects_a_crop_for_another_detector():
    from gui.tabs.instrument_tab import InstrumentTab
    f = InstrumentTab._window_fits
    assert f(400.0, 1050.0, 380.0, 1100.0)      # normal crop, well inside
    assert f(300.0, 1100.0, 410.0, 1124.0)      # edges over the floor, still >50% overlap
    assert f(395.0, 1105.0, 410.0, 1124.0)      # small edge mismatch still fits
    assert not f(1200.0, 1300.0, 380.0, 1100.0)  # disjoint -> a different spectrometer
    assert not f(200.0, 405.0, 410.0, 1124.0)    # only a sliver overlaps


def test_connect_clamps_wavelength_spinboxes_to_the_spectrometer_span(window):
    """On connect the wl spin boxes must be bounded by what the spectrometer
    actually reports, not the old 0–5000 nm free-for-all."""
    tab = window.instrument_tab
    tab.simulated_check.setChecked(True)
    tab.on_connect()                       # FakeSpectrometer: 380–1100 nm

    full = tab._full_wl
    assert tab.wl_min_spin.minimum() == pytest.approx(float(full[0]), abs=0.5)
    assert tab.wl_max_spin.maximum() == pytest.approx(float(full[-1]), abs=0.5)
    assert "1100" in tab.wl_status.text()   # the real span is surfaced, not hidden


# --- Potentiostat mode selection (External / Python / Autolab) ---------------
# The External + Python paths are the working Gamry rig. Every test here exists to
# make sure adding a third mode left those two exactly as they were.

def _tab(window):
    return window.instrument_tab


def test_external_is_the_default_mode(window):
    """External is the only mode that works with no vendor stack installed, and it is
    the proven one. It must be what a fresh launch selects."""
    tab = _tab(window)
    assert tab.pstat_external_radio.isChecked()
    assert not tab.pstat_python_radio.isChecked()
    assert not tab.pstat_autolab_radio.isChecked()


def test_all_three_modes_round_trip_through_settings(window):
    tab = _tab(window)
    for radio, expected in ((tab.pstat_external_radio, "external"),
                            (tab.pstat_python_radio, "python"),
                            (tab.pstat_autolab_radio, "autolab")):
        radio.setChecked(True)
        settings = {}
        tab.collect_into(settings)
        assert settings["potentiostat_mode"] == expected


def test_a_saved_mode_the_machine_cannot_honour_falls_back_to_external(window):
    """The safety property: loading a settings file that says "autolab" on the Gamry
    rig (no pythonnet) must leave External selected, not select a radio whose driver
    would raise at Start."""
    tab = _tab(window)
    from spec_echem.settings import DEFAULT_SETTINGS

    tab.pstat_autolab_radio.setEnabled(False)          # as on a box without pythonnet
    tab.populate_from(dict(DEFAULT_SETTINGS, potentiostat_mode="autolab"))
    assert tab.pstat_external_radio.isChecked()

    tab.pstat_python_radio.setEnabled(False)           # and the Gamry equivalent
    tab.populate_from(dict(DEFAULT_SETTINGS, potentiostat_mode="python"))
    assert tab.pstat_external_radio.isChecked()


def test_dta_checkbox_belongs_to_gamry_python_mode_only(window):
    """.DTA is a Gamry format written by toolkitpy. External writes its own through
    Framework and the Autolab has none, so the box must be live in exactly one mode."""
    tab = _tab(window)
    if not tab.pstat_python_radio.isEnabled():
        pytest.skip("toolkitpy not available in this environment")
    tab.pstat_python_radio.setChecked(True)
    assert tab.save_dta_check.isEnabled()
    tab.pstat_autolab_radio.setChecked(True)
    assert not tab.save_dta_check.isEnabled()
    tab.pstat_external_radio.setChecked(True)
    assert not tab.save_dta_check.isEnabled()


def test_an_unavailable_mode_is_disabled_and_says_why(window):
    """A greyed radio with no reason reads as a bug. Whichever vendor stack is missing
    here, its radio must be off AND its label must name what is missing."""
    tab = _tab(window)
    for radio, needle in ((tab.pstat_python_radio, "toolkitpy"),
                          (tab.pstat_autolab_radio, "pythonnet")):
        if not radio.isEnabled():
            assert needle in radio.text()


def test_connect_button_follows_the_selected_mode(window):
    """External has nothing to connect to from here — the Gamry runs standalone."""
    tab = _tab(window)
    tab.pstat_external_radio.setChecked(True)
    assert not tab.pstat_connect_btn.isEnabled()


def test_spectrometer_detail_names_the_detector(window):
    """A serial alone doesn't distinguish one 2048-pixel Avantes from another; the
    pixel count and calibrated span are what you can check against the bench."""
    tab = _tab(window)
    tab.simulated_check.setChecked(True)                # no SDK on a dev box
    tab.on_connect()
    detail = tab.spec_detail.text()
    assert "serial" in detail
    assert "px" in detail and "nm" in detail


# --- Start actually runs -----------------------------------------------------
# A smoke test: build the real window, hand it fakes, click Start, and assert it
# does not blow up. It checks nothing about the science — only that on_start()
# EXECUTES. That is worth a test on its own because on_start had no coverage and a
# refactor once deleted a variable it still referenced, so Start raised NameError
# in every potentiostat mode and nothing noticed until someone clicked it at a rig.

@pytest.fixture
def ready_window(window, tmp_path, monkeypatch):
    """A window that would really start a run — fake spectrometer, calibration in
    hand, a writable data folder — with the worker thread stubbed so nothing
    acquires. Everything up to and including building the potentiostat runs for real."""
    import numpy as np
    from qtpy.QtCore import QThread
    from spec_echem.fakes import FakeSpectrometer

    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    window.spec = spec
    window.wavelengths = wl
    window.dark = np.full(len(wl), 100.0)
    window.ref = np.full(len(wl), 5000.0)
    window.settings["data_root"] = str(tmp_path)
    window.settings["data_folder"] = "20260903_smoke"

    started = []
    monkeypatch.setattr(QThread, "start", lambda self, *a, **k: started.append(self))
    return window, started


@pytest.mark.parametrize("mode", ["external", "python", "autolab"])
def test_start_executes_in_every_mode(ready_window, monkeypatch, mode):
    """The regression guard. Every mode must get through on_start() and hand a
    worker to a thread — including the modes whose vendor stack is absent here,
    since the driver is only constructed, never opened."""
    window, started = ready_window
    tab = window.run_tab
    window.settings["potentiostat_mode"] = mode
    monkeypatch.setattr(window, "collect_settings", lambda: dict(window.settings))
    if mode != "external":
        # Stand in for the vendor driver: on a dev box neither stack imports, and
        # this test is about on_start's own code, not about the drivers.
        monkeypatch.setattr("gui.tabs.run_tab.make_potentiostat",
                            lambda s: object())

    tab.on_start()

    assert started, f"Start did not reach thread.start() in {mode!r} mode"
    assert tab._worker is not None


def test_start_refuses_without_a_spectrometer(window, monkeypatch):
    """The guard that must still fire — Start with nothing connected should warn,
    not crash, and must not spin up a worker."""
    from qtpy.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window.spec = None

    window.run_tab.on_start()

    assert getattr(window.run_tab, "_worker", None) is None
