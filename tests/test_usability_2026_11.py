"""
Usability changes requested after the first sessions with students (2026-11-06):
the wavelength line can be left out of a figure, the save buttons say Export, the
Analysis traces have a data cursor, and the Parameters tab shows the run's length.

Headless: forces the offscreen Qt platform, so it runs with no display.
"""
import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtWidgets import QApplication, QPushButton           # noqa: E402

from spec_echem.data import DATA_TYPE_DOPING                   # noqa: E402
from spec_echem.experiment import Segment, run_duration_bounds  # noqa: E402
from spec_echem.gitt import gitt_duration_bounds               # noqa: E402
from spec_echem.pitt import pitt_duration_bounds               # noqa: E402
from spec_echem.settings import DEFAULT_SETTINGS               # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    from unittest.mock import patch
    import gui.main_window as _mw
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        w = _mw.MainWindow()
    yield w
    w.close()


# --- 1. the wavelength line can be left out of a figure ---------------------------

def _load_one_segment(win, tmp_path):
    wl = np.linspace(400.0, 1100.0, 60)
    t = np.linspace(0.0, 20.0, 40)
    frame = pd.DataFrame(np.outer(np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2),
                                  1.0 - np.exp(-t / 4.0)), index=wl, columns=t)
    win.results = {"Doping 0": frame}
    win.segments_by_label = {
        "Doping 0": Segment("Doping 0", DATA_TYPE_DOPING, 0, 40, 0.1, True)}
    win.run_folder = tmp_path / "run"
    win.results_tab.refresh_segments()
    return win.results_tab


def _vlines(fig):
    """Vertical lines drawn at a data x across the full height: the wavelength line."""
    out = []
    for ax in fig.axes:
        for line in ax.get_lines():
            x = np.asarray(line.get_xdata(), dtype=float)
            if x.size == 2 and x[0] == x[1]:
                out.append(float(x[0]))
    return out


def test_the_wavelength_line_can_be_left_out_of_the_export(win, tmp_path):
    tab = _load_one_segment(win, tmp_path)
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("spectra"))
    assert tab.show_wl_line.isChecked()
    assert tab.canvas._last_draw[2]["mark_wl"] is not None
    assert _vlines(tab.canvas.render_to_figure(tab.canvas.last_draw()))

    tab.show_wl_line.setChecked(False)
    assert tab.canvas._last_draw[2]["mark_wl"] is None
    assert _vlines(tab.canvas.fig) == []
    # What an export draws: the recorded call, re-run into a fresh figure.
    assert _vlines(tab.canvas.render_to_figure(tab.canvas.last_draw())) == []
    # The wavelength itself is still followed by the other views.
    assert tab.analysis_wl.value() > 0


def test_the_line_box_shows_only_on_the_view_that_draws_the_line(win, tmp_path):
    tab = _load_one_segment(win, tmp_path)
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("spectra"))
    assert not tab.show_wl_line.isHidden()
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("kinetics"))
    assert tab.show_wl_line.isHidden()


# --- 2. every plot's button says Export -------------------------------------------

def test_no_button_under_a_plot_still_says_save_figure(win):
    from gui.widgets.figure_dialog import EXPORT_LABEL
    texts = [b.text() for b in win.findChildren(QPushButton)]
    assert not any(t.startswith("Save figure") for t in texts)
    # Results x2, Analysis x2, Band fits x1.
    assert texts.count(EXPORT_LABEL) == 5
    tips = {b.toolTip() for b in win.findChildren(QPushButton) if b.text() == EXPORT_LABEL}
    assert len(tips) == 1 and "CSV" in tips.pop()


# --- 3. the data cursor on the Analysis traces ------------------------------------

def _move(canvas, ax, x, y):
    from matplotlib.backend_bases import MouseEvent
    canvas.draw()          # lay out first: the pixel position depends on it
    px, py = ax.transData.transform((x, y))
    event = MouseEvent("motion_notify_event", canvas, px, py)
    canvas.callbacks.process("motion_notify_event", event)


def test_the_cursor_snaps_to_the_data_and_reads_it_out(win):
    tab = win.analysis_tab
    canvas = tab.fit_canvas
    t = np.linspace(0.0, 10.0, 21)
    y = 1.0 - np.exp(-t / 2.0)
    canvas.plot_fit(t, y, y, "Time (s)", "Absorbance")

    tab.cursor_check.setChecked(True)
    _move(canvas, canvas.ax, 4.1, 0.0)     # near t = 4.0, off the curve in y
    text = canvas._readout_text.get_text()
    assert "Time (s): 4" in text
    assert f"Absorbance: {y[8]:.6g}" in text
    dot_x, dot_y = canvas._readout_dot.get_data()
    assert dot_x[0] == pytest.approx(4.0) and dot_y[0] == pytest.approx(y[8])


def test_the_cursor_never_reaches_an_export(win):
    tab = win.analysis_tab
    canvas = tab.fit_canvas
    t = np.linspace(0.0, 10.0, 21)
    y = np.exp(-t)
    canvas.plot_fit(t, y, y, "Time (s)", "Current (A)")
    tab.cursor_check.setChecked(True)
    _move(canvas, canvas.ax, 2.0, y[4])
    assert canvas._readout_dot is not None

    def lines(fig):
        return sum(len(ax.get_lines()) for ax in fig.axes)

    def readouts(fig):
        return [t for ax in fig.axes for t in ax.texts if "Time (s):" in t.get_text()]

    exported = canvas.render_to_figure(canvas.last_draw())
    assert lines(canvas.fig) == lines(exported) + 1     # the dot, on screen only
    assert readouts(canvas.fig) and not readouts(exported)
    assert list(canvas.last_data().columns) == ["x", "y", "fit", "residual"]
    assert len(canvas.last_data()) == t.size


