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


def test_the_cadence_note_says_when_a_spectrum_does_not_fit_the_step(window):
    """Advisory label: the spectrum's cost is set on this tab, the step it must fit
    inside on the Parameters tab, so neither tab shows the collision on its own."""
    tab = window.instrument_tab
    tab.integration_spin.setValue(2.6439)
    tab.averages_spin.setValue(200)          # ~559 ms against a 100 ms CV step
    tab._update_cadence_note()
    text = tab.cadence_note.text()
    assert "Does NOT fit" in text
    assert "averages would fit" in text      # it suggests, rather than only scolding


def test_the_cadence_note_is_quiet_when_there_is_room(window):
    """The working Gamry rig's settings: 0.088 ms x 200 + overhead = ~48 ms in a
    100 ms step. A warning that fires here would be crying wolf on a good rig."""
    tab = window.instrument_tab
    tab.integration_spin.setValue(0.088)
    tab.averages_spin.setValue(200)
    tab._update_cadence_note()
    assert "Fits" in tab.cadence_note.text()
    assert "NOT" not in tab.cadence_note.text()


def test_the_cadence_note_cannot_take_the_tab_down(window, monkeypatch):
    """It is a convenience. If anything behind it raises, it must go quiet — never
    propagate into the Instrument tab's construction or its signal handlers."""
    import gui.tabs.instrument_tab as mod
    monkeypatch.setattr(mod, "spectrum_cost_seconds",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tab = window.instrument_tab
    tab._update_cadence_note()               # must not raise
    assert tab.cadence_note.text() == "—"


def test_the_cadence_note_wraps(window):
    """Same rule as every other uncontrolled-length label here: it must not be able
    to drive the window's width."""
    assert window.instrument_tab.cadence_note.wordWrap() is True


# --- "did my click land?" ----------------------------------------------------
# Both connect probes block the GUI THREAD for seconds. Qt cannot repaint until the
# event loop runs again, so a status label alone shows the user nothing and they
# click again — which put two full connect cycles into the 2026-09-11 crash log, on
# a USB stack that has twice failed under repeated open/close.

def test_a_slow_connect_disables_its_own_button(window, monkeypatch):
    """The button must be dead BEFORE the blocking call, not after it returns — a
    click on a disabled button is discarded rather than queued."""
    from gui.tabs import instrument_tab

    tab = window.instrument_tab
    seen = {}

    def slow_probe(_settings):
        # Runs while the GUI is "blocked": record what the user would actually see.
        seen["enabled"] = tab.pstat_connect_btn.isEnabled()
        seen["text"] = tab.pstat_status.text()
        raise RuntimeError("no instrument here")

    monkeypatch.setattr(instrument_tab, "autolab_identity", slow_probe)
    tab.pstat_autolab_radio.setChecked(True)
    tab.pstat_connect_btn.setEnabled(True)     # the state a user clicks from
    tab.on_connect_pstat()

    assert seen["enabled"] is False            # cannot be clicked again mid-probe
    assert "Connecting" in seen["text"]        # and it says so
    # Restored to what it WAS, not force-enabled: the tab disables this button by
    # policy in some modes, and a connect attempt must not override that.
    assert tab.pstat_connect_btn.isEnabled() is True


def test_the_spectrometer_button_behaves_the_same(window, monkeypatch):
    from gui.tabs import instrument_tab

    tab = window.instrument_tab
    seen = {}

    class SlowSpec:
        def init(self):
            seen["enabled"] = tab.connect_btn.isEnabled()
            seen["text"] = tab.spec_status.text()
            raise RuntimeError("no spectrometer here")

    monkeypatch.setattr(instrument_tab, "AvantesSpectrometer", SlowSpec)
    tab.simulated_check.setChecked(False)
    tab.connect_btn.setEnabled(True)
    tab.on_connect()

    assert seen["enabled"] is False
    assert "Connecting" in seen["text"]
    assert tab.connect_btn.isEnabled() is True


def test_a_button_disabled_by_policy_stays_disabled(window, monkeypatch):
    """_click_landed restores the PRIOR state. A connect attempt must not hand back
    a button the tab had deliberately disabled."""
    from gui.tabs import instrument_tab

    tab = window.instrument_tab
    monkeypatch.setattr(instrument_tab, "autolab_identity",
                        lambda s: (_ for _ in ()).throw(RuntimeError("nope")))
    tab.pstat_autolab_radio.setChecked(True)
    tab.pstat_connect_btn.setEnabled(False)
    tab.on_connect_pstat()
    assert tab.pstat_connect_btn.isEnabled() is False


def test_every_potential_field_names_what_it_drives(window):
    """A bare "Potential:" cost two film runs on 2026-09-11: -0.5 V meant for dedoping
    went into the pre-dedoping field, so the films got one hard reduction up front and
    then dedoped at 0.0 V for the whole ladder. The settings and the data were both
    faithful — the label was the defect. Every potential field must say which one it is.
    """
    from qtpy.QtWidgets import QFormLayout

    tab = window.parameters_tab
    keys = [k for k in tab._widgets if k.endswith("_potential")
            or k.startswith("doping_potential")]
    assert keys, "no potential fields found — has the tab been restructured?"

    for key in keys:
        w = tab._widgets[key]
        form = w.parentWidget().layout()
        if not isinstance(form, QFormLayout):
            continue
        label = form.labelForField(w)
        assert label is not None, f"{key} has no label"
        text = label.text().lower()
        # The label has to distinguish this field from its siblings: a bare
        # "Potential" does not.
        assert text.strip().rstrip(":") not in ("potential", "potential (vs vref)"), (
            f"{key} is labelled {label.text()!r} — too generic to tell apart from the "
            f"other potential fields")


# --- the current range is a per-SAMPLE setting, so it belongs in the GUI ------
# It was bench.ini-only until 2026-09-11, the same gap autolab_pulse_delay_s has:
# a value that changes with the sample but could only be changed by editing a file.

def test_the_current_range_is_selectable_in_the_gui(window):
    from spec_echem.potentiostat import AUTOLAB_CURRENT_RANGES

    tab = window.parameters_tab
    w = tab._widgets["autolab_current_range"]
    values = [w.itemData(i) for i in range(w.count())]

    assert values[0] == "", "first entry must mean 'leave the instrument's own'"
    assert "CR10_1mA" in values and "CR09_10mA" in values
    # every SDK member offered, none invented
    assert set(values) == {""} | {v for v, _ in AUTOLAB_CURRENT_RANGES}


def test_the_range_survives_a_settings_round_trip(window):
    """It stores the enum MEMBER, not the human label — the driver looks the member
    up on the SDK enum, so a label would fail at the instrument."""
    tab = window.parameters_tab
    tab.populate_from({"autolab_current_range": "CR11_100uA"})
    assert tab._widgets["autolab_current_range"].currentData() == "CR11_100uA"

    out = {}
    tab.collect_into(out)
    assert out["autolab_current_range"] == "CR11_100uA"


def test_an_unknown_range_falls_back_to_leave_alone(window):
    """A settings file naming a range this build does not know must not silently
    select some other range."""
    tab = window.parameters_tab
    tab.populate_from({"autolab_current_range": "CR99_nonsense"})
    assert tab._widgets["autolab_current_range"].currentData() == ""


# --- the Results dropdown must not hide the end of a run ---------------------
# 2026-09-11: a 0.2-0.7 V ladder in 0.1 V steps makes 14 reviewable segments, and
# Qt's default maxVisibleItems is 10 — so Doping/Dedoping 4 and 5 were invisible and
# the run looked truncated. The files were all on disk.

def test_the_segment_dropdown_holds_a_full_ladder(window, monkeypatch):
    import pandas as pd
    from gui.tabs.results_tab import SEGMENT_COMBO_VISIBLE

    # This is a dropdown test; plotting is exercised elsewhere.
    monkeypatch.setattr(window.results_tab.canvas, "show_absorbance",
                        lambda *a, **k: None)

    labels = ["CV", "Pre-dedoping"]
    for n in range(6):
        labels += [f"Doping {n}", f"Dedoping {n}"]
    assert len(labels) == 14                       # the run that exposed this

    df = pd.DataFrame({"Wavelength (nm)": [500.0], "Absorbance": [0.1]})
    window.results = {k: df for k in labels}
    window.results_tab.refresh_segments()

    combo = window.results_tab.segment_combo
    assert combo.count() == 14                     # every segment reviewable...
    assert SEGMENT_COMBO_VISIBLE >= 14             # ...and none of them below the fold
    shown = [combo.itemText(i) for i in range(combo.count())]
    assert "Doping 5" in shown and "Dedoping 5" in shown


def test_a_longer_ladder_still_scrolls_rather_than_vanishing(window, monkeypatch):
    """Beyond the visible limit the popup must scroll, not truncate — the count is
    what matters, not how many fit on screen."""
    import pandas as pd

    monkeypatch.setattr(window.results_tab.canvas, "show_absorbance",
                        lambda *a, **k: None)

    df = pd.DataFrame({"Wavelength (nm)": [500.0], "Absorbance": [0.1]})
    labels = ["CV"] + [f"Doping {n}" for n in range(40)]
    window.results = {k: df for k in labels}
    window.results_tab.refresh_segments()
    assert window.results_tab.segment_combo.count() == 41


# --- Tab 5: Analysis ---------------------------------------------------------
# Fitting lives in its own tab because it is post-run work with model, window and
# re-run controls, while tab 4 is the live glance. The maths is tested in
# test_analysis.py; these check the tab drives it and reports failure honestly.

@pytest.fixture
def analysis_window(window, tmp_path):
    """A window holding one synthetic doping segment — an absorbance matrix whose
    polaron band grows, and a matching chrono file with a capacitive spike."""
    import numpy as np
    import pandas as pd
    from spec_echem.data import DATA_TYPE_DOPING, write_echem_file, EchemData
    from spec_echem.experiment import Segment

    wl = np.linspace(400.0, 1100.0, 120)
    t = np.linspace(0.0, 20.0, 120)
    polaron = np.exp(-0.5 * ((wl - 900.0) / 60.0) ** 2)
    pi = np.exp(-0.5 * ((wl - 550.0) / 40.0) ** 2)
    # Both bands evolve with tau = 4 s. The OD levels are the physical ones: on
    # doping the polaron band starts near zero and GROWS, while pi-pi* starts high
    # (~0.8 OD) and bleaches as polarons build. An earlier version of this fixture
    # sat both bands on a flat 1.0 pedestal, which fits identically -- every model
    # here has an additive offset that absorbs a baseline -- but is not what a film
    # does, and would mislead anyone reading it as an example of real data.
    frac = 1.0 - np.exp(-t / 4.0)
    a = (0.02
         + np.outer(pi, 0.85 - 0.55 * frac)
         + np.outer(polaron, 0.50 * frac))
    window.results = {"Doping 0": pd.DataFrame(a, index=wl, columns=t)}
    window.segments_by_label = {
        "Doping 0": Segment("Doping 0", DATA_TYPE_DOPING, 0, 120, 0.1, True)}

    current = 3.0e-5 * np.exp(-t / 4.0) + 6.0e-4 * np.exp(-t / 0.05)
    write_echem_file(EchemData(time=t, potential=np.full(120, 0.3), current=current),
                     DATA_TYPE_DOPING, 0, tmp_path, "run")
    window.run_folder = tmp_path / "run"
    window.analysis_tab.refresh_segments()
    return window


def test_the_cv_is_not_offered_for_transient_fitting(analysis_window):
    """A CV is a sweep, not a step — there is no transient to fit, so offering it
    would only produce a confident-looking meaningless number."""
    import pandas as pd
    import numpy as np
    from spec_echem.data import DATA_TYPE_CV
    from spec_echem.experiment import Segment

    w = analysis_window
    w.results["CV"] = pd.DataFrame(np.zeros((3, 3)))
    w.segments_by_label["CV"] = Segment("CV", DATA_TYPE_CV, 0, 3, 0.1, True)
    w.analysis_tab.refresh_segments()

    labels = [w.analysis_tab.segment_combo.itemText(i)
              for i in range(w.analysis_tab.segment_combo.count())]
    assert "Doping 0" in labels and "CV" not in labels


def test_the_window_start_defaults_to_the_current_peak(analysis_window):
    """Where the capacitive spike ends — computed from the data, not guessed."""
    tab = analysis_window.analysis_tab
    tab.on_segment_changed()
    assert tab.auto_start_check.isChecked()
    assert tab.start_spin.value() == pytest.approx(0.0, abs=0.3)   # spike is at t=0
    assert tab.stop_spin.value() == 0.0            # 0 = "end of segment"
    assert tab.stop_spin.specialValueText() == "end of segment"


def test_fitting_a_segment_fills_all_three_traces(analysis_window):
    tab = analysis_window.analysis_tab
    tab.on_fit_segment()

    fits = tab._fits["Doping 0"]
    assert set(fits) == {"absorbance", "current", "charge"}
    assert fits["absorbance"].ok
    # the synthetic absorbance rises with tau = 4 s at the auto-picked polaron band
    assert fits["absorbance"].tau == pytest.approx(4.0, rel=0.25)


def test_the_auto_wavelength_lands_on_the_growing_band(analysis_window):
    """Not the bleach, which is what |dA| would have chosen."""
    tab = analysis_window.analysis_tab
    tab._absorbance_trace("Doping 0")
    assert 850 < tab._wavelength < 950


def test_a_failed_fit_shows_its_reason_instead_of_a_number(analysis_window):
    """curve_fit returns confident nonsense rather than raising, so the table must
    not present it as a measurement."""
    import numpy as np
    from spec_echem.analysis import FitResult

    tab = analysis_window.analysis_tab
    tab._show_fits({"absorbance": FitResult("exp", reason="uncertainty too large")})
    assert "failed" in tab.table.item(0, 1).text()
    assert tab.table.item(0, 2).text() == ""       # no tau, no beta


def test_the_ladder_plot_waits_for_a_fit(analysis_window):
    tab = analysis_window.analysis_tab
    tab._draw_ladder()          # nothing fitted yet — must not raise
    tab.on_fit_segment()
    tab.ratio_check.setChecked(True)    # and the toggle must not raise either
    tab.ratio_check.setChecked(False)


# --- Tab 4: the live views ---------------------------------------------------
# Spectra stays the default; kinetics and modulation are what make this tab useful
# DURING a run rather than only after it.

def _ladder_window(window, tmp_path, n_steps=4):
    """A window holding several completed doping segments, as mid-run."""
    import numpy as np
    import pandas as pd
    from spec_echem.data import DATA_TYPE_DOPING
    from spec_echem.experiment import Segment

    wl = np.linspace(400.0, 1100.0, 80)
    t = np.linspace(0.0, 10.0, 40)
    polaron = np.exp(-0.5 * ((wl - 900.0) / 60.0) ** 2)
    pi = np.exp(-0.5 * ((wl - 550.0) / 40.0) ** 2)
    results, segments = {}, {}
    for n in range(n_steps):
        grow = (1.0 - np.exp(-t / 3.0)) * (0.2 + 0.2 * n)   # more doping each step
        a = 1.0 - 1.2 * np.outer(pi, grow) + 0.5 * np.outer(polaron, grow)
        label = f"Doping {n}"
        results[label] = pd.DataFrame(a, index=wl, columns=t)
        segments[label] = Segment(label, DATA_TYPE_DOPING, n, 40, 0.25, True)
    window.results = results
    window.segments_by_label = segments
    window.run_folder = tmp_path
    window.settings.update({"doping_potential_start": 0.2, "doping_potential_step": 0.1,
                            "doping_potential_end": 0.5})
    window.results_tab.refresh_segments()
    return window


def test_spectra_remains_the_default_view(window, tmp_path):
    w = _ladder_window(window, tmp_path)
    assert w.results_tab.view_combo.currentData() == "spectra"


def test_the_modulation_view_builds_one_point_per_step(window, tmp_path):
    """The plot that earns the tab: it must work from partial runs, because catching a
    dying film mid-ladder is the entire point."""
    w = _ladder_window(window, tmp_path, n_steps=3)
    tab = w.results_tab
    tab.view_combo.setCurrentIndex(2)          # modulation
    tab.on_segment_changed()

    line = tab.canvas.ax.get_lines()[0]
    assert len(line.get_xdata()) == 3          # one point per completed step
    assert list(line.get_xdata()) == sorted(line.get_xdata())   # ordered by potential


def test_the_modulation_view_says_so_when_there_is_nothing_yet(window, tmp_path):
    w = _ladder_window(window, tmp_path, n_steps=0)
    tab = w.results_tab
    tab.view_combo.setCurrentIndex(2)
    tab._plot_modulation()                      # must explain, not raise or go blank
    assert tab.canvas.ax.texts


def test_the_kinetics_view_follows_the_growing_band(window, tmp_path):
    """Automatic wavelength must give the polaron, not the larger pi-pi* bleach."""
    w = _ladder_window(window, tmp_path)
    tab = w.results_tab
    tab.view_combo.setCurrentIndex(1)          # kinetics
    tab.on_segment_changed()

    label = tab.canvas.ax.get_lines()[0].get_label()
    assert 850 < float(label.split()[0]) < 950


def test_a_manual_wavelength_overrides_the_automatic_one(window, tmp_path):
    w = _ladder_window(window, tmp_path)
    tab = w.results_tab
    tab.view_combo.setCurrentIndex(1)
    tab.analysis_wl.setValue(550.0)            # the pi-pi* bleach
    tab.on_segment_changed()

    label = tab.canvas.ax.get_lines()[0].get_label()
    assert 520 < float(label.split()[0]) < 580


# --- Tab 5: the fit plot -----------------------------------------------------
# A reported tau is not assessable without the curve beside it, so the tab draws
# data + fit + residuals. These check the plot tracks what the table says.

def test_building_the_tab_does_not_crash_on_the_initial_row_selection(window):
    """selectRow() emits currentCellChanged immediately, so connecting the handler
    before the canvas exists crashed the whole tab at construction."""
    assert window.analysis_tab.fit_canvas is not None
    assert window.analysis_tab.table.currentRow() == 0


def test_the_fit_plot_draws_the_data_and_the_model(analysis_window):
    tab = analysis_window.analysis_tab
    tab.on_fit_segment()
    tab.table.selectRow(0)
    lines = tab.fit_canvas.ax.get_lines()
    assert len(lines) == 2, "expected the data and the fitted curve"
    assert "tau" in lines[1].get_label(), "the fit's stats belong in the legend"


def test_the_residual_panel_exists_and_holds_the_residuals(analysis_window):
    """Overlap alone cannot distinguish exp from stretched at plot size; structure
    in the residuals is what exposes a wrong model."""
    tab = analysis_window.analysis_tab
    tab.on_fit_segment()
    tab.table.selectRow(0)
    assert tab.fit_canvas.resid_ax is not None
    assert len(tab.fit_canvas.resid_ax.get_lines()) >= 1


def test_selecting_a_table_row_plots_that_trace(analysis_window):
    tab = analysis_window.analysis_tab
    tab.on_fit_segment()
    tab.table.selectRow(0)
    assert "absorbance" in tab.fit_canvas.ax.get_title()
    tab.table.selectRow(1)
    assert "current" in tab.fit_canvas.ax.get_title()
    assert tab.fit_canvas.ax.get_ylabel() == "Current (A)"


def test_a_failed_fit_still_plots_the_data(analysis_window):
    """The data is exactly what you need in order to choose a better window, so a
    failed fit must not leave an empty canvas."""
    from spec_echem.analysis import FitResult
    tab = analysis_window.analysis_tab
    tab._fits["Doping 0"] = {"absorbance": FitResult("exp", reason="singular")}
    tab.table.selectRow(0)
    tab._draw_fit()
    assert len(tab.fit_canvas.ax.get_lines()) == 1
    assert any("singular" in t.get_text() for t in tab.fit_canvas.ax.texts)


def test_moving_the_wavelength_discards_the_absorbance_fit(analysis_window):
    """A tau belongs to the wavelength it was measured at. Leaving the old number
    on screen beside a curve from somewhere else would silently mislabel it."""
    tab = analysis_window.analysis_tab
    tab.on_fit_segment()
    assert tab._fits["Doping 0"]["absorbance"].ok
    tab.wavelength_spin.setValue(550.0)
    assert "absorbance" not in tab._fits["Doping 0"]
    assert "current" in tab._fits["Doping 0"], "the echem fits are unaffected"


def test_the_stop_time_follows_a_longer_segment(analysis_window, tmp_path):
    """The stop used to be auto-filled with the first segment's length and then
    kept, so moving to a LONGER segment silently fitted only its first part and
    reported a tau for a window the user never chose. 0 means "to the end"."""
    import numpy as np
    from spec_echem.data import DATA_TYPE_DOPING, write_echem_file, EchemData
    from spec_echem.experiment import Segment

    tab = analysis_window.analysis_tab
    tab.on_segment_changed()
    assert tab._window(tab._all_traces("Doping 0"))[1] is None, "no upper bound"

    # a second segment three times as long
    t = np.linspace(0.0, 60.0, 200)
    write_echem_file(
        EchemData(time=t, potential=np.full(200, 0.4),
                  current=3.0e-5 * np.exp(-t / 4.0)),
        DATA_TYPE_DOPING, 1, tmp_path, "run")
    analysis_window.results["Doping 1"] = analysis_window.results["Doping 0"]
    analysis_window.segments_by_label["Doping 1"] = Segment(
        "Doping 1", DATA_TYPE_DOPING, 1, 200, 0.3, True)
    tab.refresh_segments()
    tab.segment_combo.setCurrentIndex(tab.segment_combo.findText("Doping 1"))

    fits = tab._fit_one("Doping 1")
    assert fits["current"].t_last > 55.0, "the fit must reach the end of the segment"


def test_moving_the_window_redraws_the_shading_before_refitting(analysis_window):
    """The workflow is move the edge, look, refit — so the greyed region has to
    follow the spin box immediately, not wait for the next fit."""
    tab = analysis_window.analysis_tab
    tab.on_fit_segment()
    tab.auto_start_check.setChecked(False)
    before = len(tab.fit_canvas.ax.patches)
    tab.start_spin.setValue(5.0)
    assert len(tab.fit_canvas.ax.patches) > before, "expected the excluded span"
