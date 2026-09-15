"""
Tab 5 — Analysis.

Fitting, after a run. Deliberately separate from tab 4: that one is the live glance
during acquisition and needs almost no controls, while fitting means choosing a model,
adjusting a window and re-running. Different lifecycle, different control density —
crowding them together would make the live view worse at the one thing it is for.

Design: docs/analysis-design.md. The maths lives in spec_echem.analysis, which has no
Qt, so it is tested against synthetic data with known answers; this file is the view.
"""
import numpy as np
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem,
    QSplitter, QMessageBox, QHeaderView, QDialog,
)
from qtpy.QtCore import Qt

from spec_echem.analysis import (
    MODELS, MODEL_FORMULAS, fit_transient, probe_wavelength, tau_ratio,
)
from spec_echem.data import (echem_txt_path, segment_potential, DATA_TYPE_CV,
                             DATA_TYPE_DOPING, DATA_TYPE_DEDOPING)
from spec_echem.gamry_data import read_chrono
from gui.widgets.plot_canvas import MplCanvas

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

# One colour per trace, so doping and dedoping of the SAME trace are visibly a pair
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
        # is move the edge, look at the residuals, fit again. The greyed region on
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
        self.start_spin.setSpecialValueText("start of segment")
        self.start_spin.setToolTip(
            "First point the fit uses. 0 = the start of the segment. Raise it to\n"
            "exclude the capacitive spike, then refit and watch the residual panel.")
        self.stop_spin = QDoubleSpinBox()
        self.stop_spin.setRange(0.0, 100000.0)
        self.stop_spin.setDecimals(3)
        self.stop_spin.setSuffix(" s")
        self.stop_spin.setSingleStep(0.1)   # same reasoning as the start box
        # 0 means "run to the end", so the stop never has to be re-typed for a
        # longer segment. It used to be auto-filled with the first segment's
        # length and then kept, which silently fitted only part of a longer one.
        self.stop_spin.setSpecialValueText("end of segment")
        self.stop_spin.setValue(0.0)
        self.stop_spin.setToolTip(
            "Last point the fit uses. 0 = the end of the segment.")
        # Live: the greyed excluded region follows these before any refit, so the
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
        self.wavelength_spin.setSpecialValueText("auto (polaron)")
        self.wavelength_spin.setValue(0.0)
        self.wavelength_spin.setToolTip(
            "0 = automatic: the wavelength whose absorbance GROWS most across the\n"
            "segment, which is the polaron band. Set a value to probe elsewhere,\n"
            "e.g. the pi-pi* bleach.")
        self.wavelength_spin.valueChanged.connect(self._on_wavelength_changed)
        wl_row = QHBoxLayout()
        wl_row.addWidget(self.wavelength_spin)
        # The plot title carries this, but it disappears the moment you select the
        # current or charge trace -- and the control itself never said.
        self.auto_wl_label = QLabel("")
        self.auto_wl_label.setStyleSheet("color: #555;")
        wl_row.addWidget(self.auto_wl_label)
        wl_row.addStretch()
        form.addRow("Wavelength:", wl_row)

        buttons = QHBoxLayout()
        self.fit_btn = QPushButton("Fit segment")
        self.fit_btn.clicked.connect(self.on_fit_segment)
        self.fit_all_btn = QPushButton("Fit all segments")
        self.fit_all_btn.clicked.connect(self.on_fit_all)
        buttons.addWidget(self.fit_btn)
        buttons.addWidget(self.fit_all_btn)
        # Dean: "there needs to be a table somewhere that holds fit data for all
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
        top.addWidget(self.table)
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
        self.trace_checks = {}
        for trace in TRACES:
            cb = QCheckBox(trace)
            cb.setChecked(True)
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
        requested = self.wavelength_spin.value()
        if requested > 0:
            row = int(np.abs(wl - requested).argmin())
        else:
            probe = self._probe_wavelength(label, df.values, wl)
            if probe is None:
                return None, None
            row = int(np.abs(wl - probe).argmin())
        # Recorded whichever branch ran, and as the PIXEL actually used rather than
        # the value asked for: the ladder has to be able to say what it compared.
        self._wavelength = float(wl[row])
        self.auto_wl_label.setText(
            "" if requested > 0 else f"= {self._wavelength:.1f} nm")
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
        plot — one definition, so the grey region cannot disagree with the fit."""
        # 0 means "the whole segment" at both ends, so neither box needs re-typing
        # for a different run. There was a checkbox here that computed a start from
        # the peak |I|; on a potential step that peaks at the FIRST sample, so it
        # resolved to 0 and excluded nothing -- a control whose only setting was the
        # default. Dean: "I don't see a point of the auto start check box."
        return (self.start_spin.value() or None), (self.stop_spin.value() or None)

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
            for trace in TRACES:
                fit = fits.get(trace)
                if fit is None:
                    continue
                rows.append((label, potential, direction, trace,
                             self._fit_wl.get(label), fit))
        if not rows:
            QMessageBox.information(self, "No fits yet",
                                    "Fit a segment first, or use Fit all segments.")
            return
        AllFitsDialog(rows, self).exec_()

    # --- display ---------------------------------------------------------

    def _show_fits(self, fits):
        for row, trace in enumerate(TRACES):
            fit = (fits or {}).get(trace)
            if fit is None:
                cells = ["", ""]
            elif fit.did_not_converge:
                cells = ["", "no fit"]          # nothing to show, not a judgement
            elif not fit.ok:
                # FLAGGED, not hidden. The fit converged, so it has numbers worth
                # seeing -- Dean: "since you didn't share the results the scientist
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
            if fit.needs_review:
                caution = f"NEEDS REVIEW\n{fit.reason}"

        title = f"{self._segment_display(label)} - {trace}"
        if trace == "absorbance" and self.wavelength_spin.value() > 0:
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
        if seg.data_type == DATA_TYPE_DOPING:
            return self.win.segment_potential(seg)
        if seg.data_type == DATA_TYPE_DEDOPING:
            for other in self.win.segments_by_label.values():
                if (other.data_type == DATA_TYPE_DOPING
                        and other.run_number == seg.run_number):
                    return self.win.segment_potential(other)
        return None

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

    def _draw_ladder(self, *_):
        """tau (or the ratio) against the potential doped to, across whatever has been
        fitted so far -- so it builds as segments are fitted, not only at the end."""
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
            # Units on BOTH sides, so it is visible that they cancel. Dean: "so
            # we know the numerator and denominator have same units and Y is
            # dimensionless."
            ylabel = "(abs mean tau [s]) / (current mean tau [s])"
            title = f"Kinetic coupling (dimensionless) - {probe} (95% CI)"
        else:
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
                    if present:
                        name = f"{trace} ({direction})"
                        add(name, vals, trace, direction, errs)
                        if any(flagged):
                            flags[name] = flagged
            ylabel = "mean relaxation time (s)"
            title = f"Kinetics vs potential - {probe} (95% CI)"

        if not series:
            self.ladder_canvas.show_message("Nothing selected under Show.")
            return
        # Say so when the spread is what is making the plot unreadable, rather than
        # leaving the user to work out why everything is flat at zero.
        spread = self._needs_review_spread(series, flags)
        if spread is not None and not self.log_y_check.isChecked():
            title += f"  —  {spread}"
        self.ladder_canvas.plot_series(xs, series, "Potential doped to (V)", ylabel,
                                       title=title, styles=styles, yerr=errors,
                                       flags=flags,
                                       logy=self.log_y_check.isChecked())


class AllFitsDialog(QDialog):
    """Every fit in one table, so a run can be reviewed across potentials.

    Dean: "there needs to be a table somewhere that holds fit data for all potentials.
    There is no way currently to review that data." The tab's own table answers "what
    did the three traces of THIS segment do"; this answers "what did the run do".

    Read-only but selectable, so rows can be copied out until the CSV export in TODO.md
    exists.
    """

    COLUMNS = ("segment", "V", "direction", "trace", "lambda (nm)", "model",
               "tau (s)", "beta", "mean tau (s)", "95% CI", "status")

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("All fits")
        self.resize(1000, 460)
        layout = QVBoxLayout(self)

        table = QTableWidget(len(rows), len(self.COLUMNS), self)
        table.setHorizontalHeaderLabels(list(self.COLUMNS))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)

        def cell(value, fmt="{:.4g}"):
            return "" if value is None else (fmt.format(value)
                                             if isinstance(value, float) else str(value))

        for r, (label, potential, direction, trace, wavelength, fit) in enumerate(rows):
            if fit.did_not_converge:
                status = "did not converge"
            elif fit.needs_review:
                status = f"NEEDS REVIEW — {fit.reason}"
            else:
                status = "ok"
            values = [label, cell(potential, "{:+.3f}"), direction, trace,
                      cell(wavelength, "{:.1f}") if trace == "absorbance" else "",
                      fit.model, cell(fit.tau),
                      cell(fit.beta) if fit.beta is not None else "-",
                      cell(fit.mean_tau), cell(fit.mean_tau_ci95), status]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if fit.needs_review or fit.did_not_converge:
                    item.setToolTip(status)
                table.setItem(r, c, item)

        table.resizeColumnsToContents()
        layout.addWidget(table)

        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
