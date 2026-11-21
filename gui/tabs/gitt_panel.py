"""The GITT tab under the Analysis fit plot: a GITT run, pulse by pulse.

Beside the PITT tab, this reads the run's `{folder}_gitt.h5` through
spec_echem.gitt_analysis and draws the relaxed potential against charge, C per
pulse, every step on one plot, one step's potential, and the spectra at each step's
end. The table under the plot has every number for every step, with the reasons
beside any it objects to; nothing is hidden.

The film thickness for D is the Charge tab's (its Edit... box), as the PITT tab's is.
"""
import pathlib

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                            QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                            QVBoxLayout, QWidget)

from spec_echem import gitt_analysis as ga
from spec_echem import staircase_views as sv
from spec_echem.data import DATA_TYPE_GITT, h5_path
from spec_echem.gitt import ROLE_TITRATION
from gui.tabs import staircase_draw as sd
from gui.widgets.figure_dialog import EXPORT_LABEL, EXPORT_TIP, open_figure_dialog
from gui.widgets.plot_canvas import MplCanvas

VIEWS = (
    ("Relaxed E vs charge", "titration"),
    ("C per pulse vs potential", "c"),
    ("Step potential", "step"),
    ("All steps: potential vs run time", "timeline"),
    ("All steps: overlaid from each start", "overlay"),
    ("All steps: spectra at each step's end", "spectra"),
    ("All steps: bands vs charge", "bands"),
)

RING = {"marker": "o", "ms": 11, "mfc": "none", "mec": "#e07b00", "mew": 1.6,
        "linestyle": "none"}
FLAG_COLUMNS = ("C_F", "C_leak_F", "D_cm2_s", "E_relaxed_V")


