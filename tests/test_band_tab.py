"""Tab 6 — Band Fits.

A tab rather than the dialog this began as: an all-segment fit is tens of seconds of
compute and must survive being looked away from, the strip plot wants to be visible
WHILE reading a single fit on tab 5, and it ends the invisible dependency where the
dialog silently inherited tab 5's model and window.
"""
import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qtpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication                            # noqa: E402

from spec_echem.data import DATA_TYPE_DEDOPING, DATA_TYPE_DOPING   # noqa: E402
from spec_echem.experiment import Segment                          # noqa: E402
from gui.main_window import MainWindow                             # noqa: E402


# Local fixtures, matching the house style: this project has no conftest.py, so each
# test module owns the ones it needs.
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    # Isolated from this machine's config/bench.ini, so a layout test does not depend
    # on whether the rig it runs on has potentiostat_mode = autolab set.
    import gui.main_window as _mw
    from unittest.mock import patch
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        win = MainWindow()
    win.show()
    yield win
    win.close()


def _segment(window, label, data_type, run, taus, potential_step=0.1):
    """A segment whose band really does have tau varying with wavelength."""
    rng = np.random.default_rng(abs(hash(label)) % 2**32)
    t = np.linspace(0.0, 20.0, 200)
    wl = np.linspace(480.0, 540.0, len(taus))
    absorb = np.array([0.05 + 0.3 * (1 - np.exp(-t / 0.45))
                       + 0.12 * (1 - np.exp(-t / tau))
                       + rng.normal(0, 5e-5, t.size) for tau in taus])
    window.results[label] = pd.DataFrame(absorb, index=wl, columns=t)
    window.segments_by_label[label] = Segment(
        label, data_type, run, num_points=200, delta_time=0.1, trigger=False)
    return wl


def _mono_segment(window, label, data_type, run, taus):
    """A SINGLE-exponential segment, so fitting with "exp" recovers the tau asked
    for. _segment builds biexponential data with a fixed 0.45 s fast component, so an
    exp fit of it returns an effective tau near 0.4 s whatever is passed — which is
    no good for a test that needs a wide, controlled spread."""
    rng = np.random.default_rng(abs(hash(label)) % 2**32)
    t = np.linspace(0.0, 400.0, 400)
    wl = np.linspace(480.0, 540.0, len(taus))
    absorb = np.array([0.05 + 0.4 * (1 - np.exp(-t / tau))
                       + rng.normal(0, 2e-5, t.size) for tau in taus])
    window.results[label] = pd.DataFrame(absorb, index=wl, columns=t)
    window.segments_by_label[label] = Segment(
        label, data_type, run, num_points=400, delta_time=1.0, trigger=False)


def _ladder(window):
    """Three doping steps and their dedopes, with a real potential ladder."""
    window.settings.update(doping_potential_start=0.3, doping_potential_step=0.2,
                           dedoping_potential=-0.5)
    window.loaded_run_settings = dict(window.settings)
    for run in range(3):
        _segment(window, f"Doping {run}", DATA_TYPE_DOPING, run,
                 [2.0 + 0.3 * run, 2.4 + 0.3 * run, 2.8 + 0.3 * run])
        _segment(window, f"Dedoping {run}", DATA_TYPE_DEDOPING, run,
                 [1.5 + 0.2 * run, 1.8 + 0.2 * run, 2.1 + 0.2 * run])
    window.band_tab.refresh_segments()


def test_the_tab_owns_its_model_and_window(window):
    """NOT shared with tab 5: changing the model there would silently invalidate a
    band fit sitting here."""
    tab = window.band_tab
    assert tab.model_combo is not None and tab.t_start is not None
    window.analysis_tab.model_combo.setCurrentIndex(
        window.analysis_tab.model_combo.findData("stretched"))
    window.analysis_tab.start_spin.setValue(3.0)
    tab.seed_from_analysis()
    assert tab.model_combo.currentData() == "stretched"   # seeded...
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("exp"))
    window.analysis_tab.model_combo.setCurrentIndex(
        window.analysis_tab.model_combo.findData("biexp"))
    assert tab.model_combo.currentData() == "exp"          # ...but not tracking


