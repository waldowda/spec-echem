"""The Charge tab under the Analysis fit plot (requested 2026-11-06).

Beside the Kinetics ladder, which says how FAST each step switched, this says how
MUCH: the charge, the carrier density it makes, the neutral absorption it bleached,
and how much came back out on dedoping. The numbers come from spec_echem.charge;
this panel gathers each step's data and draws them.

Film size comes from the run's own metadata (win.label_settings), so a loaded run is
analysed with the thickness and area it was recorded with. Only an older run that
never recorded them falls back to the Parameters tab, and the plot says so.
"""
import pathlib

import numpy as np
from qtpy.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                            QHBoxLayout, QLabel, QMessageBox, QPushButton,
                            QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                            QSplitter)
from qtpy.QtCore import Qt

from spec_echem.charge import (DEFAULT_DRIFT_FRACTION, DEFAULT_TAIL_S,
                               DEFAULT_TAU_MULTIPLE, analyse_step, reversibility)
from spec_echem.data import DATA_TYPE_DEDOPING, DATA_TYPE_DOPING, DATA_TYPE_PREDEDOPING
from gui.widgets.plot_canvas import MplCanvas
from gui.widgets.figure_dialog import EXPORT_LABEL, EXPORT_TIP, open_figure_dialog

VIEWS = (
    ("Charge vs potential", "charge"),
    ("Carrier density vs potential", "density"),
    ("Charge density vs potential", "charge_density"),
    ("Bleach vs carrier density", "bleach"),
    ("Bleach vs charge density", "bleach_charge"),
    ("Q out / Q in vs potential", "reversibility"),
)

# Which StepCharge fields each density view reads: (raw, leak-corrected).
DENSITY_FIELDS = {
    "density": ("carriers_raw", "carriers_corrected"),
    "charge_density": ("charge_density_raw", "charge_density_corrected"),
}
DENSITY_LABELS = {
    "density": ("Carrier density", "cm$^{-3}$"),
    "charge_density": ("Charge density", "C cm$^{-3}$"),
}

DIRECTION_STYLE = {
    "doping": {"color": "#1f77b4", "marker": "o", "linestyle": "-"},
    "dedoping": {"color": "#d62728", "marker": "s", "linestyle": "--"},
}
RING = {"marker": "o", "ms": 11, "mfc": "none", "mec": "#e07b00", "mew": 1.6,
        "linestyle": "none"}

COLUMNS = ("segment", "doped_to_V", "direction", "duration_s", "Q_raw_mC",
           "Q_corrected_mC", "leak_uA", "carriers_raw_cm3", "carriers_corrected_cm3",
           "charge_density_raw_C_cm3", "charge_density_corrected_C_cm3",
           "A_end", "A0", "bleach", "at_equilibrium", "reason")


