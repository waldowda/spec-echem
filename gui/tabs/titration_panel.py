"""The Titration tab under the Analysis fit plot: a PITT staircase's charge per step.

Beside Kinetics (how fast each step switched) and Charge (the doping ladder's
charge), this reads the run's `{folder}_pitt.h5` through spec_echem.pitt_analysis
and draws dQ/dE against potential, the running charge Q(E), one step's current, or
one swept step's impedance. The table under the plot has every number for every
step, with the reasons beside any it objects to; nothing is hidden.

The film size is the Charge tab's (its Edit... box), so both tabs use one value.
"""
import pathlib

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                            QMessageBox, QPushButton, QSplitter, QTableWidget,
                            QTableWidgetItem, QVBoxLayout, QWidget)

from spec_echem import pitt_analysis as pa
from spec_echem.data import DATA_TYPE_PITT, h5_path, read_impedance_h5
from spec_echem.pitt import ROLE_FORWARD, ROLE_RETURN
from gui.widgets.figure_dialog import EXPORT_LABEL, EXPORT_TIP, open_figure_dialog
from gui.widgets.plot_canvas import MplCanvas
from gui.tabs import staircase_draw as sd
from spec_echem import staircase_views as sv

VIEWS = (
    ("dQ/dE vs potential", "dqde"),
    ("Charge Q(E)", "q"),
    ("Step current", "step"),
    ("All steps: current vs run time", "timeline"),
    ("All steps: overlaid from each start", "overlay"),
    ("All steps: spectra at each step's end", "spectra"),
    ("All steps: bands vs potential", "bands"),
    ("Impedance: Nyquist", "nyquist"),
    ("Impedance: C′ vs frequency", "cprime"),
)

LEG_STYLE = {
    ROLE_FORWARD: {"color": "#1f77b4", "marker": "o", "linestyle": "-"},
    ROLE_RETURN: {"color": "#d62728", "marker": "s", "linestyle": "--"},
}
RING = {"marker": "o", "ms": 11, "mfc": "none", "mec": "#e07b00", "mew": 1.6,
        "linestyle": "none"}