def test_turning_the_cursor_off_removes_it(win):
    tab = win.analysis_tab
    canvas = tab.fit_canvas
    t = np.linspace(0.0, 10.0, 21)
    canvas.plot_fit(t, t, None, "Time (s)", "Charge (C)")
    tab.cursor_check.setChecked(True)
    _move(canvas, canvas.ax, 3.0, 3.0)
    dot = canvas._readout_dot
    assert dot is not None and dot in canvas.ax.get_lines()

    tab.cursor_check.setChecked(False)
    assert dot not in canvas.ax.get_lines()
    assert not canvas.readout_on()


def test_the_cursor_ignores_a_zero_line():
    """The residual panel's axhline is not data and must never be read out."""
    from gui.widgets.plot_canvas import MplCanvas
    canvas = MplCanvas()
    canvas.ax.plot([0.0, 10.0], [5.0, 5.0], "o")
    canvas.ax.axhline(0.0)
    canvas.draw()
    px, py = canvas.ax.transData.transform((0.5, 0.0))
    assert canvas.nearest_point(canvas.ax, px, py) == (0.0, 5.0)


# --- 4. the whole run's length ----------------------------------------------------

def _settings(**over):
    s = dict(DEFAULT_SETTINGS, cv_enabled=False, prededoping_enabled=False,
             doping_enabled=False, pitt_enabled=False, gitt_enabled=False)
    s.update(over)
    return s


def test_the_run_length_adds_up_by_hand():
    s = _settings(cv_enabled=True, cv_initial_v=0.0, cv_limit1_v=0.8,
                  cv_limit2_v=-0.2, cv_final_v=0.0, cv_step_size=2.0,
                  cv_scan_rate=20.0, cv_cycles=3,
                  prededoping_enabled=True, prededoping_time=30.0,
                  doping_enabled=True, doping_potential_start=0.1,
                  doping_potential_end=0.5, doping_potential_step=0.1,
                  chrono_time=60.0, chrono_delta_time=0.1)
    lo, hi, n = run_duration_bounds(s)
    cv = (0.8 + 1.0 + 0.2) * 1000 / 20.0 * 3      # 2 V of path at 20 mV/s, 3 cycles
    ladder = 5 * 2 * 60.0                           # 5 potentials, dope + dedope
    assert lo == hi == pytest.approx(cv + 30.0 + ladder)
    assert n == 1 + 1 + 10


def test_a_titration_gives_a_range():
    s = _settings(pitt_enabled=True, pitt_cutoff_enabled=True,
                  gitt_enabled=True, gitt_relax_settle_enabled=True)
    lo, hi, n = run_duration_bounds(s)
    p_lo, p_hi = pitt_duration_bounds(s)
    g_lo, g_hi = gitt_duration_bounds(s)
    assert (lo, hi, n) == (pytest.approx(p_lo + g_lo), pytest.approx(p_hi + g_hi), 2)
    assert lo < hi


def test_nothing_selected_is_zero_segments():
    assert run_duration_bounds(_settings()) == (0.0, 0.0, 0)


def test_the_parameters_tab_shows_the_run_length_and_follows_edits(win):
    tab = win.parameters_tab
    tab.populate_from(_settings(doping_enabled=True, doping_potential_start=0.1,
                                doping_potential_end=0.1, chrono_time=30.0))
    assert "60 s" in tab.total_time_label.text()            # 2 x 30 s
    assert "2 segments" in tab.total_time_label.text()
    tab._widgets["chrono_time"].setValue(300.0)
    assert "10 min" in tab.total_time_label.text()
    tab._widgets["doping_enabled"].setChecked(False)
    assert tab.total_time_label.text() == "Nothing selected to run"


def test_the_cursor_reads_the_data_not_the_fit_drawn_through_it():
    """Seen on the first screenshot: a fit through the data won the tie and the
    readout gave the model's value."""
    from gui.widgets.plot_canvas import MplCanvas
    canvas = MplCanvas()
    canvas.ax.plot([0.0, 1.0, 2.0], [1.0, 2.0, 3.0], "o")       # data
    canvas.ax.plot([0.0, 1.0, 2.0], [1.05, 2.05, 3.05], "-")    # its fit
    canvas.draw()
    px, py = canvas.ax.transData.transform((1.0, 2.05))         # ON the fit
    assert canvas.nearest_point(canvas.ax, px, py) == (1.0, 2.0)


def test_the_export_window_can_leave_the_line_out(win, tmp_path):
    """Seen 2026-11-06: the line was still in an export -- the Results-tab box was
    not where anyone looked. The Export window now offers it too."""
    from gui.widgets.figure_dialog import FigureDialog
    tab = _load_one_segment(win, tmp_path)
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("spectra"))
    canvas = tab.canvas
    dialog = FigureDialog(win, canvas, canvas.last_draw(),
                          draw_unmarked=canvas.last_draw_unmarked())
    assert not dialog.line_check.isHidden() and dialog.line_check.isChecked()
    assert _vlines(dialog._fig)
    dialog.line_check.setChecked(False)
    assert _vlines(dialog._fig) == []                  # what Save writes
    assert canvas._last_draw[2]["mark_wl"] is not None  # the screen is untouched
    dialog.close()

    # No line on the plot, no box offered.
    tab.show_wl_line.setChecked(False)
    assert canvas.last_draw_unmarked() is None
    dialog = FigureDialog(win, canvas, canvas.last_draw(), draw_unmarked=None)
    assert dialog.line_check.isHidden()
    dialog.close()
