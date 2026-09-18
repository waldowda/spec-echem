"""
Tab 5 — Analysis.

Fitting, after a run. Deliberately separate from tab 4: that one is the live glance
during acquisition and needs almost no controls, while fitting means choosing a model,
adjusting a window and re-running. Different lifecycle, different control density —
crowding them together would make the live view worse at the one thing it is for.

Design: docs/analysis-design.md. The maths lives in spec_echem.analysis, which has no
Qt, so it is tested against synthetic data with known answers; this file is the view.
"""
import pathlib

import numpy as np
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem,
    QSplitter, QMessageBox, QHeaderView, QDialog, QApplication,
    QFileDialog, QAbstractItemView,
)
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor, QPalette

from spec_echem.analysis import (
    MODELS, MODEL_FORMULAS, fit_transient, probe_wavelength, tau_ratio,
)
from spec_echem.data import (echem_txt_path, segment_potential, DATA_TYPE_CV,
                             DATA_TYPE_DOPING, DATA_TYPE_DEDOPING)
from spec_echem.gamry_data import read_chrono
from gui.widgets.plot_canvas import MplCanvas

# How far a measured rung potential may sit outside the potential-range boxes and
# still count as inside. Measured potentials differ from the nominal step by a
# fraction of a mV, and the boxes round to 1 mV; 10 mV absorbs both while staying
# well under any rung spacing in use (50-100 mV).
RANGE_TOLERANCE_V = 0.010


# The selected (plotted) trace row. Strong enough to read at a glance, and used
# for focused and unfocused alike -- Windows' default unfocused highlight is
# nearly white.
SELECTED_ROW_BG = "#2f6fb0"


def _ratio_ci95(ratio, numerator, denominator):
    """95% CI on a ratio of two INDEPENDENT fits: (s_r/r)^2 = (s_a/a)^2 + (s_c/c)^2.

    Independent because absorbance and current are separate measurements of the same
    step, fitted separately -- so their errors do not share a covariance the way two
    parameters of one fit do.
    """
    if ratio is None or numerator is None or denominator is None:
        return np.nan
    a, c = numerator.mean_tau, denominator.mean_tau
    ea, ec = numerator.mean_tau_ci95, denominator.mean_tau_ci95
    if not a or not c or ea is None or ec is None:
        return np.nan
    return float(abs(ratio) * np.hypot(ea / a, ec / c))


# The three traces fitted per segment, in table order.
TRACES = ("absorbance", "current", "charge")

# One color per trace, so doping and dedoping of the SAME trace are visibly a pair
# rather than two unrelated series.
TRACE_COLORS = {"absorbance": "#1f77b4", "current": "#ff7f0e", "charge": "#2ca02c"}

TRACE_UNITS = {
    "absorbance": "Absorbance",
    "current": "Current (A)",
    "charge": "Charge (C)",
}


class AnalysisTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._fits = {}          # label -> {trace: FitResult}
        self._fit_wl = {}        # label -> wavelength that fit was made at
        self._wavelength = None  # None = auto
        self._fill_range_boxes = False   # set when the range is ticked; see _on_range_toggled
        self._build()

    # --- layout ---------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)

        controls = QGroupBox("Fit")
        form = QFormLayout(controls)

        self.segment_combo = QComboBox()
        self.segment_combo.currentIndexChanged.connect(self.on_segment_changed)
        form.addRow("Segment:", self.segment_combo)

        # One model for BOTH absorbance and current — comparing them is the point, and
        # a ratio between two different models would not mean anything.
        self.model_combo = QComboBox()
        for name in ("exp", "biexp", "stretched"):
            self.model_combo.addItem(name, name)
        self.model_combo.setToolTip(
            "Applied to absorbance AND current, so the two stay comparable.\n"
            "biexp is also the capacitive-spike model: a fast component plus a\n"
            "slow one is capacitance plus ion motion.")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo)
        # The equation beside the choice, so A and B need no explaining and
        # y(0) = A + sum(B) is self-evident.
        self.model_formula = QLabel("")
        self.model_formula.setStyleSheet("color: #555;")
        self.model_formula.setToolTip(
            "A is what the curve approaches as t grows; each B is the amplitude of\n"
            "the part that decays, so y(0) = A + the prefactors.")
        model_row.addWidget(self.model_formula)
        model_row.addStretch()
        self.model_combo.currentIndexChanged.connect(self._sync_model_formula)
        form.addRow("Model:", model_row)

        # The fit window. Both ends are meant to be tuned by hand and refitted —
        # the capacitive spike's RC is not known in advance, so the useful workflow
        # is move the edge, look at the residuals, fit again. The grayed region on
        # the plot updates live as these move, before any refit.
        span = QHBoxLayout()
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, 100000.0)
        self.start_spin.setDecimals(3)
        self.start_spin.setSuffix(" s")
        # 0.1 s per click, not Qt's default 1.0: chrono_delta_time is 0.1 s, so one
        # step is one data point. A 1 s click jumped ten points at a time, which is
        # far too coarse for trimming a capacitive spike.
        self.start_spin.setSingleStep(0.1)
        self.start_spin.setToolTip(
            "First point the fit uses, in seconds from the start of the segment.\n"
            "Raise it to exclude the capacitive spike, then refit and watch the\n"
            "residual panel.")
        self.stop_spin = QDoubleSpinBox()
        self.stop_spin.setRange(0.0, 100000.0)
        self.stop_spin.setDecimals(3)
        self.stop_spin.setSuffix(" s")
        self.stop_spin.setSingleStep(0.1)   # same reasoning as the start box
        # Shows the segment's own end time, as a number (requested: numbers, not
        # "end of segment"). The trap to avoid is the one this box once fell into:
        # auto-filled with the FIRST segment's length and then kept, it silently
        # fitted only part of every longer segment. So an untouched box is refilled
        # per segment and means "to the end" when fitting -- see _window().
        self.stop_spin.setValue(0.0)
        self._stop_seeded = None
        self.stop_spin.setToolTip(
            "Last point the fit uses. Shows the segment's end until you change it;\n"
            "a value you type applies to every segment fitted.")
        # Live: the grayed excluded region follows these before any refit, so the
        # effect of moving an edge is visible while choosing it.
        self.start_spin.valueChanged.connect(self._draw_fit)
        self.stop_spin.valueChanged.connect(self._draw_fit)
        span.addWidget(self.start_spin)
        span.addWidget(QLabel("to"))
        span.addWidget(self.stop_spin)
        span.addStretch()
        form.addRow("Fit window:", span)

        self.wavelength_spin = QDoubleSpinBox()
        self.wavelength_spin.setRange(0.0, 5000.0)
        self.wavelength_spin.setDecimals(1)
        self.wavelength_spin.setSuffix(" nm")
        self.wavelength_spin.setValue(0.0)
        self.wavelength_spin.setToolTip(
            "The wavelength fitted. Type a value to probe elsewhere, e.g. the\n"
            "pi-pi* bleach -- that turns auto off.")
        self.wavelength_spin.valueChanged.connect(self._on_wavelength_typed)
        # A state, not a value: auto resolves per segment, so the number in the box
        # changes as you step through a run while auto stays on.
        self.wl_auto = QCheckBox("auto")
        self.wl_auto.setChecked(True)
        self.wl_auto.setToolTip(
            "Follow the polaron band for each segment: the band that GROWS on\n"
            "doping, the one that DECAYS on dedoping.")
        self.wl_auto.toggled.connect(self._on_wavelength_changed)
        wl_row = QHBoxLayout()
        wl_row.addWidget(self.wavelength_spin)
        wl_row.addWidget(self.wl_auto)
        wl_row.addStretch()
        form.addRow("Wavelength:", wl_row)

        buttons = QHBoxLayout()
        self.fit_btn = QPushButton("Fit segment")
        self.fit_btn.clicked.connect(self.on_fit_segment)
        self.fit_all_btn = QPushButton("Fit all segments")
        self.fit_all_btn.clicked.connect(self.on_fit_all)
        buttons.addWidget(self.fit_btn)
        buttons.addWidget(self.fit_all_btn)
        # Requested: "there needs to be a table somewhere that holds fit data for all
        # potentials. There is no way currently to review that data." The per-segment
        # table shows three traces of ONE segment; this is every fit at once.
        self.all_fits_btn = QPushButton("All fits…")
        self.all_fits_btn.setToolTip(
            "Every fitted segment and trace in one table, with the parameters,\n"
            "the interval and anything needing review.")
        self.all_fits_btn.clicked.connect(self.on_show_all_fits)
        buttons.addWidget(self.all_fits_btn)
        buttons.addStretch()
        form.addRow("", buttons)

        # Controls beside the table rather than across the top: the form is narrow
        # and left most of the window empty, which squeezed the fit plot into a
        # third of the width. The plot is the thing being read, so it gets the lot.
        top = QSplitter(Qt.Horizontal)
        top.addWidget(controls)

        self.table = QTableWidget(len(TRACES), 3)
        # <tau> with its 95% interval is what the ladder PLOTS, so the number here
        # and the point there are the same thing. It read "SD (s)" once, which was a
        # 1-sigma SD on the RAW tau -- a different statistic on a different quantity
        # from the error bars, with nothing saying so.
        #
        # The raw tau column is gone: the legend now carries tau, beta and every
        # prefactor with their SDs, and four columns of this width truncated to
        # "au (s" and "17....". Raw tau is also ambiguous for a biexp, where it means
        # only the slower of two. It survives on this cell's tooltip.
        self.table.setHorizontalHeaderLabels(
            ["trace", "beta", "mean tau (s), 95% CI"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        # The trace names are fixed strings; stretching them equally with the number
        # columns truncated "absorbance" to "absorba..." once the table shared the
        # row with the controls.
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        # Sized to contents rather than stretched: the value carries its interval
        # too ("2.603 +/- 0.074") and an even split truncated it to "2.603 +/-...".
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for row, trace in enumerate(TRACES):
            self.table.setItem(row, 0, QTableWidgetItem(trace))
        # The table is also the trace SELECTOR, and nothing said so: on Windows the
        # selected row fades to near-white as soon as focus leaves the table, so a
        # user could not tell which row was plotted, or that the rows were
        # clickable at all. Whole rows, one at a time, highlighted the same with or
        # without focus, a hand cursor, and a line of text saying what clicking does.
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        palette = self.table.palette()
        for group in (QPalette.Active, QPalette.Inactive):
            palette.setColor(group, QPalette.Highlight, QColor(SELECTED_ROW_BG))
            palette.setColor(group, QPalette.HighlightedText, QColor("white"))
        self.table.setPalette(palette)
        self.table.setStyleSheet(
            f"QTableWidget::item:selected {{ background: {SELECTED_ROW_BG};"
            f" color: white; }}")
        self.table.viewport().setCursor(Qt.PointingHandCursor)
        table_box = QWidget()
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table)
        self.table_hint = QLabel("Click a row to plot that trace below.")
        self.table_hint.setStyleSheet("color: #555;")
        table_layout.addWidget(self.table_hint)
        top.addWidget(table_box)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 1)
        layout.addWidget(top)

        split = QSplitter(Qt.Vertical)
        self.fit_canvas = MplCanvas(self, xlabel="Time (s)", ylabel="Absorbance")
        # Connected only once the canvas exists: selectRow() emits immediately, and
        # a handler that draws into a not-yet-built canvas crashes the tab.
        # Selecting a row plots that trace: the table is the trace selector, so there
        # is no second control that can disagree with it about what is shown.
        self.table.currentCellChanged.connect(lambda *_: self._draw_fit())
        self.table.selectRow(0)
        split.addWidget(self.fit_canvas)

        plot_box = QWidget()
        plot_layout = QVBoxLayout(plot_box)
        self.ratio_check = QCheckBox("show tau(abs) / tau(current) ratio")
        self.ratio_check.setToolTip(
            "Ratio uses MEAN relaxation times, not raw tau — for a stretched\n"
            "exponential the raw tau is not the physical timescale.\n"
            "A potential with a failed fit leaves a gap rather than being skipped.")
        self.ratio_check.toggled.connect(self._draw_ladder)

        toggles = QHBoxLayout()
        toggles.addWidget(QLabel("Show:"))
        # Charge tau runs ~100x the others on a real ladder, which squashes absorbance
        # and current flat. Hiding a trace rescales the axis to what is left.
        # Charge therefore starts UNticked (requested) -- it is still fitted, and
        # still in the table and All fits...; only the ladder leaves it out until
        # asked for.
        self.trace_checks = {}
        for trace in TRACES:
            cb = QCheckBox(trace)
            cb.setChecked(trace != "charge")
            cb.toggled.connect(self._draw_ladder)
            self.trace_checks[trace] = cb
            toggles.addWidget(cb)
        # A single needs-review point can be 10^11 times the rest -- a dedoping charge
        # integral that never saturates inside the window returns an enormous tau. On a
        # linear axis that flattens every real value to zero. Log is the answer rather
        # than dropping the point, which would be the software deciding again.
        self.log_y_check = QCheckBox("log y")
        self.log_y_check.setToolTip(
            "Use when one fit is orders of magnitude from the rest -- the usual cause\n"
            "is a trace that has not settled inside the window, which is flagged for\n"
            "review rather than hidden.")
        self.log_y_check.toggled.connect(self._draw_ladder)
        toggles.addSpacing(16)
        toggles.addWidget(self.log_y_check)
        toggles.addSpacing(16)
        toggles.addWidget(self.ratio_check)
        toggles.addStretch()
        plot_layout.addLayout(toggles)

        # Both controls below EXCLUDE data, so both default to off and both say in a
        # footnote exactly what they removed. Hiding is the scientist's call, made
        # deliberately -- it is not the software deciding a result should not be seen.
        limits = QHBoxLayout()
        self.hide_flagged_check = QCheckBox("hide flagged points")
        self.hide_flagged_check.setToolTip(
            "Leave the ringed needs-review points out, so the axis scales to the\n"
            "fits you trust. They are still in the table and in All fits...; the\n"
            "plot states how many were hidden.\n"
            "Only applies to the mean-tau view -- the ratio combines two fits and\n"
            "rings neither, so there is nothing there for this to hide.")
        self.hide_flagged_check.toggled.connect(self._draw_ladder)
        limits.addWidget(self.hide_flagged_check)

        limits.addSpacing(16)
        # Well below threshold there is little to switch, so the transients are small
        # and noisy and their tau means little. Restricting the ladder to the rungs
        # above onset keeps those from setting the scale for the ones that matter.
        self.range_check = QCheckBox("potential range:")
        self.range_check.setToolTip(
            "Restrict the ladder to a span of potentials -- the usual reason is to\n"
            "drop rungs far below threshold, where there is too little switching to\n"
            "fit. Opening this fills the boxes with the full span that is plotted.")
        self.range_check.toggled.connect(self._on_range_toggled)
        limits.addWidget(self.range_check)
        self.range_lo = QDoubleSpinBox()
        self.range_hi = QDoubleSpinBox()
        for box in (self.range_lo, self.range_hi):
            box.setRange(-10.0, 10.0)
            box.setDecimals(3)
            box.setSingleStep(0.05)
            box.setSuffix(" V")
            box.setEnabled(False)
            box.valueChanged.connect(self._draw_ladder)
            limits.addWidget(box)
            if box is self.range_lo:
                limits.addWidget(QLabel("to"))
        limits.addStretch()
        plot_layout.addLayout(limits)
        # Label it at construction: MplCanvas defaults to the Instrument tab's
        # raw-spectrum axes ("Wavelength (nm)" / "Intensity (counts)"), which are
        # wrong here and visible until the first fit is plotted.
        self.ladder_canvas = MplCanvas(self, xlabel="Potential (V)",
                                       ylabel="mean relaxation time (s)")
        plot_layout.addWidget(self.ladder_canvas)
        split.addWidget(plot_box)

        layout.addWidget(split, stretch=1)
        self._sync_model_formula()
        self.fit_canvas.show_message("Fit a segment to see the data and its fit.")
        self.ladder_canvas.show_message(
            "Fit a segment to build this plot.")

    # --- data ------------------------------------------------------------

    def _sync_model_formula(self, *_):
        """Show the chosen model's equation next to the dropdown."""
        self.model_formula.setText(
            MODEL_FORMULAS.get(self.model_combo.currentData(), ""))

    def _segment_display(self, label):
        """'Doping 5  (+0.700 V)'. The ladder plots against potential, so the segment
        that produced a point has to name one too -- otherwise the only place a
        potential appears is an axis you cannot map back to a selection."""
        seg = self.win.segments_by_label.get(label)
        text = self.win.segment_potential_text(seg) if seg is not None else ""
        return f"{label}  ({text})" if text else label

    def _current_label(self):
        """The segment's REAL label. The combo displays the potential alongside it,
        so the visible text is not the key into win.results -- itemData is."""
        return self.segment_combo.currentData()

    def refresh_segments(self):
        """Repopulate from the main window's results store, keeping the selection."""
        previous = self._current_label()
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        for label in self.win.results:
            seg = self.win.segments_by_label.get(label)
            # CV is a sweep, not a step — there is no transient to fit.
            if seg is not None and seg.data_type == DATA_TYPE_CV:
                continue
            self.segment_combo.addItem(self._segment_display(label), label)
        if previous:
            i = self.segment_combo.findData(previous)
            if i >= 0:
                self.segment_combo.setCurrentIndex(i)
        self.segment_combo.blockSignals(False)
        self.on_segment_changed()

    def _absorbance_trace(self, label):
        """(time, absorbance) at the chosen wavelength, or (None, None)."""
        df = self.win.results.get(label)
        if df is None or df.empty:
            return None, None
        wl = np.asarray(df.index.values, dtype=float)
        if not self.wl_auto.isChecked():
            row = int(np.abs(wl - self.wavelength_spin.value()).argmin())
        else:
            probe = self._probe_wavelength(label, df.values, wl)
            if probe is None:
                return None, None
            row = int(np.abs(wl - probe).argmin())
        # Recorded whichever branch ran, and as the PIXEL actually used rather than
        # the value asked for: the ladder has to be able to say what it compared.
        self._wavelength = float(wl[row])
        if self.wl_auto.isChecked():
            # The program writing the resolved value, not the user choosing one.
            self.wavelength_spin.blockSignals(True)
            self.wavelength_spin.setValue(self._wavelength)
            self.wavelength_spin.blockSignals(False)
        return np.asarray(df.columns.values, dtype=float), df.values[row, :]

    def _probe_wavelength(self, label, absorbance, wl):
        """The polaron wavelength for this segment. The doping/dedoping distinction
        lives in analysis.probe_wavelength so both tabs cannot disagree."""
        seg = self.win.segments_by_label.get(label)
        doping = seg is None or seg.data_type == DATA_TYPE_DOPING
        return probe_wavelength(absorbance, wl, doping=doping)

    def _echem_traces(self, label):
        """(time, current, charge) for a segment, or (None, None, None)."""
        seg = self.win.segments_by_label.get(label)
        if seg is None or self.win.run_folder is None:
            return None, None, None
        path = echem_txt_path(self.win.run_folder, seg.data_type, seg.run_number)
        if not path.exists():
            return None, None, None
        df = read_chrono(path)
        t = df["Corrected time (s)"].to_numpy(dtype=float)
        i = df["WE(1).Current (A)"].to_numpy(dtype=float)
        # Charge is the running integral — smoother than current, and the more
        # physical comparison against absorbance, which tracks population not rate.
        q = np.concatenate([[0.0], np.cumsum(np.diff(t) * (i[1:] + i[:-1]) / 2.0)])
        return t, i, q

    def _all_traces(self, label):
        """{trace: (time, values)} for one segment.

        Recomputed rather than cached: the absorbance trace depends on the wavelength
        box, and a cache would quietly serve the previous wavelength's curve.
        """
        traces = {}
        t_a, a = self._absorbance_trace(label)
        if t_a is not None:
            traces["absorbance"] = (t_a, a)
        t_e, i, q = self._echem_traces(label)
        if t_e is not None:
            traces["current"] = (t_e, i)
            traces["charge"] = (t_e, q)
        return traces

    # --- actions ---------------------------------------------------------

    def on_segment_changed(self, *_):
        label = self._current_label()
        if not label:
            return
        self._seed_stop(label)
        self._show_fits(self._fits.get(label))
        self._draw_fit()

    def on_fit_segment(self):
        label = self._current_label()
        if not label:
            return
        result = self._fit_one(label)
        if result is None:
            QMessageBox.warning(self, "Nothing to fit",
                                f"No usable data for {label}.")
            return
        self._fits[label] = result
        self._show_fits(result)
        self._draw_fit()
        self._draw_ladder()

    def on_fit_all(self):
        fitted = 0
        for i in range(self.segment_combo.count()):
            label = self.segment_combo.itemData(i)
            result = self._fit_one(label)
            if result is not None:
                self._fits[label] = result
                fitted += 1
        self._show_fits(self._fits.get(self._current_label()))
        self._draw_fit()
        self._draw_ladder()
        if not fitted:
            QMessageBox.warning(self, "Nothing to fit", "No segment had usable data.")

    def _fit_one(self, label):
        model = self.model_combo.currentData()
        traces = self._all_traces(label)
        if not traces:
            return None
        # _all_traces has just set _wavelength via _absorbance_trace.
        if "absorbance" in traces:
            self._fit_wl[label] = self._wavelength
        start, stop = self._window(traces)
        return {name: fit_transient(t, y, model, start, stop)
                for name, (t, y) in traces.items()}

    def _window(self, traces):
        """(start, stop) for the fit, shared by fitting and by the shading on the
        plot — one definition, so the gray region cannot disagree with the fit."""
        # 0 means "the whole segment" at both ends, so neither box needs re-typing
        # for a different run. There was a checkbox here that computed a start from
        # the peak |I|; on a potential step that peaks at the FIRST sample, so it
        # resolved to 0 and excluded nothing -- a control whose only setting was the
        # default. Requested: "I don't see a point of the auto start check box."
        stop = self.stop_spin.value()
        # Untouched = still the end time we filled in = "to the end", so Fit all
        # segments runs each one to ITS end rather than to the displayed one's.
        if self._stop_seeded is not None and round(stop, 3) == self._stop_seeded:
            stop = None
        return (self.start_spin.value() or None), (stop or None)

    def _seed_stop(self, label):
        """Put this segment's end time in the stop box, unless the user set one."""
        df = self.win.results.get(label)
        if df is None or df.empty:
            return
        current = round(self.stop_spin.value(), 3)
        if self._stop_seeded is not None and current != self._stop_seeded \
                and current != 0.0:
            return                           # typed by the user: keep it
        end = float(np.nanmax(np.asarray(df.columns.values, dtype=float)))
        self.stop_spin.blockSignals(True)
        self.stop_spin.setValue(end)
        self.stop_spin.blockSignals(False)
        self._stop_seeded = round(self.stop_spin.value(), 3)

    def on_show_all_fits(self):
        """Every fit made so far, in one reviewable table."""
        rows = []
        for i in range(self.segment_combo.count()):
            label = self.segment_combo.itemData(i)
            fits = self._fits.get(label)
            if not fits:
                continue
            seg = self.win.segments_by_label.get(label)
            potential = self._ladder_potential(seg) if seg is not None else None
            direction = ("doping" if seg is not None
                         and seg.data_type == DATA_TYPE_DOPING else "dedoping")
            # The residual split needs the DATA, which only this tab has -- compute it
            # here rather than making the dialog reach back for traces.
            traces = self._all_traces(label)
            for trace in TRACES:
                fit = fits.get(trace)
                if fit is None:
                    continue
                split = None
                if trace in traces:
                    split = fit.residual_split(*traces[trace])
                rows.append({
                    "segment": label, "potential": potential,
                    "direction": direction, "trace": trace,
                    "wavelength": self._fit_wl.get(label),
                    "fit": fit, "split": split,
                })
        if not rows:
            QMessageBox.information(self, "No fits yet",
                                    "Fit a segment first, or use Fit all segments.")
            return
        AllFitsDialog(rows, self).exec_()

    # --- display ---    # --- display ---------------------------------------------------------

    def _show_fits(self, fits):
        for row, trace in enumerate(TRACES):
            fit = (fits or {}).get(trace)
            if fit is None:
                cells = ["", ""]
            elif fit.did_not_converge:
                cells = ["", "no fit"]          # nothing to show, not a judgement
            elif not fit.ok:
                # FLAGGED, not hidden. The fit converged, so it has numbers worth
                # seeing -- Requested: "since you didn't share the results the scientist
                # doesn't have information to make informed decisions." The "!" and
                # the tooltip carry the concern; the reason is on the plot in full.
                ci = fit.mean_tau_ci95
                cells = [f"{fit.beta:.3g}" if fit.beta is not None else "-",
                         f"? {fit.mean_tau:.4g}"
                         + (f" +/- {ci:.2g}" if ci is not None else "")]
            else:
                ci = fit.mean_tau_ci95
                mean = fit.mean_tau
                cells = [f"{fit.beta:.3g}" if fit.beta is not None else "-",
                         f"{mean:.4g} +/- {ci:.2g}" if ci is not None
                         else f"{mean:.4g} (CI unavailable)"]
            for col, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text)
                if fit is not None and not fit.ok:
                    item.setToolTip(f"NEEDS REVIEW: {fit.reason}")
                elif fit is not None and fit.ok and col == 2:
                    item.setToolTip(f"raw tau = {fit.tau:.4g} +/- {fit.tau_sd:.2g} s "
                                    f"(1 SD)")
                self.table.setItem(row, col, item)

    def _on_wavelength_typed(self, *_):
        """A typed (or Tab-4-clicked) wavelength is a choice, so auto goes off."""
        self.wl_auto.blockSignals(True)
        self.wl_auto.setChecked(False)
        self.wl_auto.blockSignals(False)
        self._on_wavelength_changed()

    def _on_wavelength_changed(self, *_):
        """A fit belongs to the wavelength it was made at, so moving the probe
        discards the absorbance fits rather than leaving a stale tau on screen
        beside a curve from somewhere else. Current and charge are unaffected."""
        for fits in self._fits.values():
            fits.pop("absorbance", None)
        self._show_fits(self._fits.get(self._current_label()))
        self._draw_fit()
        self._draw_ladder()

    def _draw_fit(self, *_):
        """The selected trace with its fit over it — the plot that makes a tau
        assessable instead of merely reported."""
        label = self._current_label()
        if not label:
            self.fit_canvas.show_message("No segment selected.")
            return
        row = self.table.currentRow()
        trace = TRACES[row] if 0 <= row < len(TRACES) else TRACES[0]

        traces = self._all_traces(label)
        if trace not in traces:
            self.fit_canvas.show_message(f"No {trace} data for {label}.")
            return
        t, y = traces[trace]

        fit = (self._fits.get(label) or {}).get(trace)
        caution = None
        if fit is None:
            note, fit_y = "not fitted yet", None
        elif fit.did_not_converge:
            # Nothing to show: no parameters exist. A statement of fact.
            note, fit_y = None, None
            caution = f"FIT DID NOT CONVERGE\n{fit.reason}"
        else:
            # Every parameter, whether or not a check objected. A fit under review
            # needs its numbers MORE than a clean one, not less -- they are how the
            # concern gets judged.
            lines = fit.describe()
            if fit.t_first is not None and len(t):
                k = int(np.argmin(np.abs(t - fit.t_first)))
                for j, line in enumerate(lines):
                    if line.startswith("y(0)"):
                        lines[j] += f"  vs data {y[k]:.4g}"
                        break
            split = fit.residual_split(t, y)
            if split is not None:
                noise, systematic, fraction = split
                lines.append(f"resid: noise {noise:.2g}, model-miss {systematic:.2g}"
                             + (f" ({fraction * 100:.1f}% of swing)"
                                if np.isfinite(fraction) else ""))
            note = "\n".join(lines)
            fit_y = fit.curve(t)
            # No separate caution here: describe() already ends with the
            # "NEEDS REVIEW: <reason>" line, and the legend turns amber around it.
            # `caution` is only for the case with no legend to carry it.

        title = f"{self._segment_display(label)} - {trace}"
        if trace == "absorbance" and not self.wl_auto.isChecked():
            title += f" @ {self.wavelength_spin.value():.1f} nm"
        elif trace == "absorbance" and self._wavelength is not None:
            title += f" @ {self._wavelength:.1f} nm (auto)"

        self.fit_canvas.plot_fit(t, y, fit_y, "Time (s)", TRACE_UNITS[trace],
                                 title=title, window=self._window(traces), note=note,
                                 fit_ok=bool(fit is not None and fit.ok),
                                 caution=caution)

    def _ladder_probe_text(self, labels):
        """'abs @ 807.9 nm', or a warning when the points do not share a wavelength.

        In auto mode the band is chosen PER SEGMENT, and on 20260709_P3HT_01 that gives
        867 / 778 / 808 nm across three potentials. Comparing tau between them is then
        comparing different parts of the polaron band, which the plot must not do
        silently. Fixing it by locking one wavelength across the ladder is a science
        decision, so this states the problem rather than hiding it.
        """
        wls = [self._fit_wl[l] for l in labels if l in self._fit_wl]
        if not wls:
            return "no absorbance"
        lo, hi = min(wls), max(wls)
        if hi - lo <= 1.0:
            return f"abs @ {lo:.1f} nm"
        return f"abs @ {lo:.0f}-{hi:.0f} nm (AUTO, VARIES)"

    def _ladder_potential(self, seg):
        """x for this segment: the potential the film was DOPED TO.

        For a doping step that is its own potential. For the dedoping step that
        follows it, it is that doping step's -- every dedoping segment is held at the
        same -0.5 V, so against its own potential all six stack on one x and the line
        joining them means nothing. What distinguishes them is how far the film was
        doped first, which is also the comparison the bench notes actually make.

        Pre-dedoping returns None: it is a single baseline, not a rung on the ladder.
        """
        return self.win.doped_to(seg)

    def _needs_review_spread(self, series, flags):
        """A hint when needs-review points are orders of magnitude off the rest.

        Returns None unless they actually distort the axis -- the point is to explain
        a plot that has gone flat, not to nag about every flagged fit.
        """
        reviewed, flagged_vals = [], []
        for name, values in series.items():
            mask = flags.get(name) or [False] * len(values)
            for value, is_flagged in zip(values, mask):
                if value is None or not np.isfinite(value):
                    continue
                (flagged_vals if is_flagged else reviewed).append(abs(value))
        if not reviewed or not flagged_vals:
            return None
        if max(flagged_vals) < 100 * max(reviewed):
            return None
        return f"{len(flagged_vals)} point(s) need review, off scale — try log y"

    def _on_range_toggled(self, on):
        """Enable the range boxes, filled with the plotted span so ticking removes
        nothing. _draw_ladder keeps them on the span while the range is off, but
        that needs a draw to have happened -- so the fill is also requested here,
        or ticking before the first draw would apply "0.000 to 0.000"."""
        self.range_lo.setEnabled(on)
        self.range_hi.setEnabled(on)
        self._fill_range_boxes = on
        self._draw_ladder()

    def _draw_ladder(self, *_):
        """tau (or the ratio) against the potential doped to, across whatever has been
        fitted so far -- so it builds as segments are fitted, not only at the end."""
        # The ratio view draws no rings, so there is nothing for this to hide. Leaving
        # the box live there would be a control that silently does nothing.
        self.hide_flagged_check.setEnabled(not self.ratio_check.isChecked())
        rows = []
        for i in range(self.segment_combo.count()):
            label = self.segment_combo.itemData(i)
            seg = self.win.segments_by_label.get(label)
            fits = self._fits.get(label)
            if seg is None or not fits:
                continue
            x = self._ladder_potential(seg)
            if x is None:
                continue
            direction = "doping" if seg.data_type == DATA_TYPE_DOPING else "dedoping"
            rows.append((x, direction, label, fits))

        if not rows:
            self.ladder_canvas.show_message("Fit a segment to build this plot.")
            return

        # While the range is OFF its boxes show the span actually plotted -- numbers,
        # not a "0.000 to 0.000" that meant nothing. Ticking it then starts from
        # exactly what is on screen, so it never removes anything until an end is
        # moved; once on, the boxes are the user's and are not touched again.
        if not self.range_check.isChecked() or self._fill_range_boxes:
            self._fill_range_boxes = False
            span = [r[0] for r in rows]
            for box, value in ((self.range_lo, min(span)), (self.range_hi, max(span))):
                box.blockSignals(True)
                box.setValue(value)
                box.blockSignals(False)

        # Applied BEFORE the x axis is built, so a restricted ladder rescales instead
        # of leaving empty rungs at the ends.
        excluded = []
        if self.range_check.isChecked():
            lo, hi = self.range_lo.value(), self.range_hi.value()
            # Rung potentials are MEASURED (+0.399627 V for a "+0.400" step) and the
            # boxes hold 3 decimals, so exact comparison dropped the rung the user had
            # just typed -- and seeding rounded +0.699498 to 0.699, silently dropping
            # the top rung before anything was touched.
            tol = RANGE_TOLERANCE_V
            kept = [r for r in rows if lo - tol <= r[0] <= hi + tol]
            if len(kept) != len(rows):
                excluded.append(f"{len(rows) - len(kept)} segment(s) outside "
                                f"{lo:+.3f} to {hi:+.3f} V")
            rows = kept
        if not rows:
            self.ladder_canvas.show_message(
                "No fitted segment inside "
                f"{self.range_lo.value():+.3f} to {self.range_hi.value():+.3f} V.\n"
                "Widen the potential range.")
            return

        # Doping and dedoping share an x axis (same run number, same rung), so the
        # series are padded onto the union with NaN rather than plotted separately.
        xs = sorted({r[0] for r in rows})
        at = {x: k for k, x in enumerate(xs)}
        probe = self._ladder_probe_text([r[2] for r in rows])
        series, styles, errors, flags = {}, {}, {}, {}

        def add(name, values, trace, direction, errs=None):
            series[name] = values
            if errs is not None:
                errors[name] = errs
            styles[name] = {
                "color": TRACE_COLORS.get(trace, None),
                "linestyle": "-" if direction == "doping" else "--",
                "marker": "o" if direction == "doping" else "s",
            }

        if self.ratio_check.isChecked():
            for direction in ("doping", "dedoping"):
                vals, errs, present = [np.nan] * len(xs), [np.nan] * len(xs), False
                for x, d, _label, fits in rows:
                    if d != direction:
                        continue
                    present = True
                    # None -> NaN so a failed fit leaves a visible GAP; dropping the
                    # point would hide which potential failed.
                    a, c = fits.get("absorbance"), fits.get("current")
                    r = tau_ratio(a, c)
                    vals[at[x]] = np.nan if r is None else r
                    errs[at[x]] = _ratio_ci95(r, a, c)
                if present:
                    add(f"ratio ({direction})", vals, "absorbance", direction, errs)
            # Units on BOTH sides, so it is visible that they cancel. Requested: "so
            # we know the numerator and denominator have same units and Y is
            # dimensionless."
            ylabel = "(abs mean tau [s]) / (current mean tau [s])"
            title = f"Kinetic coupling (dimensionless) - {probe} (95% CI)"
        else:
            hide_flagged = self.hide_flagged_check.isChecked()
            hidden = 0
            for trace in TRACES:
                if not self.trace_checks[trace].isChecked():
                    continue
                for direction in ("doping", "dedoping"):
                    vals, errs = [np.nan] * len(xs), [np.nan] * len(xs)
                    flagged = [False] * len(xs)
                    present = False
                    for x, d, _label, fits in rows:
                        if d != direction or trace not in fits:
                            continue
                        present = True
                        fit = fits[trace]
                        # Plotted and ringed, never dropped. A gap would be the
                        # software deciding the scientist should not see a result.
                        vals[at[x]] = (fit.mean_tau if fit.mean_tau is not None
                                       else np.nan)
                        ci = fit.mean_tau_ci95
                        errs[at[x]] = np.nan if ci is None else ci
                        if not fit.ok:
                            flagged[at[x]] = True
                            if hide_flagged:
                                # NaN rather than removed from the x array, so the
                                # remaining points keep their potentials. Both axes
                                # then rescale to the fits that are left, which is the
                                # point of the control -- one runaway tau below
                                # threshold otherwise flattens every rung above it.
                                vals[at[x]] = np.nan
                                errs[at[x]] = np.nan
                                hidden += 1
                    if present:
                        name = f"{trace} ({direction})"
                        # Everything in this series was flagged and is now hidden --
                        # keeping it would put an entry in the legend that draws
                        # nothing. The footnote still counts the points.
                        if hide_flagged and not np.any(np.isfinite(vals)):
                            continue
                        add(name, vals, trace, direction, errs)
                        if any(flagged) and not hide_flagged:
                            flags[name] = flagged
            ylabel = "mean relaxation time (s)"
            title = f"Kinetics vs potential - {probe} (95% CI)"
            if hidden:
                excluded.append(f"{hidden} flagged point(s) hidden")

        if not series:
            # Two different reasons for an empty plot, and they need different
            # actions: untick a trace vs widen the filters. Saying the wrong one
            # sends the user to the wrong control.
            self.ladder_canvas.show_message(
                "Nothing left to plot after excluding " + ", ".join(excluded) + "."
                if excluded else "Nothing selected under Show.")
            return
        # Say so when the spread is what is making the plot unreadable, rather than
        # leaving the user to work out why everything is flat at zero.
        spread = self._needs_review_spread(series, flags)
        if spread is not None and not self.log_y_check.isChecked():
            title += f"  —  {spread}"
        # Say what was left out, every time. An exclusion the reader cannot see is
        # the one that turns a plot into a claim it cannot support.
        footnote = ("excluding " + ", ".join(excluded) +
                    " - these are choices made here, not fit failures"
                    ) if excluded else ""
        self.ladder_canvas.plot_series(xs, series, "Potential doped to (V)", ylabel,
                                       title=title, styles=styles, yerr=errors,
                                       flags=flags, footnote=footnote,
                                       logy=self.log_y_check.isChecked())


