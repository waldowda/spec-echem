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
                               DEFAULT_TAU_MULTIPLE, analyse_step, block_sem,
                               film_rel_u, first_sample_delays, reversibility,
                               reversibility_u, two_sig_fig_u)
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

# Which StepCharge fields each density view reads: (raw, leak-corrected, its +-).
DENSITY_FIELDS = {
    "density": ("carriers_raw", "carriers_corrected", "carriers_u"),
    "charge_density": ("charge_density_raw", "charge_density_corrected",
                       "charge_density_u"),
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
           "Q_corrected_mC", "Q_u_mC", "Q_head_max_mC", "leak_uA",
           "u_background_mC", "u_integration_mC", "u_noise_mC",
           "carriers_raw_cm3", "carriers_corrected_cm3", "carriers_u_cm3",
           "charge_density_raw_C_cm3", "charge_density_corrected_C_cm3",
           "charge_density_u_C_cm3", "A_end", "A0", "bleach", "bleach_u",
           "at_equilibrium", "reason")

ERRORBAR_NOTE = ("error bars: background model (steady vs grown from zero), "
                 "integration check and 2-sigma noise, combined")


def _milli(value):
    return None if value is None else 1e3 * value


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
        """(thickness_nm, area_cm2, u_thickness_nm, u_area_cm2, where they came
        from). An uncertainty of 0 -- or one an older run never recorded -- means
        two significant figures."""
        recorded = self.win.label_settings() or {}
        thickness = recorded.get("film_thickness_nm")
        area = recorded.get("film_area_cm2")
        if thickness and area:
            source = recorded
            where = "this run"
        else:
            source = self.win.settings
            where = "the Parameters tab (this run did not record them)"
        return (float(source.get("film_thickness_nm") or 0.0),
                float(source.get("film_area_cm2") or 0.0),
                float(source.get("film_thickness_u_nm") or 0.0),
                float(source.get("film_area_u_cm2") or 0.0), where)

    def _film_note(self, thickness, area, u_t, u_a):
        """The film-size uncertainty, one scale for every density point."""
        rel = film_rel_u(thickness, area, u_t, u_a)
        if rel is None:
            return None
        auto = [name for name, u in (("thickness", u_t), ("area", u_a)) if not u]
        how = f" ({' and '.join(auto)} to 2 sig. figs)" if auto else ""
        return (f"film volume +-{100 * rel:.1f}%: thickness +-"
                f"{u_t or two_sig_fig_u(thickness):g} nm, area +-"
                f"{u_a or two_sig_fig_u(area):g} cm2{how}; one scale for every "
                f"density, not in the error bars")

    def _head_delays(self):
        """{label: seconds from cell ON to the first current sample}, from the run
        log beside the data. {} when there is no log or it has no such lines."""
        folder = getattr(self.win, "run_folder", None)
        if folder is None:
            return {}
        folder = pathlib.Path(folder)
        try:
            text = (folder / f"{folder.name}_log.log").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return {}
        return first_sample_delays(text)

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
        """(A0 spectrum, wavelengths, where it came from, the spectra it averaged),
        or (None, None, reason, None).

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
                        "end of pre-dedoping", df.values[:, keep])
        for label, seg in segments:
            if seg.data_type == DATA_TYPE_DOPING:
                df = self.win.results[label]
                return (df.values[:, :3].mean(axis=1),
                        np.asarray(df.index.values, dtype=float),
                        f"start of {label} (no pre-dedoping in this run)",
                        df.values[:, :3])
        return None, None, "no neutral spectrum", None

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
        thickness, area, u_t, u_a, film_source = self._film()
        a0_spectrum, wl, a0_source, a0_block = self._neutral(segments)
        row = self._bleach_row(a0_spectrum, wl) if a0_spectrum is not None else None
        a0 = float(a0_spectrum[row]) if row is not None else None
        a0_sem = block_sem(a0_block[row]) if row is not None else None
        a0_u = 2.0 * a0_sem if a0_sem is not None else None
        tail = self.tail_spin.value()
        delays = self._head_delays()

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
                tau_multiple=self.tau_spin.value(), a0_u=a0_u,
                head_delay_s=delays.get(label)))
            steps[-1].run_number = seg.run_number
        self.steps = steps
        self._film_info = (thickness, area, film_rel_u(thickness, area, u_t, u_a),
                           "this run" if film_source == "this run"
                           else "Parameters tab")
        self._a0_info = (a0, float(wl[row])) if a0 is not None else None
        notes = [f"film {thickness:g} nm x {area:g} cm2 from {film_source}"]
        film_note = self._film_note(thickness, area, u_t, u_a)
        if film_note:
            notes.append(film_note)
        if a0 is not None:
            a0_text = f" +- {a0_u:.2g}" if a0_u is not None else ""
            notes.append(f"A0 = {a0:.3f}{a0_text} at {float(wl[row]):.1f} nm, "
                         f"{a0_source}")
        else:
            notes.append(a0_source)
        notes.append(ERRORBAR_NOTE)
        if any(s.q_head_max is not None for s in steps):
            notes.append("Q_head_max: charge before the first current sample (cell ON "
                         "to first sample, from the run log), an exponential run back: "
                         "an upper bound, NOT in Q")
        elif steps:
            notes.append("no cell-ON timing in the run log: the charge before the "
                         "first current sample is not estimated (and not in Q)")
        ends = [s.bleach for s in steps
                if s.direction == "dedoping" and s.bleach is not None]
        self._floor = (min(ends), max(ends)) if ends else None
        if ends:
            notes.append(f"dedoping steps end at bleach {min(ends):.3f} to "
                         f"{max(ends):.3f} (0 = back to A0): baseline drift plus "
                         f"charge left in, a floor under every bleach")
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
        footnote = self._footnote(view, steps, flagged)
        if not curves:
            self.canvas.show_message(title)
            return
        self.canvas.plot_multi_xy(curves, xlabel, ylabel, title=title,
                                  footnote=footnote, footnote_warn=bool(flagged))

    def _footnote(self, view, steps, flagged):
        """A SHORT footnote for this view; the full notes go with the CSV.

        Every note on every plot took half the figure (2026-11-08)."""
        thickness, area, rel, where = self._film_info
        bleach_view = view in ("bleach", "bleach_charge")
        parts = []
        if view != "charge" and view != "reversibility":
            film = f"film {thickness:g} nm x {area:g} cm2 ({where})"
            if rel is not None:
                film += f", volume +-{100 * rel:.1f}% not in the bars"
            parts.append(film)
        if bleach_view:
            if self._a0_info:
                parts.append("A0 %.3f at %.0f nm" % self._a0_info)
            parts.append("doping steps only (dedoping ends neutral; see the table)")
            if self._floor:
                parts.append("dedoping ends at bleach %.3f to %.3f" % self._floor)
        parts.append("bars: background model, integration, noise")
        heads = any(s.q_head_max is not None for s in steps)
        if view == "reversibility" and heads:
            parts.append("the charge before the first sample is not in Q and is larger "
                         "on dedoping, so this ratio reads low (Q_head_max)")
        elif not bleach_view:
            parts.append("Q leaves out the charge before the first current sample"
                         + (" (Q_head_max in the table)" if heads else
                            " (not estimated: no timing in the run log)"))
        if flagged:
            parts.append(f"{flagged} step(s) ringed: not at equilibrium "
                         f"(reason in the table)")
        return "; ".join(parts)

    def _curves(self, view, steps):
        """(curves, xlabel, ylabel, title) for plot_multi_xy."""
        curves = []

        def add(direction, pairs, label, style=None, flagged_pairs=()):
            """pairs: (x, y) or (x, y, y_err) or (x, y, y_err, x_err)."""
            pairs = [tuple(p) + (None,) * (4 - len(p)) for p in pairs
                     if p[0] is not None and p[1] is not None]
            if pairs:
                pairs.sort(key=lambda p: p[0])
                xs, ys, yerr, xerr = zip(*pairs)
                errs = {key: err for key, err in (("yerr", yerr), ("xerr", xerr))
                        if any(e is not None for e in err)}
                curves.append((xs, ys, label, {**DIRECTION_STYLE[direction],
                                               **(style or {}), **errs}))
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
                pairs.append((s.potential, r, reversibility_u(d, s)))
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
                add(direction, [(s.potential, 1e3 * s.q_corrected, 1e3 * s.q_u)
                                for s in group],
                    f"{direction}: leak-corrected",
                    flagged_pairs=[(s.potential, 1e3 * s.q_corrected)
                                   for s in ring_key])
            elif view in ("density", "charge_density"):
                raw, corr, unc = DENSITY_FIELDS[view]
                add(direction, [(s.potential, getattr(s, raw)) for s in group],
                    f"{direction}: raw", {"alpha": 0.45})
                add(direction, [(s.potential, getattr(s, corr), getattr(s, unc))
                                for s in group],
                    f"{direction}: leak-corrected",
                    flagged_pairs=[(s.potential, getattr(s, corr))
                                   for s in ring_key])
            elif view in ("bleach", "bleach_charge") and direction == "doping":
                _raw, corr, unc = DENSITY_FIELDS["density" if view == "bleach"
                                                 else "charge_density"]
                # Doping only. A dedoping step ENDS neutral, so its bleach (~0)
                # describes the film after the charge came out, while its x would be
                # the charge removed -- a neutral film drawn at 7e20 cm-3. Its bleach
                # is still in the table.
                add(direction,
                    [(getattr(s, corr), s.bleach, s.bleach_u, getattr(s, unc))
                     for s in group],
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
                "Q_u_mC": _milli(s.q_u), "Q_head_max_mC": _milli(s.q_head_max),
                "leak_uA": 1e6 * s.leak_current,
                "u_background_mC": _milli(s.u_background),
                "u_integration_mC": _milli(s.u_integration),
                "u_noise_mC": _milli(s.u_noise),
                "carriers_raw_cm3": s.carriers_raw,
                "carriers_corrected_cm3": s.carriers_corrected,
                "carriers_u_cm3": s.carriers_u,
                "charge_density_raw_C_cm3": s.charge_density_raw,
                "charge_density_corrected_C_cm3": s.charge_density_corrected,
                "charge_density_u_C_cm3": s.charge_density_u,
                "A_end": s.a_end, "A0": s.a0, "bleach": s.bleach,
                "bleach_u": s.bleach_u,
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
