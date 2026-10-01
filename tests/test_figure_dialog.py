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
                                       write_csv, _PlotToolbar)


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
    """One save affordance. The toolbar's writes at the figure's own dpi with no
    provenance, which is not what this dialog promises."""
    names = [t[0] for t in _PlotToolbar.toolitems if t[0]]
    assert "Save" not in names
    assert "Zoom" in names and "Pan" in names


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
