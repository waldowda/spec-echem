"""The Charge tab under the Analysis fit plot (requested 2026-11-06)."""
import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtWidgets import QApplication                         # noqa: E402

from spec_echem.charge import E_CHARGE_C                         # noqa: E402
from spec_echem.data import (DATA_TYPE_DEDOPING, DATA_TYPE_DOPING,  # noqa: E402
                             DATA_TYPE_PREDEDOPING, EchemData, write_echem_file)
from spec_echem.experiment import Segment                        # noqa: E402

T = np.arange(0.0, 60.0 + 1e-9, 0.1)
WL = np.linspace(400.0, 1000.0, 61)
PEAK = 600.0
I0, TAU = 50e-6, 2.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _spectra(a_peak):
    """Absorbance block: a neutral band at 600 nm whose height follows a_peak(t)."""
    shape = np.exp(-0.5 * ((WL - PEAK) / 50.0) ** 2)
    return pd.DataFrame(np.outer(shape, a_peak), index=WL, columns=T)


@pytest.fixture
def run(app, tmp_path):
    """Pre-dedoping, then two doping/dedoping pairs at +0.4 and +0.6 V.

    Doping n bleaches the 600 nm band from 1.0 to 1.0 - (0.3 + 0.1 n) with tau = 2 s
    and passes I0 * exp(-t/tau) * (1 + n); dedoping returns 90% of that charge."""
    from unittest.mock import patch
    import gui.main_window as _mw
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        win = _mw.MainWindow()
    folder = "20261106_run"
    win.run_folder = tmp_path / folder
    results, segs = {}, {}

    results["Pre-dedoping"] = _spectra(np.ones(T.size))
    segs["Pre-dedoping"] = Segment("Pre-dedoping", DATA_TYPE_PREDEDOPING, 0, T.size,
                                   0.1, True)
    write_echem_file(EchemData(time=T, potential=np.full(T.size, -0.2),
                               current=np.zeros(T.size)),
                     DATA_TYPE_PREDEDOPING, 0, tmp_path, folder)
    for n, volts in enumerate((0.4, 0.6)):
        depth = 0.3 + 0.1 * n
        decay = np.exp(-T / TAU)
        i = I0 * (1 + n) * decay
        for kind, dtype, a, cur in (
                ("Doping", DATA_TYPE_DOPING, 1.0 - depth * (1 - decay), i),
                ("Dedoping", DATA_TYPE_DEDOPING, 1.0 - depth * decay, -0.9 * i)):
            label = f"{kind} {n}"
            results[label] = _spectra(a)
            segs[label] = Segment(label, dtype, n, T.size, 0.1, True)
            write_echem_file(EchemData(time=T, potential=np.full(T.size, volts if
                                       dtype == DATA_TYPE_DOPING else -0.2),
                                       current=cur), dtype, n, tmp_path, folder)
    win.results, win.segments_by_label = results, segs
    win.settings.update(film_thickness_nm=100.0, film_area_cm2=2.0)
    win.analysis_tab.refresh_segments()
    yield win
    win.close()


def _panel(win):
    panel = win.analysis_tab.charge_panel
    panel.refresh()
    return panel


def test_every_step_is_in_the_table_with_its_charge(run):
    panel = _panel(run)
    rows = {r["segment"]: r for r in panel.rows()}
    assert sorted(rows) == ["Dedoping 0", "Dedoping 1", "Doping 0", "Doping 1"]
    # Integral of I0 (1+n) exp(-t/tau) over 60 s = I0 (1+n) tau, in mC.
    assert rows["Doping 1"]["Q_raw_mC"] == pytest.approx(1e3 * I0 * 2 * TAU, rel=1e-3)
    assert rows["Dedoping 1"]["Q_raw_mC"] == pytest.approx(-0.9e3 * I0 * 2 * TAU,
                                                           rel=1e-3)
    # The dedoping step sits at the potential the film was DOPED to.
    assert rows["Dedoping 1"]["doped_to_V"] == pytest.approx(0.6, abs=1e-6)


def test_carrier_density_uses_the_film_size(run):
    panel = _panel(run)
    row = {r["segment"]: r for r in panel.rows()}["Doping 0"]
    volume = 2.0 * 100.0 * 1e-7
    assert row["carriers_corrected_cm3"] == pytest.approx(
        1e-3 * row["Q_corrected_mC"] / (E_CHARGE_C * volume), rel=1e-9)


