"""Every step on one plot (spec_echem/staircase_views.py) and the GITT tab."""
import os

import numpy as np
import pytest

from spec_echem import staircase_views as sv

h5py = pytest.importorskip("h5py")


def _write(path, steps, key="gitt_steps_json", wl=None, spectra=None, run_clock=True):
    """A minimal staircase h5: per step (t, e, i), optional spectra per step."""
    import json
    with h5py.File(path, "w") as f:
        f.attrs[key] = json.dumps([dict(index=k, role="titration", current_set_A=1e-6)
                                   for k in range(len(steps))])
        if wl is not None:
            f["wavelength"] = wl
        offset = 0.0
        for k, (t, e, i) in enumerate(steps):
            g = f.create_group(str(k))
            ech = g.create_group("echem")
            ech["time"], ech["potential"], ech["current"] = t, e, i
            if run_clock:
                ech["time_potentiostat"] = t + offset
            offset += float(t[-1]) + 0.3
            if spectra is not None:
                g["absorbance_vs_time"] = spectra[k]


def test_the_traces_keep_both_clocks(tmp_path):
    t = np.linspace(0.05, 10, 50)
    p = tmp_path / "r.h5"
    _write(p, [(t, t * 0 + 0.1 * k, t * 0 + 1e-6) for k in range(3)])
    tr = sv.step_traces(p)
    assert [x["index"] for x in tr] == [0, 1, 2]
    assert tr[1]["t"][0] == pytest.approx(0.05)                 # its own clock
    assert tr[1]["t_run"][0] == pytest.approx(10.0 + 0.3 + 0.05)  # the run's
    assert tr[0]["setpoint"] == pytest.approx(1e-6)


def test_without_a_run_clock_the_steps_are_laid_end_to_end(tmp_path):
    t = np.linspace(0.0, 5, 20)
    p = tmp_path / "r.h5"
    _write(p, [(t, t, t), (t, t, t)], run_clock=False)
    tr = sv.step_traces(p)
    assert tr[1]["t_run"][0] == pytest.approx(5.0)


def test_the_end_spectra_and_their_reference(tmp_path):
    wl = np.linspace(400, 1000, 61)
    base = np.full(wl.size, 0.6)
    a = [np.stack([base, base], axis=1)]
    for k in (1, 2):
        end = base - 0.1 * k * np.exp(-((wl - 520) / 40) ** 2) \
            + 0.08 * k * np.exp(-((wl - 780) / 60) ** 2)
        a.append(np.stack([base, end], axis=1))
    t = np.linspace(0, 1, 5)
    p = tmp_path / "r.h5"
    _write(p, [(t, t, t)] * 3, wl=wl, spectra=a)
    w, ref, ends = sv.step_end_spectra(p)
    assert np.allclose(ref, base)
    assert [k for k, _r, _a in ends] == [0, 1, 2]
    fall, rise = sv.band_wavelengths(w, ends[-1][2] - ref)
    assert fall == pytest.approx(520, abs=10)
    assert rise == pytest.approx(780, abs=10)


def test_no_change_means_no_band():
    wl = np.linspace(400, 1000, 61)
    assert sv.band_wavelengths(wl, np.zeros(wl.size)) == (None, None)


# --- The GITT tab ---------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("qtpy")
    from qtpy.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def gitt_win(app, tmp_path):
    from unittest.mock import patch
    import gui.main_window as _mw
    from tests.test_gitt_run import _run, quick
    _steps, path, _record = _run(tmp_path, quick())
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        w = _mw.MainWindow()
    w.run_folder = path.parent
    w.settings.update(film_thickness_nm=100.0, film_area_cm2=1.0)
    yield w
    w.close()


def test_the_gitt_tab_tabulates_every_step_and_draws_every_view(gitt_win):
    from spec_echem import gitt_analysis as ga
    panel = gitt_win.analysis_tab.gitt_panel
    panel.refresh()
    assert panel.table.rowCount() == 3 == len(panel.results)    # rest + 2 pulses
    names = [n for n, _u in ga.TABLE_COLUMNS]
    assert panel.table.columnCount() == len(names)
    for i in range(panel.view_combo.count()):
        panel.view_combo.setCurrentIndex(i)
        for j in range(panel.step_combo.count()):
            panel.step_combo.setCurrentIndex(j)
            panel.redraw()
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("titration"))
    panel.redraw()
    line = panel.canvas.ax.get_lines()[0]
    assert len(line.get_xdata()) == 3              # the rest at Q = 0, then 2 pulses
    assert line.get_xdata()[0] == 0.0


def test_the_gitt_csv_carries_its_provenance(gitt_win, tmp_path, monkeypatch):
    import pandas as pd
    from qtpy.QtWidgets import QFileDialog
    from spec_echem import gitt_analysis as ga
    panel = gitt_win.analysis_tab.gitt_panel
    panel.refresh()
    out = tmp_path / "g.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel.on_save_table()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# run: 20261029_gitt")
    assert "film 100 nm" in text
    frame = pd.read_csv(out, comment="#")
    assert list(frame.columns) == [n for n, _u in ga.TABLE_COLUMNS] and len(frame) == 3