class AllFitsDialog(QDialog):
    """Every fit in one table, so a run can be reviewed across potentials.

    The parameter columns are built from the MODELS registry for whichever models are
    present, rather than being a fixed set. An earlier version showed only tau, beta
    and <tau> because exp has three parameters, biexp five and stretched four and a
    fixed set cannot hold them all -- but that dropped the prefactors, the second time
    constant, y(0) and the residual split, which is most of what a fit says.

    Read-only but selectable, and exportable as CSV.
    """

    # A canonical order, so a table mixing models still reads left to right sensibly.
    PARAM_ORDER = ("A", "B", "tau", "beta", "B1", "tau1", "B2", "tau2")

    FIXED_LEFT = ("segment", "V", "direction", "trace", "lambda (nm)", "model")
    FIXED_RIGHT = ("y(0)", "mean tau (s)", "95% CI", "n pts",
                   "resid noise", "resid model-miss", "model-miss %", "status")

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("All fits")
        self.resize(1180, 520)
        self._rows = rows

        # Only the parameters actually present, in canonical order -- a table of exp
        # fits should not carry four empty biexp columns.
        present = set()
        for row in rows:
            present.update(MODELS[row["fit"].model][1])
        self._params = [n for n in self.PARAM_ORDER if n in present]
        self._headers = (list(self.FIXED_LEFT)
                         + [f"{n} +/- SD" for n in self._params]
                         + list(self.FIXED_RIGHT))

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(rows), len(self._headers), self)
        self.table.setHorizontalHeaderLabels(self._headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        for r, row in enumerate(rows):
            for c, text in enumerate(self._cells(row)):
                item = QTableWidgetItem(text)
                status = self._status(row["fit"])
                if status != "ok":
                    item.setToolTip(status)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("Copy as CSV", self)
        copy_btn.setToolTip("Every column of every row, to the clipboard.")
        copy_btn.clicked.connect(self._copy_csv)
        save_btn = QPushButton("Save CSV…", self)
        save_btn.clicked.connect(self._save_csv)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(copy_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)

    @staticmethod
    def _status(fit):
        if fit.did_not_converge:
            return "did not converge"
        if fit.needs_review:
            return f"NEEDS REVIEW — {fit.reason}"
        return "ok"

    def _cells(self, row):
        fit = row["fit"]
        names = MODELS[fit.model][1]
        values = dict(zip(names, fit.params)) if fit.params is not None else {}
        sds = dict(zip(names, fit.sd)) if fit.sd is not None else {}

        def num(value, fmt="{:.4g}"):
            return "" if value is None else fmt.format(value)

        cells = [row["segment"], num(row["potential"], "{:+.3f}"), row["direction"],
                 row["trace"],
                 num(row["wavelength"], "{:.1f}") if row["trace"] == "absorbance" else "",
                 fit.model]
        for name in self._params:
            if name not in values:
                cells.append("")            # a parameter this model does not have
            else:
                sd = sds.get(name)
                cells.append(f"{values[name]:.4g}"
                             + (f" +/- {sd:.2g}" if sd is not None else ""))
        noise, missfit, fraction = row["split"] or (None, None, None)
        cells += [num(fit.y_at_start), num(fit.mean_tau), num(fit.mean_tau_ci95),
                  str(fit.n), num(noise, "{:.3g}"), num(missfit, "{:.3g}"),
                  num(fraction * 100 if fraction is not None else None, "{:.2f}"),
                  self._status(fit)]
        return cells

    def _csv(self):
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(self._headers)
        for row in self._rows:
            writer.writerow(self._cells(row))
        return buffer.getvalue()

    def _copy_csv(self):
        QApplication.clipboard().setText(self._csv())

    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save fit results", "fits.csv",
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            pathlib.Path(path).write_text(self._csv(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
