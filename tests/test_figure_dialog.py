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


def test_a_two_dimensional_view_has_no_per_trace_csv_but_still_offers_one(app):
    """It has no `csv` callable -- it is a wavelength x time block, not a set of
    named series -- so the dialog widens the block instead. It was masked on the
    grounds that the block is already on disk, but that is not these numbers: this
    is what the FIGURE shows, after any wavelength window (2026-10-02)."""
    canvas = MplCanvas()
    frame = pd.DataFrame(np.arange(12.0).reshape(4, 3),
                         index=[400.0, 500.0, 600.0, 700.0],
                         columns=[0.0, 1.0, 2.5])
    canvas.show_absorbance(frame)
    assert canvas.last_data() is None                 # no per-trace csv
    assert canvas.last_matrix() is not None           # but a block

    dlg = FigureDialog(None, canvas, draw=lambda: canvas.show_absorbance(frame),
                       csv=None, matrix=frame, basename="cv_spectra")
    assert dlg.csv_btn.isVisible() or not dlg.isVisible()   # shown, not masked
    assert dlg.csv_btn.isVisibleTo(dlg)

    out = dlg._table()
    assert list(out.columns) == ["Wavelength (nm)", "0 s", "1 s", "2.5 s"]
    assert np.allclose(out["Wavelength (nm)"], frame.index.values)
    assert np.allclose(out.to_numpy()[:, 1:], frame.to_numpy())

    # ...while a series plot keeps its own csv and is untouched by this.
    canvas.plot_series(np.arange(3.0), {"tau": np.arange(3.0)}, "Potential (V)",
                       "tau (s)")
    series = canvas.last_data()
    assert list(series.columns) == ["x", "tau"] and len(series) == 3


def test_the_spectra_csv_says_what_its_columns_are(app, tmp_path, monkeypatch):
    """A wide table is not self-describing the way a tall one is: without this the
    headers are bare numbers and nothing says they are times."""
    from qtpy.QtWidgets import QFileDialog
    canvas = MplCanvas()
    frame = pd.DataFrame(np.arange(6.0).reshape(3, 2), index=[400.0, 500.0, 600.0],
                         columns=[0.0, 0.5])
    canvas.show_absorbance(frame)
    dlg = FigureDialog(None, canvas, draw=lambda: canvas.show_absorbance(frame),
                       csv=None, matrix=frame, basename="cv_spectra",
                       run_id="20260925_test10")
    out = tmp_path / "cv.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    dlg.on_save_csv()

    text = out.read_text()
    assert "# run: 20260925_test10" in text
    assert "column 1: wavelength (nm)" in text
    back = pd.read_csv(out, comment="#")
    assert list(back.columns) == ["Wavelength (nm)", "0 s", "0.5 s"]
    assert np.allclose(back.to_numpy()[:, 1:], frame.to_numpy())


def test_a_plot_with_neither_series_nor_block_still_offers_nothing(app):
    """The guard that kept a dead control off the dialog has to survive the unmask."""
    canvas = MplCanvas()
    dlg = FigureDialog(None, canvas, draw=lambda: canvas.show_message("no data"),
                       csv=None, matrix=None, basename="empty")
    assert not dlg.csv_btn.isVisibleTo(dlg)
    assert not dlg.itx_btn.isVisibleTo(dlg)
    assert dlg._table() is None


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


def test_an_empty_state_message_discards_the_previous_plot(app):
    """A message is not a figure, and it must not leave the PREVIOUS figure
    saveable: a save made then would write that plot under this one's name."""
    canvas = MplCanvas()
    canvas.show_spectrum(np.arange(5.0), np.arange(5.0), title="real plot")
    assert canvas.last_draw() is not None

    canvas.show_message("No current data for Doping 3.")
    assert canvas.last_draw() is None
    assert canvas.last_data() is None


# --- step 6: tab 5's bounded save-all ----------------------------------------

def test_save_all_writes_the_traces_and_the_ladder_with_their_data(app, tmp_path,
                                                                   monkeypatch):
    """Three fits of the segment on screen plus the ladder, each with its CSV."""
    import gui.tabs.analysis_tab as at
    from gui.main_window import MainWindow
    from spec_echem.data import (DATA_TYPE_DOPING, write_echem_file, EchemData)
    from spec_echem.experiment import Segment

    win = MainWindow()
    run = tmp_path / "20250710_run"
    t = np.linspace(0.0, 20.0, 60)
    wl = np.linspace(400.0, 1100.0, 40)
    frac = 1.0 - np.exp(-t / 4.0)
    a = 0.02 + np.outer(np.exp(-0.5 * ((wl - 900.0) / 60.0) ** 2), 0.5 * frac)
    win.results = {"Doping 0": pd.DataFrame(a, index=wl, columns=t)}
    win.segments_by_label = {
        "Doping 0": Segment("Doping 0", DATA_TYPE_DOPING, 0, 60, 0.1, True)}
    write_echem_file(EchemData(time=t, potential=np.full(60, 0.3),
                               current=3.0e-5 * np.exp(-t / 4.0)),
                     DATA_TYPE_DOPING, 0, tmp_path, "20250710_run")
    win.run_folder = run
    tab = win.analysis_tab
    tab.refresh_segments()
    tab.on_fit_all()

    shown = []
    monkeypatch.setattr(at.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[-1])))
    monkeypatch.setattr(at.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[-1])))
    tab.on_save_all_figures()

    figures = sorted(p.name for p in (run / "figures").glob("*.png"))
    # The ladder is across EVERY segment, so its name carries no segment -- naming
    # it after whichever one was selected produced "Pre-dedoping0_ladder.png" for a
    # ladder of the whole run (reported 2026-10-02).
    assert any(f.startswith("ladder_") for f in figures), figures
    assert not any("Doping" in f and "ladder" in f for f in figures), figures
    assert any("absorbance" in f for f in figures)
    # Every figure has its numbers beside it, under the same stem.
    for png in (run / "figures").glob("*.png"):
        assert png.with_suffix(".csv").exists(), f"{png.name} has no CSV"

    # The message names the FOLDER, not just basenames.
    assert str(run / "figures") in shown[0]

    # The CSV carries provenance and reads back.
    csv = next((run / "figures").glob("*absorbance*.csv"))
    assert csv.read_text().startswith("# run: 20250710_run")
    assert not pd.read_csv(csv, comment="#").empty