def test_bleach_is_read_at_the_neutral_peak_against_the_pre_dedoping_spectrum(run):
    panel = _panel(run)
    assert panel.wl_spin.value() == pytest.approx(PEAK, abs=10.0)
    rows = {r["segment"]: r for r in panel.rows()}
    assert rows["Doping 0"]["bleach"] == pytest.approx(0.3, abs=0.01)
    assert rows["Doping 1"]["bleach"] == pytest.approx(0.4, abs=0.01)
    assert rows["Dedoping 1"]["bleach"] == pytest.approx(0.0, abs=0.01)
    assert any("end of pre-dedoping" in n for n in panel._notes)


def test_a_run_s_own_film_size_wins_over_the_form(run):
    run.loaded_run_settings = {"film_thickness_nm": 200.0, "film_area_cm2": 1.0}
    panel = _panel(run)
    assert any("200 nm x 1 cm2 from this run" in n for n in panel._notes)
    run.loaded_run_settings = {}          # an older run that recorded none
    panel = _panel(run)
    assert any("Parameters tab" in n for n in panel._notes)


def test_a_step_shorter_than_5_tau_is_ringed_not_removed(run):
    from spec_echem.analysis import FitResult
    tab = run.analysis_tab
    tab.wl_auto.setChecked(False)          # fit the band itself, not an auto probe
    tab.wavelength_spin.setValue(PEAK)
    tab.on_fit_all()                       # the real fits: tau = 2 s, all settled
    panel = _panel(run)
    assert all(r["at_equilibrium"] for r in panel.rows()), panel.rows()
    # A passed fit with tau = 20 s on one step: 60 s is under 5 tau.
    tab._fits["Doping 1"]["absorbance"] = FitResult(
        "exp", params=np.array([0.6, 0.4, 20.0]), sd=np.array([0.001] * 3),
        cov=np.diag([1e-6] * 3), ok=True, n=600)
    panel = _panel(run)
    rows = {r["segment"]: r for r in panel.rows()}
    assert not rows["Doping 1"]["at_equilibrium"]
    assert "under 5 x absorbance tau" in rows["Doping 1"]["reason"]
    assert rows["Doping 0"]["at_equilibrium"]
    labels = [l.get_label() for l in panel.canvas.ax.get_lines()]
    assert any("not at equilibrium" in l for l in labels)
    assert len(panel.rows()) == 4          # flagged, never dropped
    panel.tau_spin.setValue(2.0)           # the scientist's call: 2 tau is enough
    assert {r["segment"]: r for r in panel.rows()}["Doping 1"]["at_equilibrium"]


def test_every_view_draws_and_names_its_export(run):
    panel = _panel(run)
    for i in range(panel.view_combo.count()):
        panel.view_combo.setCurrentIndex(i)
        key = panel.view_combo.currentData()
        assert panel.canvas.ax.get_lines(), key
        assert panel.figure_basename() == f"charge_{key}"
        assert panel.canvas.last_data() is not None


def test_reversibility_is_charge_out_over_charge_in(run):
    panel = _panel(run)
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("reversibility"))
    data = panel.canvas.last_data()
    ratios = data.loc[~data["series"].str.contains("equilibrium"), "y"]
    assert np.allclose(ratios, 0.9, atol=1e-3)


def test_the_table_saves_with_its_notes(run, tmp_path, monkeypatch):
    from qtpy.QtWidgets import QFileDialog
    panel = _panel(run)
    out = tmp_path / "charge.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel.on_save_table()
    text = out.read_text()
    assert text.startswith("# film 100 nm x 2 cm2")
    frame = pd.read_csv(out, comment="#")
    assert len(frame) == 4 and "bleach" in frame.columns


def test_the_bleach_plot_has_doping_steps_only(run):
    """A dedoping step ends neutral; drawn at the charge it removed it would put a
    neutral film at a high carrier density."""
    panel = _panel(run)
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("bleach"))
    series = set(panel.canvas.last_data()["series"])
    assert series == {"doping"}
    assert "Dedoping 0" in {r["segment"] for r in panel.rows()}     # still tabled


def test_charge_density_views_use_q_over_volume(run):
    panel = _panel(run)
    rows = {r["segment"]: r for r in panel.rows()}
    volume = 2.0 * 100.0 * 1e-7
    assert rows["Doping 1"]["charge_density_corrected_C_cm3"] == pytest.approx(
        1e-3 * rows["Doping 1"]["Q_corrected_mC"] / volume, rel=1e-9)
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("charge_density"))
    assert "C cm" in panel.canvas.ax.get_ylabel()
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("bleach_charge"))
    data = panel.canvas.last_data()
    assert set(data["series"]) == {"doping"}
    expected = sorted(r["charge_density_corrected_C_cm3"] for r in rows.values()
                      if r["direction"] == "doping")
    assert np.allclose(sorted(data["x"]), expected)
    assert "Charge density" in panel.canvas.ax.get_xlabel()


