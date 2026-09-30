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


def test_segments_with_no_ladder_potential_are_named_not_dropped(window):
    """A CV sweeps and a pre-dedope is a single baseline — neither is a rung."""
    _ladder(window)
    from spec_echem.data import DATA_TYPE_CV
    _segment(window, "CV", DATA_TYPE_CV, 0, [2.0, 2.2, 2.4])
    window.band_tab.refresh_segments()

    tab = window.band_tab
    tab.start_spin.setRange(0.0, 5000.0); tab.stop_spin.setRange(0.0, 5000.0)
    tab.start_spin.setValue(480.0); tab.stop_spin.setValue(540.0)
    tab.on_fit_all()

    assert "CV" in tab.status.text()
    assert "no ladder potential" in tab.status.text()
    assert len(tab._ladder) == 6          # the six real rungs still fitted


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
