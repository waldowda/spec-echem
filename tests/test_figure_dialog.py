"""
The figure preview dialog: output size is chosen, not inherited, and the CSV
carries its provenance.

Headless: forces the offscreen Qt platform, so it runs with no display.
"""
import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtWidgets import QApplication                       # noqa: E402
from gui.widgets.plot_canvas import MplCanvas                 # noqa: E402
from gui.widgets.figure_dialog import (FigureDialog, PRESETS, # noqa: E402
                                       preset_rc, write_csv, PlotToolbar)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app):
    canvas = MplCanvas(xlabel="Time (s)", ylabel="Absorbance")
    canvas.fig.set_size_inches(14.0, 3.0)        # a window of the wrong shape
    t = np.linspace(0.0, 10.0, 40)
    frame = pd.DataFrame({"time": t, "absorbance": 1.0 - np.exp(-t / 2.0)})
    return FigureDialog(
        None, canvas,
        draw=lambda: canvas.show_spectrum(t, frame["absorbance"].values),
        csv=lambda: frame, basename="doping5_kinetics", run_id="20250710")


def test_each_preset_renders_at_its_own_size_and_font(dialog):
    """A preset re-renders; it never scales another preset's output. That is why
    the single-column one is readable instead of being all axis labels."""
    seen = []
    for i, (_label, size, pt) in enumerate(PRESETS):
        dialog.preset_combo.setCurrentIndex(i)
        assert tuple(dialog._fig.get_size_inches()) == size
        seen.append(pt)
    assert len(set(seen)) > 1, "every preset uses the same font size"

    # Single column is SMALLER in inches but not in points-per-inch-of-figure --
    # it draws at its own size rather than being shrunk.
    single = next(p for p in PRESETS if "Single" in p[0])
    double = next(p for p in PRESETS if "Double" in p[0])
    assert single[2] < double[2] and single[1][0] < double[1][0]


def test_the_preview_does_not_inherit_the_window(dialog):
    assert tuple(dialog._canvas.fig.get_size_inches()) == (14.0, 3.0)
    assert dialog._fig is not dialog._canvas.fig


def test_the_toolbar_has_no_save_button_of_its_own(dialog):
    """One save affordance, in the dialog AND on the live tabs. The toolbar's save
    writes at whatever size the widget happens to be, with no provenance -- which is
    the entire failure this work exists to remove."""
    names = [t[0] for t in PlotToolbar.toolitems if t[0]]
    assert "Save" not in names
    assert "Zoom" in names and "Pan" in names


def test_the_live_band_tab_toolbar_also_has_no_save(app):
    """Tab 6 carries a toolbar of its own; it must not offer a second save."""
    from gui.main_window import MainWindow
    from gui.widgets.figure_dialog import PlotToolbar as _T
    win = MainWindow()
    assert isinstance(win.band_tab.toolbar, _T)
    assert "Save" not in [t[0] for t in win.band_tab.toolbar.toolitems if t[0]]


def test_the_saved_file_has_the_requested_pixel_size(dialog, tmp_path, monkeypatch):
    from qtpy.QtWidgets import QFileDialog
    out = tmp_path / "fig.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    dialog.preset_combo.setCurrentIndex(0)       # 6.5 x 4.5
    dialog.dpi_spin.setValue(300)
    dialog.on_save_figure()

    from PIL import Image
    assert Image.open(out).size == (1950, 1350)


def test_provenance_is_optional_on_the_figure(dialog):
    assert dialog.provenance_text() is None      # off by default
    dialog.provenance_check.setChecked(True)
    text = dialog.provenance_text()
    assert "20250710" in text and "spec-echem" in text


def test_the_csv_always_carries_provenance_and_reads_back(dialog, tmp_path,
                                                          monkeypatch):
    """Unlike the figure stamp, this is not optional: a data file outlives the
    context that produced it."""
    from qtpy.QtWidgets import QFileDialog
    out = tmp_path / "fig.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    assert not dialog.provenance_check.isChecked()      # still writes it
    dialog.on_save_csv()

    head = out.read_text().splitlines()[0]
    assert head.startswith("# run: 20250710")
    back = pd.read_csv(out, comment="#")
    assert list(back.columns) == ["time", "absorbance"]
    assert len(back) == 40


def test_write_csv_round_trips_without_a_dialog(tmp_path):
    frame = pd.DataFrame({"potential": [0.2, 0.3], "tau": [1.5, 2.5]})
    path = tmp_path / "x.csv"
    write_csv(frame, path, ["run: 20250710", "produced by: spec-echem 0.3.1"])
    back = pd.read_csv(path, comment="#")
    pd.testing.assert_frame_equal(back, frame)


