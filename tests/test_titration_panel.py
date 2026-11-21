"""The PITT tab under the Analysis fit plot: a PITT staircase's dQ/dE, Q(E),
step current and impedance, drawn from the run's _pitt.h5 (spec_echem.pitt_analysis)."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")
pytest.importorskip("h5py")

from qtpy.QtWidgets import QApplication                         # noqa: E402

from spec_echem import pitt_analysis as pa                       # noqa: E402
from spec_echem.acquisition import acquire_pitt                  # noqa: E402
from spec_echem.data import write_pitt_h5                        # noqa: E402
from spec_echem.fakes import FakeEreisPotentiostat, FakeSpectrometer  # noqa: E402
from spec_echem.pitt import pitt_plan                            # noqa: E402
from tests.test_pitt_run import Clock, ClockedSpectrometer, settings  # noqa: E402

C = 1e-3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _staircase(tmp_path, folder):
    """0 -> 0.4 V in 0.1 V steps and back, impedance on every other potential."""
    s = settings(pitt_cutoff_enabled=False, pitt_max_hold_s=10.0, pitt_stop_v=0.4,
                 pitt_return=True, pitt_eis_enabled=True, pitt_eis_every_n=2,
                 pitt_eis_f_high_hz=1e4, pitt_eis_f_low_hz=1.0,
                 pitt_eis_points_per_decade=5)
    clock = Clock()
    pot = FakeEreisPotentiostat(s, point_s=0.5, clock=clock, r_series=1e3, c=C)
    record = acquire_pitt(ClockedSpectrometer(clock), pot, pitt_plan(s), s,
                          clock=clock, sleep=clock.sleep)
    wl = FakeSpectrometer().wavelengths()[1]
    return write_pitt_h5(record, np.full(wl.size, 100.0), np.full(wl.size, 4e4), wl,
                         tmp_path, folder, settings=s)


@pytest.fixture
def win(app, tmp_path):
    from unittest.mock import patch
    import gui.main_window as _mw
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        w = _mw.MainWindow()
    folder = "20261117_pitt"
    _staircase(tmp_path, folder)
    w.run_folder = tmp_path / folder
    w.settings.update(film_thickness_nm=100.0, film_area_cm2=1.0)
    yield w
    w.close()


def _panel(w):
    panel = w.analysis_tab.titration_panel
    panel.refresh()
    return panel


def test_it_sits_beside_kinetics_and_charge(win):
    tabs = win.analysis_tab.lower_tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "Kinetics", "Charge", "PITT", "GITT"]


def test_the_table_has_a_row_per_step_and_the_rc_capacitance(win):
    panel = _panel(win)
    assert panel.table.rowCount() == 9 == len(panel.results)       # 5 up, 4 down
    rows = panel.rows()
    assert rows[0]["C_F"] is None                                   # the first step
    for row in rows[1:]:
        assert row["C_head_F"] == pytest.approx(C, rel=0.01)
        assert row["g_per_eV_cm3"] is not None                      # film size known


def test_every_view_draws_without_hiding_anything(win):
    panel = _panel(win)
    for i in range(panel.view_combo.count()):
        panel.view_combo.setCurrentIndex(i)
        for j in range(panel.step_combo.count()):
            panel.step_combo.setCurrentIndex(j)
            panel.redraw()
    panel.view_combo.setCurrentIndex(0)
    panel.redraw()
    labels = [line.get_label() for line in panel.canvas.ax.get_lines()]
    assert any("forward" in x for x in labels) and any("return" in x for x in labels)


def test_the_all_steps_views_draw_one_curve_per_step_in_run_order(win):
    """Requested 2026-11-20: every step on one plot, coloured in run order."""
    panel = _panel(win)
    for view in ("timeline", "overlay"):
        panel.view_combo.setCurrentIndex(panel.view_combo.findData(view))
        panel.redraw()
        lines = panel.canvas.ax.get_lines()
        assert len(lines) == len(panel.results), view
        assert len({line.get_color() if isinstance(line.get_color(), str)
                    else tuple(line.get_color()) for line in lines}) == len(lines)
        assert lines[0].get_label().startswith("0: +0.000 V")
        # Every step drawn; at most five named in the legend, first and last kept.
        named = [ln.get_label() for ln in lines if not ln.get_label().startswith("_")]
        assert len(named) <= 5 and named[-1] == lines[-1].get_label()
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("timeline"))
    panel.redraw()
    # (The fake's first step, 0 V from 0 V, draws no current: nothing on a log axis.)
    xs = [x for x in (line.get_xdata() for line in panel.canvas.ax.get_lines())
          if len(x)]
    assert len(xs) >= len(panel.results) - 1
    # The run's clock: each step starts after the one before it ends.
    assert all(b[0] >= a[-1] for a, b in zip(xs, xs[1:]))


def test_a_step_without_a_sweep_says_so_and_names_the_swept_ones(win):
    panel = _panel(win)
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("nyquist"))
    unswept = next(r.index for r in panel.results if r.sweep is None)
    panel.step_combo.setCurrentIndex(panel.step_combo.findData(unswept))
    panel.redraw()
    texts = " ".join(" ".join(t.get_text().split()) for t in panel.canvas.ax.texts
                     + panel.canvas.fig.texts)
    assert "no impedance sweep" in texts and "Swept steps: 0, 2, 4" in texts


def test_a_flagged_step_keeps_its_number_with_a_question_mark(win):
    panel = _panel(win)
    panel.results[2].reasons.append("a test concern")
    panel._fill_table()
    names = [n for n, _u in pa.TABLE_COLUMNS]
    item = panel.table.item(2, names.index("C_F"))
    assert item.text().startswith("? ")
    assert panel.table.item(2, names.index("reasons")).text() == "a test concern"
    # Step 2 was swept: the impedance's own concern is beside it, not on its dQ.
    assert "resistive" in panel.table.item(2, names.index("Z_concerns")).text()
    assert not panel.table.item(4, names.index("C_F")).text().startswith("? ")


def test_the_csv_carries_its_provenance_and_every_column(win, tmp_path, monkeypatch):
    import pandas as pd
    from qtpy.QtWidgets import QFileDialog
    panel = _panel(win)
    out = tmp_path / "t.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel.on_save_table()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# run: 20261117_pitt")
    assert "# hysteresis:" in text and "film 100 nm x 1 cm2" in text
    frame = pd.read_csv(out, comment="#")
    assert list(frame.columns) == [n for n, _u in pa.TABLE_COLUMNS]
    assert len(frame) == 9


def test_a_run_without_a_pitt_says_so(app, tmp_path):
    from unittest.mock import patch
    import gui.main_window as _mw
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        w = _mw.MainWindow()
    try:
        w.run_folder = tmp_path / "20261117_cv"
        panel = _panel(w)
        assert panel.results == [] and panel.table.rowCount() == 0
    finally:
        w.close()
