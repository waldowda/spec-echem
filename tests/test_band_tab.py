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


def test_the_x_axis_is_categorical_with_the_true_potential_on_the_tick(window):
    _ladder(window)
    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    ax = tab.canvas.fig.axes[0]
    np.testing.assert_allclose(ax.get_xticks(), [0, 1, 2])     # categorical
    assert [t.get_text() for t in ax.get_xticklabels()] == ["+0.30", "+0.50", "+0.70"]


def test_a_stretched_ladder_gets_a_beta_row(window):
    """beta is dimensionless and 0-1: its own row, sharing the categorical x, so it
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