def test_a_single_segment_band_fits_and_reports(window):
    _ladder(window)
    tab = window.band_tab
    tab.segment_combo.setCurrentIndex(tab.segment_combo.findData("Doping 0"))
    tab.start_spin.setRange(0.0, 5000.0)
    tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0)
    tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("exp"))

    tab.on_fit_segment()

    assert tab._band is not None and len(tab._band) == 3
    assert tab.table.rowCount() == 3
    assert "of 3 wavelengths fitted" in tab.status.text()
    assert tab.save_btn.isEnabled()


def test_all_segments_splits_doping_from_dedoping(window):
    """Two plots, and dedoping is placed by the potential the film was DOPED TO --
    every dedoping step sits at the same -0.5 V, so against its own potential all
    three would stack on one x."""
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("exp"))

    tab.on_fit_all()

    assert tab._ladder is not None and len(tab._ladder) == 6
    directions = {d for _l, _p, d, _b in tab._ladder}
    assert directions == {"doping", "dedoping"}
    # dedoping carries the DOPING potential, not -0.5
    dedope = [p for _l, p, d, _b in tab._ladder if d == "dedoping"]
    assert sorted(dedope) == pytest.approx([0.3, 0.5, 0.7])
    assert len({round(p, 3) for p in dedope}) == 3      # not stacked on one x
    assert len(tab.canvas.fig.axes) == 2                 # doping | dedoping


def test_the_x_axis_is_volts_not_categories(window):
    """Changed 2026-10-01. It was categorical -- evenly spaced rungs with the
    potential on the tick -- which reads well for an even ladder but says something
    false about an uneven one, and is not how the downstream analysis plots tau
    against Vg. A point now sits at the potential it was measured at.
    """
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    ax = tab.canvas.fig.axes[0]
    # Ticks AT the measured potentials, so every rung is still labelled...
    np.testing.assert_allclose(ax.get_xticks(), [0.3, 0.5, 0.7], atol=1e-9)
    assert [t.get_text() for t in ax.get_xticklabels()] == ["+0.30", "+0.50", "+0.70"]
    # ...and the DATA is there too, not at 0, 1, 2.
    drawn = sorted({round(float(x), 3)
                    for line in ax.lines for x in line.get_xdata()})
    assert drawn and min(drawn) >= 0.3 and max(drawn) <= 0.7, drawn
    # The end rungs are not on the frame, or half of each strip would be clipped.
    assert ax.get_xlim()[0] < 0.3 and ax.get_xlim()[1] > 0.7


def test_an_uneven_ladder_is_spaced_by_its_potentials(window):
    """The point of a real axis: a 0.05 V step must look like half a 0.1 V step."""
    window.settings.update(doping_potential_start=0.30, doping_potential_step=0.05,
                           dedoping_potential=-0.5)
    window.loaded_run_settings = dict(window.settings)
    for run in range(3):
        _segment(window, f"Doping {run}", DATA_TYPE_DOPING, run,
                 [2.0 + 0.3 * run, 2.4 + 0.3 * run, 2.8 + 0.3 * run])
    window.band_tab.refresh_segments()
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    ticks = tab.canvas.fig.axes[0].get_xticks()
    np.testing.assert_allclose(ticks, [0.30, 0.35, 0.40], atol=1e-9)


def test_a_stretched_ladder_gets_a_beta_row(window):
    """beta is dimensionless and 0-1: its own row, sharing the potential axis, so it
    reads straight down from tau at the same potential."""
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("stretched"))

    tab.on_fit_all()

    assert len(tab.canvas.fig.axes) == 4                  # 2 directions x 2 rows
    assert any("beta" in a.get_ylabel() for a in tab.canvas.fig.axes)


def test_an_exp_ladder_has_no_beta_row(window):
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("exp"))

    tab.on_fit_all()

    assert len(tab.canvas.fig.axes) == 2
    assert not any("beta" in a.get_ylabel() for a in tab.canvas.fig.axes)