class GittPanel(QWidget):
    """The relaxed E, C, every step and the spectra for a GITT run."""

    def __init__(self, analysis_tab):
        super().__init__(analysis_tab)
        self.tab = analysis_tab
        self.win = analysis_tab.win
        self.results = []
        self._key = None
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
        self.step_combo.setToolTip("The step drawn by the Step potential view.")
        self.step_combo.currentIndexChanged.connect(self.redraw)
        row.addWidget(self.step_combo)
        row.addStretch()
        layout.addLayout(row)

        split = QSplitter(Qt.Vertical, self)
        plot_box = QWidget(self)
        plot_layout = QVBoxLayout(plot_box)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = MplCanvas(self, xlabel="Q (mC)", ylabel="E (V)")
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
                                       self.figure_basename(), "Export GITT",
                                       context=lambda: list(self._notes)))
        buttons.addWidget(export)
        plot_layout.addLayout(buttons)
        split.addWidget(plot_box)

        names = [name for name, _unit in ga.TABLE_COLUMNS]
        self.table = QTableWidget(0, len(names), self)
        self.table.setHorizontalHeaderLabels(names)
        for c, (_name, unit) in enumerate(ga.TABLE_COLUMNS):
            if unit:
                self.table.horizontalHeaderItem(c).setToolTip(unit)
        self.table.verticalHeader().setVisible(False)
        split.addWidget(self.table)
        layout.addWidget(split)
        self.canvas.show_message("Load a run with a GITT to see it here.")

    # --- when to recompute ---------------------------------------------------

    def request_refresh(self):
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    # --- data ----------------------------------------------------------------

    def _path(self):
        folder = getattr(self.win, "run_folder", None)
        if folder is None:
            return None
        path = pathlib.Path(h5_path(folder, DATA_TYPE_GITT))
        return path if path.exists() else None

    def _thickness(self):
        """(thickness_nm, where), from the Charge tab's film box."""
        try:
            thickness, _area, _ut, _ua, where = self.tab.charge_panel._film()
        except Exception:  # noqa: BLE001 -- no film size is a state, not a crash
            return 0.0, "not set"
        return thickness, where

    def compute(self):
        """Analyse the run's GITT, unless the file and thickness are as last time."""
        self._stale = False
        path = self._path()
        if path is None:
            self.results, self._key = [], None
            return self.results
        thickness, where = self._thickness()
        key = (str(path), path.stat().st_mtime, thickness)
        if key != self._key:
            self.results = ga.analyse_gitt(path, thickness_nm=thickness or None)
            self._key = key
            self._fill_steps()
        self._notes = [f"run: {path.parent.name}",
                       "GITT analysis (spec_echem.gitt_analysis); derived, not stored "
                       "in the h5",
                       (f"film {thickness:g} nm ({where}) for D" if thickness
                        else "no film thickness: no D"),
                       "q: measured current over the whole step; C = q / dE_s; "
                       "C_leak takes out -C dE/dt x the step, an estimate"]
        return self.results

    def _fill_steps(self):
        previous = self.step_combo.currentData()
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        for r in self.results:
            self.step_combo.addItem(self._step_label(r.index), r.index)
        if previous is not None:
            i = self.step_combo.findData(previous)
            if i >= 0:
                self.step_combo.setCurrentIndex(i)
        self.step_combo.blockSignals(False)

    def _step_label(self, index):
        for r in self.results:
            if r.index == index:
                if r.role != ROLE_TITRATION:
                    return f"{index}: {r.role}"
                start = (f" from {r.e_start:+.3f} V" if r.e_start is not None else "")
                return f"{index}: {1e6 * (r.current_set or 0.0):+.3g} µA{start}"
        return str(index)

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
            self.canvas.show_message("This run has no GITT.")
            return
        view = self.view_combo.currentData()
        if view == "titration":
            self._draw_titration()
        elif view == "c":
            self._draw_c()
        elif view == "step":
            self._draw_step()
        else:
            self._draw_all_steps(view)

    def _pulses(self):
        return [r for r in self.results if r.role == ROLE_TITRATION]

    def _draw_titration(self):
        rest = [r for r in self.results if r.role != ROLE_TITRATION
                and r.e_relaxed is not None]
        rows = [r for r in self._pulses() if r.e_relaxed is not None]
        if not rows:
            self.canvas.show_message("No pulse has a relaxed potential.")
            return
        q0 = [0.0] if rest else []
        e0 = [rest[0].e_relaxed] if rest else []
        q = q0 + [1e3 * r.q_total for r in rows]
        e = e0 + [r.e_relaxed for r in rows]
        curves = [(q, e, "relaxed E (end of each relaxation)",
                   {"color": "#1f77b4", "marker": "o", "linestyle": "-"})]
        ends = [(1e3 * (r.q_total - r.q + (r.q_set or r.q)), r.e_pulse_end)
                for r in rows if r.e_pulse_end is not None]
        if ends:
            curves.append(([p[0] for p in ends], [p[1] for p in ends],
                           "E at each pulse's end",
                           {"color": "0.55", "marker": ".", "linestyle": ":"}))
        ring = [(1e3 * r.q_total, r.e_relaxed) for r in rows if r.reasons]
        if ring:
            curves.append(([p[0] for p in ring], [p[1] for p in ring],
                           "needs review", RING))
        self.canvas.plot_multi_xy(
            curves, "Q (mC), measured", "E (V)", title="GITT titration",
            footnote="Q from 0 at the rest; a relaxation still drifting makes its E "
                     "a bound" + (f"; {len(ring)} step(s) ringed (reason in the "
                                  f"table)" if ring else ""),
            footnote_warn=bool(ring))

    def _draw_c(self):
        rows = [r for r in self._pulses() if r.c is not None and r.e_start is not None]
        if not rows:
            self.canvas.show_message("No pulse has a C.")
            return
        mid = [0.5 * (r.e_start + r.e_relaxed) for r in rows]
        curves = [(mid, [1e3 * r.c for r in rows], "C = q / dE_s",
                   {"color": "#1f77b4", "marker": "o", "linestyle": "-"})]
        leak = [(m, 1e3 * r.c_leak) for m, r in zip(mid, rows) if r.c_leak is not None]
        if leak:
            curves.append(([p[0] for p in leak], [p[1] for p in leak],
                           "C with the leak taken out (estimate)",
                           {"color": "#2ca02c", "marker": "s", "linestyle": "--"}))
        ring = [(m, 1e3 * r.c) for m, r in zip(mid, rows) if r.reasons]
        if ring:
            curves.append(([p[0] for p in ring], [p[1] for p in ring],
                           "needs review", RING))
        self.canvas.plot_multi_xy(
            curves, "Potential (V), midpoint of the relaxed E", "C (mF)",
            title="GITT capacitance per pulse",
            footnote="the leak is -C dE/dt at the step's end, assumed steady over the "
                     "step", footnote_warn=bool(ring))

    def _draw_step(self):
        r = self._selected()
        if r is None:
            self.canvas.show_message("Choose a step.")
            return
        tr = next((x for x in sv.step_traces(self._path()) if x["index"] == r.index),
                  None)
        if tr is None:
            self.canvas.show_message(f"Step {r.index} has no echem.")
            return
        curves = [(tr["t"], tr["e"], "E", {"color": "#1f77b4", "marker": ".",
                                           "linestyle": "-"})]
        if r.e_relaxed is not None:
            curves.append(([tr["t"][0], tr["t"][-1]], [r.e_relaxed] * 2,
                           "relaxed E (tail mean)",
                           {"color": "0.5", "linestyle": ":"}))
        parts = [f"end: {r.end_reason}"]
        if r.r_off is not None:
            parts.append(f"R_off {r.r_off:.0f} ohm")
        if r.drift is not None:
            parts.append(f"drift {1e6 * r.drift:+.0f} µV/s")
        self.canvas.plot_multi_xy(
            curves, "Time in step (s)", "E (V)",
            title=f"Step {self._step_label(r.index)}",
            footnote="; ".join(parts + (r.reasons or ["no concerns"])),
            footnote_warn=bool(r.reasons))

    def _draw_all_steps(self, view):
        path = self._path()
        label = lambda tr: self._step_label(tr["index"])
        if view == "timeline":
            sd.draw_timeline(self.canvas, sv.step_traces(path), lambda tr: tr["e"],
                             "E (V)", label, "GITT: every step's potential",
                             footnote="the run's own clock")
        elif view == "overlay":
            start = {r.index: (r.e_start if r.e_start is not None else np.nan)
                     for r in self.results}
            traces = [tr for tr in sv.step_traces(path)
                      if np.isfinite(start.get(tr["index"], np.nan))]
            sd.draw_overlay(self.canvas, traces,
                            lambda tr: 1e3 * (tr["e"] - start[tr["index"]]),
                            "E - E_start (mV)", label,
                            "GITT: every pulse from its own start",
                            footnote="E_start: the previous step's relaxed E")
        elif view == "spectra":
            sd.draw_end_spectra(self.canvas, path, self._step_label,
                                "GITT: absorbance change at each step's end")
        else:
            q_of = {r.index: (1e3 * r.q_total if r.q_total is not None else None)
                    for r in self.results}
            sd.draw_bands(self.canvas, path, lambda k: q_of.get(k),
                          "Q (mC), measured", "GITT: the bands at each step's end")

    # --- table and export ------------------------------------------------------

    def rows(self):
        return ga.gitt_table_rows(self.results)

    def _fill_table(self):
        rows = self.rows()
        names = [name for name, _unit in ga.TABLE_COLUMNS]
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            flagged = row["status"] != "ok"
            for c, key in enumerate(names):
                value = row[key]
                text = ("" if value is None
                        else f"{value:.4g}" if isinstance(value, float)
                        else str(value))
                if flagged and key in FLAG_COLUMNS and text:
                    text = "? " + text
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def figure_basename(self):
        return f"gitt_{self.view_combo.currentData()}"

    def on_save_table(self):
        if not self.results:
            return
        import pandas as pd
        folder = getattr(self.win, "run_folder", None)
        start = "gitt_analysis.csv"
        if folder is not None:
            folder = pathlib.Path(folder)
            start = str(folder / "figures" / f"{folder.name}_gitt_analysis.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Save GITT table", start,
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                for note in self._notes:
                    f.write(f"# {note}\n")
                names = [name for name, _unit in ga.TABLE_COLUMNS]
                units = dict(ga.TABLE_COLUMNS)
                f.write("# units: " + ", ".join(f"{n} [{units[n]}]" for n in names
                                                 if units[n]) + "\n")
                pd.DataFrame(self.rows(), columns=names).to_csv(f, index=False)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