def test_the_title_does_not_outweigh_the_axis_labels():
    """matplotlib defaults axes.titlesize to 'large' = 1.2x the base, which on a
    3.25 in figure makes the title the heaviest thing on the plot. Here the title
    only names the segment, so it sits at label size."""
    for _label, _size, pt in PRESETS:
        rc = preset_rc(pt)
        assert rc["axes.titlesize"] == rc["axes.labelsize"] == pt
        assert rc["xtick.labelsize"] < pt       # ticks step back from the labels


def test_the_footnote_scales_with_the_preset_and_never_leads(app):
    """A footnote fixed at 7 pt becomes the LARGEST text on a 7 pt figure."""
    import matplotlib
    canvas = MplCanvas()
    sizes = {}
    for pt in (10, 7):
        with matplotlib.rc_context(preset_rc(pt)):
            fig = canvas.render_to_figure(
                lambda: canvas.show_spectrum(np.arange(5.0), np.arange(5.0)),
                figsize=(3.25, 2.25), dpi=100, footnote="20250710 · spec-echem")
        sizes[pt] = fig.texts[-1].get_fontsize()

    assert sizes[10] == 7, "the on-screen footnote size must be unchanged"
    assert sizes[7] < sizes[10]
    assert sizes[7] < preset_rc(7)["xtick.labelsize"], "footnote outweighs the ticks"


# --- the canvas remembers what it drew, so the tabs need no bookkeeping -------

def test_the_canvas_records_its_last_draw_and_can_repeat_it(app):
    canvas = MplCanvas()
    assert canvas.last_draw() is None                  # nothing drawn yet

    wl = np.linspace(400.0, 1100.0, 20)
    canvas.show_spectrum(wl, np.sin(wl / 100.0), title="first")
    fig = canvas.render_to_figure(canvas.last_draw(), figsize=(6.5, 4.5), dpi=100)
    assert fig.axes[0].get_title() == "first"



def test_a_draw_issued_while_rendering_is_not_recorded(app):
    """The export must not become the thing the canvas remembers.

    Exercised through the PUBLIC method, which is the only path that reaches the
    guard: last_draw() re-invokes the undecorated function, so a test that went
    through it would pass whether the guard existed or not.
    """
    canvas = MplCanvas()
    canvas.show_spectrum(np.arange(5.0), np.arange(5.0), title="on screen")
    recorded = canvas._last_draw

    canvas.render_to_figure(
        lambda: canvas.show_spectrum(np.arange(3.0), np.arange(3.0),
                                     title="offscreen"),
        figsize=(3.25, 2.25), dpi=100)

    assert canvas._last_draw is recorded
    assert canvas._last_draw[2].get("title") == "on screen"


def test_a_two_dimensional_view_offers_no_csv(app):
    """Absorbance against wavelength AND time is a matrix already archived as .h5
    and .txt, so a CSV of it would be a worse copy of something on disk."""
    canvas = MplCanvas()
    frame = pd.DataFrame(np.zeros((4, 3)), index=[400.0, 500.0, 600.0, 700.0],
                         columns=[0.0, 1.0, 2.0])
    canvas.show_absorbance(frame)
    assert canvas.last_data() is None

    # ...while a series plot does.
    canvas.plot_series(np.arange(3.0), {"tau": np.arange(3.0)}, "Potential (V)",
                       "tau (s)")
    out = canvas.last_data()
    assert list(out.columns) == ["x", "tau"] and len(out) == 3


def test_every_wired_canvas_opens_the_same_dialog(app, monkeypatch):
    """Five save buttons, one launcher -- so they cannot drift apart."""
    from gui.main_window import MainWindow
    import gui.widgets.figure_dialog as fd

    win = MainWindow()
    canvases = [win.results_tab.canvas, win.results_tab.echem_canvas,
                win.analysis_tab.fit_canvas, win.analysis_tab.ladder_canvas,
                win.band_tab.canvas]

    opened = []
    monkeypatch.setattr(fd.FigureDialog, "exec_", lambda self: opened.append(self),
                        raising=False)
    monkeypatch.setattr(fd.FigureDialog, "exec", lambda self: opened.append(self),
                        raising=False)

    for canvas in canvases:
        canvas.show_spectrum(np.arange(5.0), np.arange(5.0), title="x")
        dialog = fd.open_figure_dialog(None, canvas, win, "basename")
        assert dialog is not None
        assert tuple(dialog._fig.get_size_inches()) == PRESETS[0][1]
    assert len(opened) == len(canvases)