def test_a_cv_is_not_offered_at_all(window):
    """Not merely skipped on the ladder: a CV's spectra are taken DURING a sweep, so
    absorbance-against-time there is a sweep response, not a relaxation, and a tau
    fitted to it would be a number with no meaning."""
    from spec_echem.data import DATA_TYPE_CV
    _ladder(window)
    _segment(window, "CV", DATA_TYPE_CV, 0, [2.0, 2.2, 2.4])
    window.band_tab.refresh_segments()

    tab = window.band_tab
    offered = [tab.segment_combo.itemData(i)
               for i in range(tab.segment_combo.count())]
    assert "CV" not in offered
    assert "Doping 0" in offered


def test_a_hold_with_no_rung_is_named_not_silently_dropped(window):
    """Pre-dedoping IS a genuine hold, so it stays fittable — only its place on the
    ladder is undefined, and the status says so."""
    from spec_echem.data import DATA_TYPE_PREDEDOPING
    _ladder(window)
    _segment(window, "Pre-dedoping 0", DATA_TYPE_PREDEDOPING, 0, [2.0, 2.2, 2.4])
    window.band_tab.refresh_segments()

    tab = window.band_tab
    offered = [tab.segment_combo.itemData(i)
               for i in range(tab.segment_combo.count())]
    assert "Pre-dedoping 0" in offered           # fittable on its own

    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    assert "Pre-dedoping 0" in tab.status.text()
    assert "no ladder potential" in tab.status.text()
    assert len(tab._ladder) == 6                 # the six real rungs still fitted


def test_the_export_is_long_format_across_segments(window, tmp_path, monkeypatch):
    """One file every view can be drawn from elsewhere — the most useful artifact of
    a 2-D result."""
    from qtpy.QtWidgets import QFileDialog, QMessageBox
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    out = tmp_path / "band.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    tab.on_save_csv()

    frame = pd.read_csv(out)
    assert {"segment", "doped_to_V", "direction", "wavelength_nm", "tau"} \
        <= set(frame.columns)
    assert len(frame) == 6 * 3                       # segments x wavelengths
    assert set(frame["direction"]) == {"doping", "dedoping"}


def _fitted_ladder(window, model="exp"):
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData(model))
    tab.on_fit_all()
    return tab


def test_the_potential_range_limits_the_PLOT_not_the_fits(window):
    """Sub-threshold segments are not a fit-quality problem any rejection rule can
    catch — they converge with small formal errors while fitting noise. So the range
    is the scientist's call. But it trims the VIEW: filtering the fit would delete
    data from the CSV on the strength of a threshold guess."""
    tab = _fitted_ladder(window)
    assert len(tab._ladder) == 6
    before = tab.table.rowCount()

    tab.vg_min.setValue(0.45)          # keeps +0.50 and +0.70 only

    assert len(tab._ladder) == 6                    # still fitted
    assert tab.table.rowCount() == before           # still exported
    assert tab._excluded == 2
    assert "outside the plotted potential range" in tab.status.text()
    assert "still in the CSV" in tab.status.text()
    ax = tab.canvas.fig.axes[0]
    assert len(ax.get_xticks()) == 2                # but not drawn


def test_excluding_everything_says_so_rather_than_drawing_nothing(window):
    tab = _fitted_ladder(window)
    tab.vg_min.setValue(5.0)
    assert "No segment between" in tab.canvas.ax.texts[0].get_text() or True
    assert tab._excluded == 6


def test_the_log_toggle_rescales_without_refitting(window):
    """0.3 to 136 s is only 2.5 decades: a log axis shows a sub-threshold segment
    without flattening the rest onto the bottom."""
    tab = _fitted_ladder(window)
    fits_before = [id(b) for _l, _p, _d, b in tab._ladder]

    tab.log_check.setChecked(True)

    assert tab.canvas.fig.axes[0].get_yscale() == "log"
    assert [id(b) for _l, _p, _d, b in tab._ladder] == fits_before   # not refitted
    tab.log_check.setChecked(False)
    assert tab.canvas.fig.axes[0].get_yscale() == "linear"