class TitrationPanel(QWidget):
    """dQ/dE, Q(E), a step's current and a swept step's impedance for a PITT."""

    def __init__(self, analysis_tab):
        super().__init__(analysis_tab)
        self.tab = analysis_tab
        self.win = analysis_tab.win
        self.results = []
        self._key = None                  # (path, mtime, film) the results are for
        self._stale = True
        self._notes = []
        self._build()

    # --- layout ------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Plot:"))
        self.view_combo = QComboBox(self)
        for text, key in VIEWS:
            self.view_combo.addItem(text, key)
        self.view_combo.currentIndexChanged.connect(self.redraw)
        row.addWidget(self.view_combo)
        row.addSpacing(16)
        row.addWidget(QLabel("Step:"))
        self.step_combo = QComboBox(self)
        self.step_combo.setToolTip("The step drawn by the Step current and "
                                   "Impedance views.")
        self.step_combo.currentIndexChanged.connect(self.redraw)
        row.addWidget(self.step_combo)
        row.addSpacing(16)
        self.head_check = QCheckBox("include the charge before the first sample", self)
        self.head_check.setChecked(True)
        self.head_check.setToolTip(
            "No current sample covers the time between the setpoint change and the\n"
            "first sample, while the current is largest. Ticked, that charge is\n"
            "estimated by running an exponential through the first samples back to\n"
            "the step's start, and added. The sampled-only values are drawn faint\n"
            "either way, and both are in the table.")
        self.head_check.toggled.connect(self.redraw)
        row.addWidget(self.head_check)
        row.addStretch()
        layout.addLayout(row)

        split = QSplitter(Qt.Vertical, self)
        plot_box = QWidget(self)
        plot_layout = QVBoxLayout(plot_box)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = MplCanvas(self, xlabel="Potential (V)", ylabel="dQ/dE (F)")
        plot_layout.addWidget(self.canvas)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.table_btn = QPushButton("Save table (CSV)…", self)
        self.table_btn.setToolTip("Every column for every step, as CSV.")
        self.table_btn.clicked.connect(self.on_save_table)
        buttons.addWidget(self.table_btn)
        export = QPushButton(EXPORT_LABEL, self)
        export.setToolTip(EXPORT_TIP)
        export.clicked.connect(
            lambda: open_figure_dialog(self, self.canvas, self.win,
                                       self.figure_basename(), "Export titration",
                                       context=lambda: list(self._notes)))
        buttons.addWidget(export)
        plot_layout.addLayout(buttons)
        split.addWidget(plot_box)

        names = [name for name, _unit in pa.TABLE_COLUMNS]
        self.table = QTableWidget(0, len(names), self)
        self.table.setHorizontalHeaderLabels(names)
        for c, (name, unit) in enumerate(pa.TABLE_COLUMNS):
            if unit:
                self.table.horizontalHeaderItem(c).setToolTip(unit)
        self.table.verticalHeader().setVisible(False)
        split.addWidget(self.table)
        layout.addWidget(split)
        self.canvas.show_message("Load a run with a PITT to see its titration.")

    # --- when to recompute ---------------------------------------------------

    def request_refresh(self):
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def showEvent(self, event):
        # Always: the film size may have been edited on the Charge tab, and an
        # unchanged file and film only refills the table (compute() caches).
        super().showEvent(event)
        self.refresh()

    # --- data ----------------------------------------------------------------

    def _path(self):
        folder = getattr(self.win, "run_folder", None)
        if folder is None:
            return None
        path = pathlib.Path(h5_path(folder, DATA_TYPE_PITT))
        return path if path.exists() else None

    def _film(self):
        """(thickness_nm, area_cm2, where), from the Charge tab's film box."""
        try:
            thickness, area, _ut, _ua, where = self.tab.charge_panel._film()
        except Exception:  # noqa: BLE001 -- no film size is a state, not a crash
            return 0.0, 0.0, "not set"
        return thickness, area, where

    def compute(self):
        """Analyse the run's PITT, unless the file and film are as last time."""
        self._stale = False
        path = self._path()
        if path is None:
            self.results, self._key = [], None
            return self.results
        thickness, area, where = self._film()
        key = (str(path), path.stat().st_mtime, thickness, area)
        if key != self._key:
            self.results = pa.analyse_staircase(path, area_cm2=area or None,
                                                thickness_nm=thickness or None)
            self._key = key
            self._fill_steps()
        self._film_where = (thickness, area, where)
        self._notes = self._make_notes(path)
        return self.results

    def _make_notes(self, path):
        thickness, area, where = self._film_where
        notes = [f"run: {path.parent.name}", "PITT staircase analysis "
                 "(spec_echem.pitt_analysis); derived, not stored in the h5"]
        if thickness and area:
            notes.append(f"film {thickness:g} nm x {area:g} cm2 ({where})")
        else:
            notes.append("no film size: C in farads, no g(E)")
        notes.append("dQ: the step's own tail current as background; dQ_ramp (a "
                     "background rising from the previous step's) is the far bound")
        notes.append("Q_head: before the first current sample, extrapolated; NOT in "
                     "dQ_u")
        hyst = pa.staircase_hysteresis(self.results)
        if hyst:
            parts = []
            if hyst["closure"] is not None:
                parts.append(f"closure {100 * hyst['closure']:.1f}% of the forward "
                             f"charge left in")
            if hyst["q_gap_fraction"] is not None:
                parts.append(f"largest forward/return gap "
                             f"{100 * hyst['q_gap_fraction']:.1f}%")
            if hyst["centre_shift_v"] is not None:
                parts.append(f"return centre {1e3 * hyst['centre_shift_v']:+.1f} mV "
                             f"from forward")
            if parts:
                notes.append("hysteresis: " + ", ".join(parts))
        return notes

    def _fill_steps(self):
        previous = self.step_combo.currentData()
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        for r in self.results:
            swept = " [Z]" if r.sweep is not None else ""
            e = r.potential_set if r.potential_set is not None else float("nan")
            self.step_combo.addItem(f"{r.index}: {e:+.3f} V, {r.role}{swept}", r.index)
        if previous is not None:
            i = self.step_combo.findData(previous)
            if i >= 0:
                self.step_combo.setCurrentIndex(i)
        self.step_combo.blockSignals(False)

    def _selected(self):
        k = self.step_combo.currentData()
        for r in self.results:
            if r.index == k:
                return r
        return None

    # --- drawing ---------------------------------------------------------------

    def refresh(self, *_):
        self.compute()
        self._fill_table()
        self.redraw()

    def redraw(self, *_):
        if not self.results:
            self.canvas.show_message("This run has no PITT staircase.")
            return
        view = self.view_combo.currentData()
        if view == "dqde":
            self._draw_dqde()
        elif view == "q":
            self._draw_q()
        elif view == "step":
            self._draw_step()
        elif view in ("timeline", "overlay", "spectra", "bands"):
            self._draw_all_steps(view)
        else:
            self._draw_impedance(view)

    def _dqde_values(self, r, head):
        """(y, y_err, label unit) for one step: g with a film size, else C."""
        if r.g is not None:
            y = r.g_head if head and r.g_head is not None else r.g
            scale = r.g / r.c if r.c else None
            err = r.c_u * scale if (r.c_u is not None and scale) else None
            return y, err
        y = r.c_head if head and r.c_head is not None else r.c
        return y, r.c_u

    def _draw_dqde(self):
        head = self.head_check.isChecked()
        with_film = any(r.g is not None for r in self.results)
        curves, flagged = [], 0
        for role in (ROLE_FORWARD, ROLE_RETURN):
            rows = [r for r in self.results if r.role == role and r.c is not None]
            if not rows:
                continue
            rows.sort(key=lambda r: r.e_mid)
            xs = [r.e_mid for r in rows]
            vals = [self._dqde_values(r, head) for r in rows]
            ys = [v[0] for v in vals]
            errs = [v[1] for v in vals]
            if head:
                sampled = [self._dqde_values(r, False)[0] for r in rows]
                curves.append((xs, sampled, f"{role}: sampled only",
                               {**LEG_STYLE[role], "alpha": 0.35}))
            label = f"{role}" + (": with the head" if head else ": sampled")
            style = dict(LEG_STYLE[role])
            if any(e is not None for e in errs):
                style["yerr"] = errs
            curves.append((xs, ys, label, style))
            ring = [(r.e_mid, y) for r, y in zip(rows, ys) if r.reasons]
            flagged += len(ring)
            if ring:
                curves.append(([p[0] for p in ring], [p[1] for p in ring],
                               f"{role}: needs review", RING))
        if not curves:
            self.canvas.show_message("No step has a dQ/dE: the first step has none, "
                                     "and this staircase has no other.")
            return
        ylabel = ("g(E) (states eV$^{-1}$ cm$^{-3}$)" if with_film
                  else "dQ/dE (F)")
        parts = ["x: the midpoint of each step, measured E",
                 "bars: background model, integration, noise"]
        if not with_film:
            parts.append("no film size: set it on the Charge tab for g(E)")
        if flagged:
            parts.append(f"{flagged} step(s) ringed (reason in the table)")
        self.canvas.plot_multi_xy(curves, "Potential (V)", ylabel,
                                  title="PITT dQ/dE", footnote="; ".join(parts),
                                  footnote_warn=bool(flagged))

    def _draw_q(self):
        legs = pa.running_charge(self.results)
        curves = [(e, 1e3 * q, role, LEG_STYLE[role]) for role, (e, q) in legs.items()]
        if not curves:
            self.canvas.show_message("No forward steps to sum.")
            return
        self.canvas.plot_multi_xy(
            curves, "Potential (V)", "Q (mC)", title="PITT running charge",
            footnote="from 0 at the first step; the return leg carries on from the "
                     "forward total; the charge before each first sample is not in it")

    def _draw_step(self):
        r = self._selected()
        if r is None:
            self.canvas.show_message("Choose a step.")
            return
        from spec_echem.data import read_segment_echem_h5
        from spec_echem.gamry_data import CURRENT_COL
        frame = read_segment_echem_h5(self._path(), r.index)
        if frame is None:
            self.canvas.show_message(f"Step {r.index} has no echem.")
            return
        t = frame["Time (s)"].to_numpy()
        i = frame[CURRENT_COL].to_numpy()
        excess = np.abs(i - (r.i_ss or 0.0))
        positive = excess > 0                     # a log axis cannot draw a zero
        keep = pa.hold_window(t)
        t, excess, keep = t[positive], excess[positive], keep[positive]
        if not t.size:
            self.canvas.show_message(f"Step {r.index}: no current beyond I_ss.")
            return
        curves = [(t[keep], excess[keep], "|I - I_ss|, the hold",
                   {"color": "#1f77b4", "marker": ".", "linestyle": "-"})]
        if (~keep).any():
            curves.append((t[~keep], excess[~keep], "after the impedance sweep",
                           {"color": "0.5", "marker": ".", "linestyle": "none"}))
        self.canvas.plot_multi_xy(
            curves, "Time in step (s)", "|I - I_ss| (A)", logy=True,
            title=f"Step {r.index}: {r.potential_set:+.3f} V ({r.role})",
            footnote=f"I_ss = {r.i_ss:.3g} A (tail mean); "
                     f"end: {r.end_reason}; " + ("; ".join(r.reasons) or "no concerns"),
            footnote_warn=bool(r.reasons))

    def _step_label(self, index):
        for r in self.results:
            if r.index == index:
                e = r.potential_set if r.potential_set is not None else float("nan")
                return f"{index}: {e:+.3f} V" + (f" ({r.role})" if r.role != ROLE_FORWARD
                                                   else "")
        return str(index)

    def _draw_all_steps(self, view):
        """Every step on one plot (requested 2026-11-20)."""
        path = self._path()
        label = lambda tr: self._step_label(tr["index"])
        if view == "timeline":
            sd.draw_timeline(self.canvas, sv.step_traces(path), lambda tr: tr["i"],
                             "|I| (A)", label, "PITT: every step's current",
                             logy=True, footnote="the run's own clock; |I| on a log "
                             "axis so the tails show beside the peaks")
        elif view == "overlay":
            # |I|, not |I - I_ss|: the difference crosses zero in the tail and a log
            # axis then plunges to 1e-19 there (seen on the first film, 2026-11-20).
            sd.draw_overlay(self.canvas, sv.step_traces(path), lambda tr: tr["i"],
                            "|I| (A)", label, "PITT: every step from its own start",
                            logy=True, footnote="a tail that flattens is the step's "
                            "steady (leak) current")
        elif view == "spectra":
            sd.draw_end_spectra(self.canvas, path, self._step_label,
                                "PITT: absorbance change at each step's end")
        else:
            e_of = {r.index: r.e for r in self.results
                    if r.role in (ROLE_FORWARD, ROLE_RETURN)}
            sd.draw_bands(self.canvas, path, lambda k: e_of.get(k),
                          "Potential (V), measured at the step's end",
                          "PITT: the bands at each step's end")

    def _draw_impedance(self, view):
        r = self._selected()
        if r is None or r.sweep is None:
            swept = [x.index for x in self.results if x.sweep is not None]
            self.canvas.show_message(
                "This step has no impedance sweep." +
                (f" Swept steps: {', '.join(map(str, swept))}." if swept
                 else " No step in this run was swept."))
            return
        if view == "nyquist":
            frame = read_impedance_h5(self._path(), r.index)
            self.canvas.show_nyquist(frame, title=f"Step {r.index}: "
                                                  f"{r.potential_set:+.3f} V")
            return
        sw = r.sweep
        f = np.asarray(sw["f"], dtype=float)
        curves = [(f, sw["c_parallel"], "C′ = Im[1/(Z - Rs)]/ω",
                   {"color": "#1f77b4", "marker": "o", "linestyle": "-",
                    "yerr": np.asarray(sw["c_u"]) * np.abs(sw["c_parallel"])}),
                  (f, sw["c_series"], "-1/(ω Z″), for reference",
                   {"color": "0.55", "marker": ".", "linestyle": ":"})]
        concerns = list(sw.get("concerns") or []) + list(
            (sw.get("low") or {}).get("concerns") or [])
        self.canvas.plot_multi_xy(
            curves, "Frequency (Hz)", "C (F)", logy=True,
            title=f"Step {r.index}: {r.potential_set:+.3f} V, Rs = {sw['rs']:.4g} ohm",
            footnote="; ".join(concerns) or "no concerns", footnote_warn=bool(concerns))
        self.canvas.ax.set_xscale("log")
        self.canvas.draw_idle()

    # --- table and export ------------------------------------------------------

    def rows(self):
        return pa.pitt_table_rows(self.results)

    def _fill_table(self):
        rows = self.rows()
        names = [name for name, _unit in pa.TABLE_COLUMNS]
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            flagged = row["status"] != "ok"
            for c, key in enumerate(names):
                value = row[key]
                text = ("" if value is None
                        else f"{value:.4g}" if isinstance(value, float)
                        else str(value))
                if flagged and key in ("dQ_C", "C_F", "C_head_F", "g_per_eV_cm3",
                                       "g_head_per_eV_cm3") and text:
                    text = "? " + text
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def figure_basename(self):
        return f"pitt_{self.view_combo.currentData()}"

    def on_save_table(self):
        if not self.results:
            return
        import pandas as pd
        folder = getattr(self.win, "run_folder", None)
        start = "pitt_analysis.csv"
        if folder is not None:
            folder = pathlib.Path(folder)
            start = str(folder / "figures" / f"{folder.name}_pitt_analysis.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Save PITT table", start,
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                for note in self._notes:
                    f.write(f"# {note}\n")
                names = [name for name, _unit in pa.TABLE_COLUMNS]
                units = dict(pa.TABLE_COLUMNS)
                f.write("# units: " + ", ".join(f"{n} [{units[n]}]" for n in names
                                                 if units[n]) + "\n")
                pd.DataFrame(self.rows(), columns=names).to_csv(f, index=False)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
