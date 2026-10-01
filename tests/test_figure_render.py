"""
Offscreen figure rendering: a saved figure must not inherit the window it was
drawn in.

This is the groundwork for figure export. The bug it forecloses is a real one --
three attempts at the linearity-plot legend in September 2026 failed because they
were developed against a wide Mac canvas and broke at the Win11 rig's ~6.4x3.8 in,
and the same cause makes a figure saved on one machine differ from the same figure
saved on the other.

Headless: forces the offscreen Qt platform, so it runs with no display.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtWidgets import QApplication            # noqa: E402
from gui.widgets.plot_canvas import MplCanvas      # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(app):
    return MplCanvas(xlabel="Time (s)", ylabel="Absorbance")


def test_the_rendered_figure_has_the_size_asked_for_not_the_widgets(canvas):
    """The whole point: output geometry is chosen, not inherited."""
    canvas.fig.set_size_inches(12.0, 3.0)          # a wide window, as on the Mac
    wl = np.linspace(400.0, 1100.0, 50)

    fig = canvas.render_to_figure(
        lambda: canvas.show_spectrum(wl, np.sin(wl / 100.0)),
        figsize=(6.5, 4.5), dpi=300)

    assert tuple(fig.get_size_inches()) == (6.5, 4.5)
    assert fig.dpi == 300
    # ...and the widget keeps the size it had.
    assert tuple(canvas.fig.get_size_inches()) == (12.0, 3.0)
    assert fig is not canvas.fig


def test_rendering_offscreen_leaves_the_on_screen_plot_alone(canvas):
    """A save must not disturb what the user is looking at."""
    wl = np.linspace(400.0, 1100.0, 50)
    canvas.show_spectrum(wl, np.ones_like(wl), title="on screen")
    before_ax = canvas.ax
    before_lines = len(canvas.ax.lines)

    canvas.render_to_figure(
        lambda: canvas.show_spectrum(wl, np.zeros_like(wl), title="offscreen"),
        figsize=(3.25, 2.4))

    assert canvas.ax is before_ax
    assert len(canvas.ax.lines) == before_lines
    assert canvas.ax.get_title() == "on screen"


def test_rendering_a_fit_restores_the_axis_labels_it_overwrites(canvas):
    """plot_fit assigns self._xlabel/_ylabel and creates resid_ax. Rendering one
    offscreen must not leave the widget relabeled or carrying stray axes."""
    t = np.linspace(0.0, 10.0, 40)
    y = 1.0 - np.exp(-t / 2.0)

    assert not hasattr(canvas, "resid_ax")
    canvas.render_to_figure(
        lambda: canvas.plot_fit(t, y, y, "Time (s)", "Charge (C)"),
        figsize=(6.5, 4.5))

    assert canvas._xlabel == "Time (s)" and canvas._ylabel == "Absorbance"
    assert not hasattr(canvas, "resid_ax")


def test_the_footnote_wraps_to_the_output_width_not_the_widgets(canvas):
    """The footnote is wrapped by the canvas because only it knows the width. That
    width must be the OUTPUT figure's, or a provenance line laid out for a wide Mac
    window runs off the edge of a narrow saved figure."""
    canvas.fig.set_size_inches(20.0, 4.0)          # absurdly wide widget
    note = ("run 20250710 — " + "measured at a fixed integration time ") * 3

    wide = canvas.render_to_figure(
        lambda: canvas.plot_multi_xy(
            [(np.arange(5.0), np.arange(5.0), "a")], "x", "y", footnote=note),
        figsize=(13.0, 4.5))
    narrow = canvas.render_to_figure(
        lambda: canvas.plot_multi_xy(
            [(np.arange(5.0), np.arange(5.0), "a")], "x", "y", footnote=note),
        figsize=(3.25, 4.5))

    lines = lambda fig: len(fig.texts[-1].get_text().splitlines())   # noqa: E731
    assert lines(narrow) > lines(wide), (
        f"narrow wrapped to {lines(narrow)} lines, wide to {lines(wide)} — the "
        "footnote is not following the output width")


def test_a_stamped_fit_figure_collides_with_neither_its_title_nor_its_footnote(app):
    """plot_fit builds a shared-x gridspec under CONSTRAINED layout, which
    tight_layout cannot handle: it warns and does nothing, so the footnote band was
    never reserved and provenance landed on top of the x-axis label. Reserving it
    through the engine then exposed the other end -- the engine does not treat a
    suptitle as part of the rect, so the residual panel climbed over the title.
    Both ends are checked here because fixing one is what revealed the other.
    """
    import warnings
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    canvas = MplCanvas(xlabel="Time (s)", ylabel="Absorbance")
    t = np.linspace(0.0, 20.0, 60)
    y = 0.5 * (1.0 - np.exp(-t / 4.0))

    fig = Figure(figsize=(6.5, 4.5), dpi=100, tight_layout=True)
    # RECORDED, not raised: turning the warning into an error makes tight_layout
    # abort instead of running, which hides the bug -- in production it runs, warns,
    # and repositions the axes anyway. An earlier version of this test did that and
    # passed against the broken code.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with canvas._retarget(fig):
            canvas.plot_fit(t, y, y, "Time (s)", "Absorbance", title="A title")
            canvas._draw_footnote("20250710 · spec-echem 0.3.1")
    assert not [w for w in caught if "tight_layout" in str(w.message)], \
        "tight_layout was applied to a constrained gridspec"
    FigureCanvasAgg(fig)
    fig.canvas.draw()

    tops = [ax.get_position().y1 for ax in fig.axes]
    bottoms = [ax.get_position().y0 for ax in fig.axes]
    suptitle_y = max(tx.get_position()[1] for tx in fig.texts)
    footnote_y = min(tx.get_position()[1] for tx in fig.texts)

    assert max(tops) < suptitle_y - 0.01, "the panel climbed over the title"
    assert min(bottoms) > footnote_y + 0.01, "provenance landed on the x-axis label"