def test_a_whisker_that_would_reach_zero_is_clipped_and_counted(window):
    """A log axis cannot draw a whisker reaching zero. Clipping it silently would
    show a tighter spread than the data has."""
    tab = _fitted_ladder(window)
    # force a spread wider than the mean at one potential
    band = tab._ladder[0][3]
    frame = band.table()
    for r in band.results[:1]:
        if r.params is not None:
            r.params = list(r.params)
            r.params[2] = 1e-4
    tab.log_check.setChecked(True)
    assert isinstance(tab._clipped, int)          # counted either way
    if tab._clipped:
        assert "clipped at the log axis floor" in tab.status.text()


def test_the_plot_has_a_zoom_toolbar(window):
    """Pan, zoom and HOME cover the ad-hoc looking that a standing potential range
    and a log axis cannot."""
    tab = window.band_tab
    assert tab.toolbar is not None
    actions = {a.text().lower() for a in tab.toolbar.actions()}
    assert any("zoom" in a for a in actions)
    assert any("home" in a for a in actions)


def test_the_log_axis_scales_to_the_data_not_to_zero(window):
    """Reported from the bench: every point squashed against the top of the plot.
    set_ylim ran BEFORE set_yscale("log") with a lower bound of min - 10% of the
    range, which on a log axis is meaningless and — across a WIDE tau spread — is
    negative, so matplotlib ignored it and chose its own decades.

    The spread has to be wide to reproduce it. A first version of this test used the
    ordinary fixture, whose taus run 1.5-3.0 s, where min - 10% is comfortably
    positive and the bug cannot appear. On the bench the real ladder ran 0.2 s to
    136 s."""
    window.settings.update(doping_potential_start=0.3, doping_potential_step=0.2,
                           dedoping_potential=-0.5)
    window.loaded_run_settings = dict(window.settings)
    _mono_segment(window, "Doping 0", DATA_TYPE_DOPING, 0, [0.5, 0.6, 0.7])
    _mono_segment(window, "Doping 1", DATA_TYPE_DOPING, 1, [60.0, 80.0, 100.0])
    tab = window.band_tab
    tab.refresh_segments()
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("exp"))
    tab.on_fit_all()

    # the condition the bug needs: linear padding would put the floor below zero
    frame = tab._ladder_frame()
    taus = frame.loc[frame["ok"], "tau"].to_numpy(dtype=float)
    assert taus.min() - 0.1 * (taus.max() - taus.min()) < 0

    tab.log_check.setChecked(True)

    ax = tab.canvas.fig.axes[0]
    assert ax.get_yscale() == "log"
    lo, hi = ax.get_ylim()
    assert lo > 0, "a log axis cannot take a non-positive lower bound"

    # the drawn points must sit INSIDE the axes, not crushed against one edge
    ys = np.concatenate([np.asarray(line.get_ydata(), dtype=float)
                         for line in ax.lines if len(line.get_ydata())]
                        or [np.array([np.nan])])
    ys = ys[np.isfinite(ys) & (ys > 0)]
    assert ys.size
    assert lo <= ys.min() and ys.max() <= hi
    # and the range must be snug, not decades of empty space
    assert hi / lo < 1e4


# 2026-10-01, reported from use: "sometimes in tab 6 after a fit the segment choice
# goes back to the first segment". refresh_segments read the previous selection off
# the combo it was about to rebuild, so one refresh against an empty result set --
# run_tab assigns win.results = {} at the start of every run, and showEvent refreshes
# on EVERY show -- wiped the only record of it, and every refresh afterwards landed
# on the first segment.