class ChargePanel(QWidget):
    """Charge, carrier density, bleach and reversibility for each step."""

    def __init__(self, analysis_tab):
        super().__init__(analysis_tab)
        self.tab = analysis_tab
        self.win = analysis_tab.win
        self.steps = []
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
        self.view_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.view_combo)
        row.addSpacing(16)
        row.addWidget(QLabel("Bleach at"))
        self.wl_spin = QDoubleSpinBox(self)
        self.wl_spin.setRange(0.0, 5000.0)
        self.wl_spin.setDecimals(1)
        self.wl_spin.setSuffix(" nm")
        self.wl_spin.setEnabled(False)
        self.wl_spin.valueChanged.connect(self.refresh)
        row.addWidget(self.wl_spin)
        self.wl_auto = QCheckBox("neutral peak", self)
        self.wl_auto.setChecked(True)
        self.wl_auto.setToolTip(
            "Use the wavelength where the neutral film (A0) absorbs most -- the\n"
            "band that bleaches. Untick to choose another; at a polaron wavelength\n"
            "the 'bleach' comes out negative, because that band grows.")
        self.wl_auto.toggled.connect(lambda on: (self.wl_spin.setEnabled(not on),
                                                 self.refresh()))
        row.addWidget(self.wl_auto)
        row.addStretch()
        layout.addLayout(row)

        checks = QHBoxLayout()
        checks.addWidget(QLabel("Equilibrium: last"))
        self.tail_spin = self._spin(" s", 0.5, 600.0, DEFAULT_TAIL_S, 1)
        self.tail_spin.setToolTip(
            "The end of each step: its absorbance and current are averaged over\n"
            "this span, the drift is measured over it, and the mean current here is\n"
            "the leak subtracted for the corrected charge.")
        checks.addWidget(self.tail_spin)
        checks.addWidget(QLabel("move <"))
        self.drift_spin = self._spin(" %", 0.1, 50.0, 100 * DEFAULT_DRIFT_FRACTION, 1)
        self.drift_spin.setToolTip(
            "A step is flagged if its absorbance or current still moved by more\n"
            "than this share of the step's whole change over that last span.")
        checks.addWidget(self.drift_spin)
        checks.addWidget(QLabel("and step ≥"))
        self.tau_spin = self._spin(" × tau", 1.0, 20.0, DEFAULT_TAU_MULTIPLE, 1)
        self.tau_spin.setToolTip(
            "...or if the step was shorter than this many relaxation times\n"
            "(5 tau = 99.3% complete). Uses the Kinetics fits of absorbance and\n"
            "current that passed review; a step not yet fitted gets the drift\n"
            "check only, and the table says so.")
        checks.addWidget(self.tau_spin)
        checks.addStretch()
        layout.addLayout(checks)

        split = QSplitter(Qt.Vertical, self)
        plot_box = QWidget(self)
        plot_layout = QVBoxLayout(plot_box)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = MplCanvas(self, xlabel="Potential doped to (V)",
                                ylabel="Charge (mC)")
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
                                       self.figure_basename(), "Export charge"))
        buttons.addWidget(export)
        plot_layout.addLayout(buttons)
        split.addWidget(plot_box)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        split.addWidget(self.table)
        layout.addWidget(split)
        self.canvas.show_message("Load or run doping steps to see their charge.")

    def _spin(self, suffix, lo, hi, value, decimals):
        box = QDoubleSpinBox(self)
        box.setRange(lo, hi)
        box.setDecimals(decimals)
        box.setSuffix(suffix)
        box.setValue(value)
        box.valueChanged.connect(self.refresh)
        return box

    # --- when to recompute ---------------------------------------------------

    def request_refresh(self):
        """Now if on screen, else the next time it is shown -- reading every step's
        echem file is cheap but not free, and the Kinetics tab is the usual view."""
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            self.refresh()

    # --- data ----------------------------------------------------------------

    def _film(self):
        """(thickness_nm, area_cm2, where they came from)."""
        recorded = self.win.label_settings() or {}
        thickness = recorded.get("film_thickness_nm")
        area = recorded.get("film_area_cm2")
        if thickness and area:
            return float(thickness), float(area), "this run"
        live = self.win.settings
        return (float(live.get("film_thickness_nm") or 0.0),
                float(live.get("film_area_cm2") or 0.0),
                "the Parameters tab (this run did not record them)")

    def _segments(self):
        """(label, segment) for every doping and dedoping step, in run order."""
        out = []
        for label in self.win.results:
            seg = self.win.segments_by_label.get(label)
            if seg is not None and seg.data_type in (DATA_TYPE_DOPING,
                                                     DATA_TYPE_DEDOPING):
                out.append((label, seg))
        return out

    def _neutral(self, segments):
        """(A0 spectrum, wavelengths, where it came from), or (None, None, reason).

        The pre-dedoping step's end is the neutral film. Without one, the first
        spectra of the first doping step: it starts from the dedoped state too."""
        for label in self.win.results:
            seg = self.win.segments_by_label.get(label)
            if seg is not None and seg.data_type == DATA_TYPE_PREDEDOPING:
                df = self.win.results[label]
                t = np.asarray(df.columns.values, dtype=float)
                keep = t >= t[-1] - self.tail_spin.value()
                return (df.values[:, keep].mean(axis=1),
                        np.asarray(df.index.values, dtype=float),
                        "end of pre-dedoping")
        for label, seg in segments:
            if seg.data_type == DATA_TYPE_DOPING:
                df = self.win.results[label]
                return (df.values[:, :3].mean(axis=1),
                        np.asarray(df.index.values, dtype=float),
                        f"start of {label} (no pre-dedoping in this run)")
        return None, None, "no neutral spectrum"

    def _bleach_row(self, a0, wl):
        """The pixel bleach is read at: the neutral peak, or the box."""
        if self.wl_auto.isChecked():
            smooth = np.convolve(a0, np.ones(5) / 5.0, mode="same")
            row = int(np.nanargmax(smooth))
            self.wl_spin.blockSignals(True)
            self.wl_spin.setRange(float(wl.min()), float(wl.max()))
            self.wl_spin.setValue(float(wl[row]))
            self.wl_spin.blockSignals(False)
            return row
        return int(np.abs(wl - self.wl_spin.value()).argmin())

    def _mean_taus(self, label):
        """{trace: mean tau} from this step's Kinetics fits that passed review."""
        fits = self.tab._fits.get(label) or {}
        return {trace: fit.mean_tau for trace, fit in fits.items()
                if trace in ("absorbance", "current") and fit.ok
                and fit.mean_tau is not None}

    def compute(self):
        """Every step's StepCharge, in run order. Sets self.steps and self._notes."""
        self._stale = False
        segments = self._segments()
        thickness, area, film_source = self._film()
        a0_spectrum, wl, a0_source = self._neutral(segments)
        row = self._bleach_row(a0_spectrum, wl) if a0_spectrum is not None else None
        a0 = float(a0_spectrum[row]) if row is not None else None
        tail = self.tail_spin.value()

        steps = []
        for label, seg in segments:
            t, i, _q = self.tab._echem_traces(label)
            if t is None:
                continue
            df = self.win.results.get(label)
            ta = a = None
            if df is not None and row is not None and row < df.shape[0]:
                ta = np.asarray(df.columns.values, dtype=float)
                a = df.values[row, :]
            steps.append(analyse_step(
                label, self.tab._ladder_potential(seg), self.tab._direction_for(seg),
                t, i, absorbance_time=ta, absorbance=a, a0=a0,
                area_cm2=area, thickness_nm=thickness,
                mean_taus=self._mean_taus(label), tail_s=tail,
                drift_fraction=self.drift_spin.value() / 100.0,
                tau_multiple=self.tau_spin.value()))
            steps[-1].run_number = seg.run_number
        self.steps = steps
        notes = [f"film {thickness:g} nm x {area:g} cm2 from {film_source}"]
        if a0 is not None:
            notes.append(f"A0 = {a0:.3f} at {float(wl[row]):.1f} nm, {a0_source}")
        else:
            notes.append(a0_source)
        self._notes = notes
        return steps

    # --- drawing ---------------------------------------------------------------

    def refresh(self, *_):
        steps = self.compute()
        self._fill_table(steps)
        if not steps:
            self.canvas.show_message(
                "No doping or dedoping step with an echem file in this run.")
            return
        view = self.view_combo.currentData()
        curves, xlabel, ylabel, title = self._curves(view, steps)
        flagged = sum(1 for s in steps if not s.at_equilibrium)
        footnote = "; ".join(self._notes)
        if view in ("bleach", "bleach_charge"):
            footnote += ("; doping steps only: a dedoping step ends neutral "
                         "(its bleach is in the table)")
        if flagged:
            footnote += (f"; {flagged} step(s) ringed: not at equilibrium "
                         f"(reason in the table)")
        if not curves:
            self.canvas.show_message(title)
            return
        self.canvas.plot_multi_xy(curves, xlabel, ylabel, title=title,
                                  footnote=footnote, footnote_warn=bool(flagged))

    def _curves(self, view, steps):
        """(curves, xlabel, ylabel, title) for plot_multi_xy."""
        curves = []

        def add(direction, pairs, label, style=None, flagged_pairs=()):
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if pairs:
                pairs.sort()
                xs, ys = zip(*pairs)
                curves.append((xs, ys, label, {**DIRECTION_STYLE[direction],
                                               **(style or {})}))
            ring = [(x, y) for x, y in flagged_pairs if x is not None and y is not None]
            if ring:
                xs, ys = zip(*ring)
                curves.append((xs, ys, f"{direction}: not at equilibrium", RING))

        by_dir = {d: [s for s in steps if s.direction == d]
                  for d in ("doping", "dedoping")}

        if view == "reversibility":
            dopes = {s.run_number: s for s in by_dir["doping"]}
            pairs, ring = [], []
            for s in by_dir["dedoping"]:
                d = dopes.get(s.run_number)
                r = reversibility(d, s)
                pairs.append((s.potential, r))
                if r is not None and not (s.at_equilibrium and d.at_equilibrium):
                    ring.append((s.potential, r))
            add("doping", pairs, "|Q dedoping| / Q doping (leak-corrected)",
                flagged_pairs=ring)
            return (curves, "Potential doped to (V)", "Q out / Q in",
                    "Charge recovered on dedoping" if curves else
                    "Needs doping and dedoping steps from the same run.")

        for direction, group in by_dir.items():
            if not group:
                continue
            ring_key = [s for s in group if not s.at_equilibrium]
            if view == "charge":
                add(direction, [(s.potential, 1e3 * s.q_raw) for s in group],
                    f"{direction}: raw", {"alpha": 0.45})
                add(direction, [(s.potential, 1e3 * s.q_corrected) for s in group],
                    f"{direction}: leak-corrected",
                    flagged_pairs=[(s.potential, 1e3 * s.q_corrected)
                                   for s in ring_key])
            elif view in ("density", "charge_density"):
                raw, corr = DENSITY_FIELDS[view]
                add(direction, [(s.potential, getattr(s, raw)) for s in group],
                    f"{direction}: raw", {"alpha": 0.45})
                add(direction, [(s.potential, getattr(s, corr)) for s in group],
                    f"{direction}: leak-corrected",
                    flagged_pairs=[(s.potential, getattr(s, corr))
                                   for s in ring_key])
            elif view in ("bleach", "bleach_charge") and direction == "doping":
                corr = DENSITY_FIELDS["density" if view == "bleach"
                                      else "charge_density"][1]
                # Doping only. A dedoping step ENDS neutral, so its bleach (~0)
                # describes the film after the charge came out, while its x would be
                # the charge removed -- a neutral film drawn at 7e20 cm-3. Its bleach
                # is still in the table.
                add(direction,
                    [(getattr(s, corr), s.bleach) for s in group],
                    f"{direction}", {"linestyle": "none"},
                    flagged_pairs=[(getattr(s, corr), s.bleach)
                                   for s in ring_key])

        if view == "charge":
            return curves, "Potential doped to (V)", "Charge (mC)", "Charge per step"
        no_size = ([], "", "", "No film size: set the film thickness and area "
                               "on the Parameters tab.")
        if view in ("density", "charge_density"):
            if not any(s.carriers_corrected for s in steps):
                return no_size
            name, unit = DENSITY_LABELS[view]
            return (curves, "Potential doped to (V)", f"{name} ({unit})",
                    f"{name} per step")
        if not any(s.bleach is not None for s in steps):
            return [], "", "", "No neutral spectrum to take A0 from."
        if not any(s.carriers_corrected for s in steps):
            return no_size
        name, unit = DENSITY_LABELS["density" if view == "bleach"
                                    else "charge_density"]
        return (curves, f"{name}, leak-corrected ({unit})",
                "Bleach (A0 - A) / A0", f"Bleach vs {name.lower()}")

    # --- table and export ------------------------------------------------------

    def rows(self):
        """One dict per step, in COLUMNS order -- the table, the CSV, the tests."""
        out = []
        for s in self.steps:
            out.append({
                "segment": s.label, "doped_to_V": s.potential,
                "direction": s.direction, "duration_s": s.duration,
                "Q_raw_mC": 1e3 * s.q_raw, "Q_corrected_mC": 1e3 * s.q_corrected,
                "leak_uA": 1e6 * s.leak_current,
                "carriers_raw_cm3": s.carriers_raw,
                "carriers_corrected_cm3": s.carriers_corrected,
                "charge_density_raw_C_cm3": s.charge_density_raw,
                "charge_density_corrected_C_cm3": s.charge_density_corrected,
                "A_end": s.a_end, "A0": s.a0, "bleach": s.bleach,
                "at_equilibrium": s.at_equilibrium, "reason": s.reason})
        return out

    def _fill_table(self, steps):
        rows = self.rows()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(COLUMNS):
                value = row[key]
                text = ("" if value is None
                        else f"{value:.4g}" if isinstance(value, float)
                        else str(value))
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def figure_basename(self):
        return f"charge_{self.view_combo.currentData()}"

    def on_save_table(self):
        if not self.steps:
            return
        import pandas as pd
        start = "charge_table.csv"
        if getattr(self.win, "run_folder", None) is not None:
            start = str(pathlib.Path(self.win.run_folder) / "figures" / start)
        path, _ = QFileDialog.getSaveFileName(self, "Save charge table", start,
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                for note in self._notes:
                    f.write(f"# {note}\n")
                pd.DataFrame(self.rows(), columns=COLUMNS).to_csv(f, index=False)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