# --- uncertainties (requested 2026-11-08) ---------------------------------------

def test_corrected_charge_carries_error_bars_into_the_plot_and_csv_data(run):
    panel = _panel(run)
    rows = {r["segment"]: r for r in panel.rows()}
    assert all(r["Q_u_mC"] is not None and r["Q_u_mC"] >= 0 for r in rows.values())
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("charge"))
    data = panel.canvas.last_data()
    corrected = data[data["series"] == "doping: leak-corrected"]
    assert np.allclose(sorted(corrected["y_err"]),
                       sorted(r["Q_u_mC"] for r in rows.values()
                              if r["direction"] == "doping"))
    assert data.loc[data["series"] == "doping: raw", "y_err"].isna().all()
    for view in ("density", "charge_density", "reversibility"):
        panel.view_combo.setCurrentIndex(panel.view_combo.findData(view))
        assert "y_err" in panel.canvas.last_data().columns, view
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("bleach_charge"))
    data = panel.canvas.last_data()
    assert {"x_err", "y_err"} <= set(data.columns)
    assert any(n.startswith("error bars:") for n in panel._notes)


def test_film_size_uncertainty_is_a_note_not_an_error_bar(run):
    panel = _panel(run)
    # Two significant figures: 100 -> +-5 nm (5%), 2.0 -> +-0.05 cm2 (2.5%): 5.6%.
    assert any("film volume +-5.6%" in n and "2 sig. figs" in n for n in panel._notes)
    run.settings.update(film_thickness_u_nm=10.0, film_area_u_cm2=0.2)
    panel = _panel(run)
    assert any("film volume +-14.1%" in n and "sig. figs" not in n
               for n in panel._notes)
    row = {r["segment"]: r for r in panel.rows()}["Doping 0"]
    # The density's own +- comes from the charge alone.
    assert row["charge_density_u_C_cm3"] == pytest.approx(
        1e-3 * row["Q_u_mC"] / (2.0 * 100.0 * 1e-7), rel=1e-9)


def test_head_charge_comes_from_the_run_log_and_stays_out_of_q(run):
    panel = _panel(run)
    assert all(r["Q_head_max_mC"] is None for r in panel.rows())
    assert any("not estimated" in n for n in panel._notes)
    folder = run.run_folder
    (folder / f"{folder.name}_log.log").write_text(
        "2026-11-06 10:00:00,000  INFO    Doping 1 timing, from cell ON: trigger "
        "edge +0.020 s | first Ei sample +0.100 s.\n", encoding="utf-8")
    panel = _panel(run)
    rows = {r["segment"]: r for r in panel.rows()}
    # I0 (1+n) exp(-t/tau) run back 0.1 s: I0 * 2 * tau * (exp(0.1/tau) - 1).
    assert rows["Doping 1"]["Q_head_max_mC"] == pytest.approx(
        1e3 * I0 * 2 * TAU * (np.exp(0.1 / TAU) - 1), rel=1e-3)
    assert rows["Doping 0"]["Q_head_max_mC"] is None
    assert rows["Doping 1"]["Q_raw_mC"] == pytest.approx(1e3 * I0 * 2 * TAU, rel=1e-3)
    assert any("upper bound, NOT in Q" in n for n in panel._notes)
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("reversibility"))
    assert "reads low" in panel.canvas._last_draw[2]["footnote"]


def test_dedoping_ends_give_the_bleach_floor(run):
    panel = _panel(run)
    ends = [r["bleach"] for r in panel.rows() if r["direction"] == "dedoping"]
    assert (f"dedoping steps end at bleach {min(ends):.3f} to {max(ends):.3f}"
            in "; ".join(panel._notes))
    assert all(r["bleach_u"] is not None for r in panel.rows())


def test_a0_uncertainty_comes_from_the_pre_dedoping_scatter(run):
    rng = np.random.default_rng(2)
    pre = run.results["Pre-dedoping"]
    run.results["Pre-dedoping"] = pre + rng.normal(0, 0.01, pre.shape)
    panel = _panel(run)
    note = next(n for n in panel._notes if n.startswith("A0 = "))
    assert "+- " in note
    row = {r["segment"]: r for r in panel.rows()}["Doping 0"]
    # The doping tail itself is noise-free here, so all of bleach_u is A0's share.
    assert row["bleach_u"] > 0.001