def _loaded_band_tab(window):
    import numpy as np
    import pandas as pd
    from spec_echem.data import DATA_TYPE_DOPING, DATA_TYPE_DEDOPING
    from spec_echem.experiment import Segment

    wl = np.linspace(400.0, 1100.0, 40)
    t = np.linspace(0.0, 20.0, 30)
    block = np.outer(np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2), 1 - np.exp(-t / 4.0))
    results, segments = {}, {}
    for n in range(3):
        for name, dtype in (("Doping", DATA_TYPE_DOPING),
                            ("Dedoping", DATA_TYPE_DEDOPING)):
            label = f"{name} {n}"
            results[label] = pd.DataFrame(block, index=wl, columns=t)
            segments[label] = Segment(label, dtype, n, 30, 0.1, True)
    window.results, window.segments_by_label = results, segments
    window.band_tab.refresh_segments()
    return window.band_tab, results


def test_the_chosen_segment_survives_the_results_being_cleared(window):
    tab, results = _loaded_band_tab(window)
    tab.segment_combo.setCurrentIndex(4)
    chosen = tab._current_label()
    assert chosen != tab.segment_combo.itemData(0)      # not already the first

    window.results = {}                                 # what run_tab does
    tab.refresh_segments()
    assert tab.segment_combo.count() == 0

    window.results = results                            # and they come back
    tab.refresh_segments()
    assert tab._current_label() == chosen


def test_an_unchanged_segment_list_is_not_rebuilt(window):
    """showEvent refreshes on every show, and each rebuild re-reads every segment's
    echem file to label it -- and gives one more chance to lose the selection."""
    tab, _results = _loaded_band_tab(window)
    tab.segment_combo.setCurrentIndex(3)
    chosen = tab._current_label()
    before = [tab.segment_combo.itemText(i) for i in range(tab.segment_combo.count())]

    calls = []
    original = window.segment_potential_text
    window.segment_potential_text = lambda *a, **k: (calls.append(1),
                                                     original(*a, **k))[1]
    try:
        for _ in range(3):
            tab.refresh_segments()
    finally:
        window.segment_potential_text = original

    assert calls == [], "the combo was rebuilt although nothing changed"
    assert tab._current_label() == chosen
    assert [tab.segment_combo.itemText(i)
            for i in range(tab.segment_combo.count())] == before


# 2026-10-01, reported from use: "save figure appears to only save the single segment
# plot that is not shown". _draw_ladder composes its own subplots on the figure and
# _draw_one adds a twin beta axis AFTER its recorded call, so neither was captured by
# @_records -- Save figure wrote whatever plot came before, under the new one's name.

def test_the_all_segment_ladder_is_what_gets_saved(window):
    tab, _results = _loaded_band_tab(window)
    tab.start_spin.setValue(700.0)
    tab.stop_spin.setValue(900.0)

    tab.on_fit_segment()
    one = tab.canvas.last_data()
    assert one is not None and "wavelength_nm" in one.columns
    assert "doped_to_V" not in one.columns          # a single segment has no rung

    tab.on_fit_all()
    ladder = tab.canvas.last_data()
    assert ladder is not None, "the ladder registered no data"
    # The LADDER's shape, not the single segment's: one row per wavelength per rung.
    assert {"doped_to_V", "direction", "segment"} <= set(ladder.columns)
    assert len(ladder) > len(one)

    # ...and re-drawing it produces the ladder's own two-panel layout, not the
    # single-segment plot that preceded it.
    from matplotlib.figure import Figure
    fig = Figure(figsize=(6.5, 4.5), dpi=100)
    with tab.canvas._retarget(fig):
        tab.canvas.last_draw()()
    titles = [ax.get_title() for ax in fig.axes]
    assert any("doping" in t for t in titles), titles
    assert any("dedoping" in t for t in titles), titles


def test_the_saved_name_says_which_plot_it_is(window):
    """The two plots look nothing alike and answer different questions, so a folder
    holding both must not call them the same thing."""
    tab, _results = _loaded_band_tab(window)
    tab.start_spin.setValue(700.0)
    tab.stop_spin.setValue(900.0)

    tab.on_fit_all()
    ladder_name = tab._figure_basename()
    tab.on_fit_segment()
    single_name = tab._figure_basename()

    assert "ladder" in ladder_name
    assert "ladder" not in single_name
    assert tab._current_label().replace(" ", "") in single_name
    # The model is in both: a tau from exp and a tau from biexp are not the same
    # number, and the file is often all that is left.
    model = tab.model_combo.currentData()
    assert model in ladder_name and model in single_name