def test_save_all_restores_the_trace_that_was_on_screen(app, tmp_path, monkeypatch):
    """It drives the real plotting path, so it must put the view back."""
    import gui.tabs.analysis_tab as at
    from gui.main_window import MainWindow

    # Every QMessageBox here is MODAL: left unpatched it blocks the suite forever.
    monkeypatch.setattr(at.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(at.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    from spec_echem.data import DATA_TYPE_DOPING
    from spec_echem.experiment import Segment

    win = MainWindow()
    t = np.linspace(0.0, 20.0, 40)
    wl = np.linspace(400.0, 1100.0, 30)
    win.results = {"Doping 0": pd.DataFrame(
        np.outer(np.ones_like(wl), 1.0 - np.exp(-t / 4.0)), index=wl, columns=t)}
    win.segments_by_label = {
        "Doping 0": Segment("Doping 0", DATA_TYPE_DOPING, 0, 40, 0.1, True)}
    win.run_folder = tmp_path / "run"
    tab = win.analysis_tab
    tab.refresh_segments()
    tab.on_fit_segment()
    tab.table.selectRow(1)

    tab.on_save_all_figures()
    assert tab.table.currentRow() == 1


# --- a figure must record the conditions it was taken at ----------------------

def test_a_wavelength_specific_view_names_its_wavelength(app, tmp_path):
    """Reported 2026-10-01: the kinetics and modulation views are taken at ONE
    wavelength, and neither the title nor the filename said which. The same trace at
    800 nm and at 520 nm are different measurements, and a figure that cannot say
    which is not evidence.
    """
    from gui.main_window import MainWindow
    from spec_echem.data import DATA_TYPE_DOPING
    from spec_echem.experiment import Segment

    win = MainWindow()
    wl = np.linspace(400.0, 1100.0, 60)
    t = np.linspace(0.0, 20.0, 40)
    frame = pd.DataFrame(np.outer(np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2),
                                  1.0 - np.exp(-t / 4.0)), index=wl, columns=t)
    win.results = {"Doping 0": frame}
    win.segments_by_label = {
        "Doping 0": Segment("Doping 0", DATA_TYPE_DOPING, 0, 40, 0.1, True)}
    win.run_folder = tmp_path / "run"
    tab = win.results_tab
    tab.refresh_segments()

    tab.wl_auto.setChecked(False)
    tab.analysis_wl.setValue(800.0)
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("kinetics"))

    title = tab.canvas._last_draw[2]["title"]
    assert "nm" in title, f"kinetics title names no wavelength: {title!r}"
    assert "80" in title
    assert "nm" in tab._figure_basename("absorbance")

    # ...and a view with no single wavelength must not inherit the last one.
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("spectra"))
    assert tab._plotted_wl is None
    assert "nm" not in tab._figure_basename("absorbance")


def test_an_automatic_probe_says_so_in_the_title(app, tmp_path):
    """An auto-chosen probe MOVES between segments, so two figures from one run can
    be at different wavelengths. The title has to admit that."""
    from gui.main_window import MainWindow
    from spec_echem.data import DATA_TYPE_DOPING
    from spec_echem.experiment import Segment

    win = MainWindow()
    wl = np.linspace(400.0, 1100.0, 60)
    t = np.linspace(0.0, 20.0, 40)
    win.results = {"Doping 0": pd.DataFrame(
        np.outer(np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2), 1.0 - np.exp(-t / 4.0)),
        index=wl, columns=t)}
    win.segments_by_label = {
        "Doping 0": Segment("Doping 0", DATA_TYPE_DOPING, 0, 40, 0.1, True)}
    win.run_folder = tmp_path / "run"
    tab = win.results_tab
    tab.refresh_segments()
    tab.wl_auto.setChecked(True)
    tab.view_combo.setCurrentIndex(tab.view_combo.findData("kinetics"))

    assert "(auto)" in tab.canvas._last_draw[2]["title"]