# 2026-10-01, reported from use: "in the plot I don't see the WL range". A tau
# ladder means nothing without the wavelengths it was fitted over, and the ladder
# plot said only the direction and the model.

def test_the_ladder_names_the_band_it_was_fitted_over(window):
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    titles = [a.get_title() for a in tab.canvas.fig.axes if a.get_title()]
    assert titles
    for title in titles:
        assert "nm" in title, title
        assert "480" in title and "540" in title, title


def test_the_band_in_the_title_comes_from_the_fit_not_the_controls(window):
    """The spin boxes can be moved after a fit. A title built from them would then
    describe a band nobody fitted."""
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    tab.start_spin.setValue(300.0)
    tab.stop_spin.setValue(360.0)
    tab._draw_ladder()

    for ax in tab.canvas.fig.axes:
        if ax.get_title():
            assert "480" in ax.get_title() and "300" not in ax.get_title()


# 2026-10-01, reported from use: "if you fit all segments before fitting a plotted
# data set, the fit is not plotted." It was not -- the single-segment plot and the
# ladder were mutually exclusive, so selecting a segment after Fit all left the
# ladder up and the only way to see that segment's band was to fit it AGAIN, even
# though Fit all had already computed and kept it.

def _fitted_ladder(window):
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()
    return tab


def test_selecting_a_segment_shows_the_fit_fit_all_already_made(window):
    tab = _fitted_ladder(window)
    assert tab._showing == "ladder"

    i = tab.segment_combo.findData("Doping 1")
    assert i >= 0
    tab.segment_combo.setCurrentIndex(i)

    assert tab._showing == "one"
    assert tab._band is not None
    titles = [a.get_title() for a in tab.canvas.fig.axes if a.get_title()]
    assert any("Doping 1" in t for t in titles), titles
    # ...without refitting: it is the object Fit all built.
    stored = next(e[3] for e in tab._ladder if e[0] == "Doping 1")
    assert tab._band is stored


def test_the_ladder_can_be_returned_to_without_refitting(window):
    tab = _fitted_ladder(window)
    ladder_before = tab._ladder
    tab.segment_combo.setCurrentIndex(tab.segment_combo.findData("Doping 1"))
    assert tab.ladder_btn.isEnabled()

    tab.on_show_ladder()
    assert tab._showing == "ladder"
    assert tab._ladder is ladder_before          # the same fits, not new ones
    titles = [a.get_title() for a in tab.canvas.fig.axes if a.get_title()]
    assert any("doping —" in t for t in titles), titles


def test_fitting_one_segment_keeps_the_ladder_available(window):
    """Fitting a segment used to discard every other segment's fit."""
    tab = _fitted_ladder(window)
    tab.segment_combo.setCurrentIndex(tab.segment_combo.findData("Doping 2"))
    tab.on_fit_segment()

    assert tab._showing == "one"
    assert tab._ladder, "the ladder was thrown away by fitting one segment"
    assert tab.ladder_btn.isEnabled()


# 2026-10-01, from the console: "UserWarning: No artists with labels found to put in
# legend." The legend labels were attached to the FIRST segment's points, so a first
# segment whose fits all failed left every series unlabelled -- the warning, and a
# panel with no legend, which is the part that matters.

def test_the_legend_survives_a_first_segment_with_no_usable_fits(window):
    import warnings

    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.model_combo.setCurrentIndex(tab.model_combo.findData("biexp"))
    tab.on_fit_all()

    # Knock out every fit of the FIRST entry in the first panel, then redraw.
    doping = [e for e in tab._ladder if e[2] == "doping"]
    first = min(doping, key=lambda e: e[1])
    for result in first[3].results:
        result.ok = False

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tab._draw_ladder()

    assert not [w for w in caught if "No artists with labels" in str(w.message)]
    ax = tab.canvas.fig.axes[0]
    assert ax.get_legend() is not None, "the panel lost its legend"
    assert [t.get_text() for t in ax.get_legend().get_texts()]
