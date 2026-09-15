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
    QSplitter, QMessageBox, QHeaderView,
)
from qtpy.QtCore import Qt

from spec_echem.analysis import (
    MODELS, default_fit_start, fit_transient, probe_wavelength, tau_ratio,
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
        form.addRow("Model:", self.model_combo)

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
        self.start_spin.setToolTip(
            "First point the fit uses. Raise it to exclude the capacitive spike,\n"
            "then refit and watch the residual panel.")
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
        self.auto_start_check = QCheckBox("auto start")
        self.auto_start_check.setChecked(True)
        self.auto_start_check.setToolTip(
            "Sets the start to the time of peak |I|. On a potential step the\n"
            "capacitive spike peaks at the FIRST sample, so in practice this is\n"
            "t = 0 and excludes nothing -- the box beside it shows the value it\n"
            "chose. UNCHECK IT to type a start time and cut the spike.")
        self.auto_start_check.toggled.connect(self._sync_start_enabled)
        self.start_spin.valueChanged.connect(self._draw_fit)
        self.stop_spin.valueChanged.connect(self._draw_fit)
        span.addWidget(self.start_spin)
        span.addWidget(QLabel("to"))
        span.addWidget(self.stop_spin)
        span.addWidget(self.auto_start_check)
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
        self._sync_start_enabled(True)
        self.fit_canvas.show_message("Fit a segment to see the data and its fit.")
        self.ladder_canvas.show_message(
            "Fit a segment to build this plot.")

    def _sync_start_enabled(self, auto):
        self.start_spin.setEnabled(not auto)

    # --- data ------------------------------------------------------------

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
        t, i, _q = self._echem_traces(label)
        if t is not None and len(t) and self.auto_start_check.isChecked():
            start = default_fit_start(t, i)
            if start is not None:
                # Blocked: setValue emits valueChanged, which would draw the fit
                # a second time on every segment change.
                self.start_spin.blockSignals(True)
                self.start_spin.setValue(start)
                self.start_spin.blockSignals(False)
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
        start = self.start_spin.value() if not self.auto_start_check.isChecked() else None
        if start is None and "current" in traces:
            start = default_fit_start(*traces["current"])
        return start, (self.stop_spin.value() or None)

    # --- display ---------------------------------------------------------

    def _show_fits(self, fits):
        for row, trace in enumerate(TRACES):
            fit = (fits or {}).get(trace)
            if fit is None:
                cells = ["", ""]
            elif not fit.ok:
                # Just "failed" here: the column is narrow and the full reason is on
                # the plot in the red banner. The tooltip carries it too, so the
                # numbers are reachable without switching traces.
                cells = ["", "failed"]
            else:
                ci = fit.mean_tau_ci95
                mean = fit.mean_tau
                cells = [f"{fit.beta:.3g}" if fit.beta is not None else "-",
                         f"{mean:.4g} +/- {ci:.2g}" if ci is not None
                         else f"{mean:.4g} (CI unavailable)"]
            for col, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text)
                if fit is not None and not fit.ok:
                    item.setToolTip(fit.reason)
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
        if fit is None:
            note = "not fitted yet"
            fit_y = None
        elif not fit.ok:
            # Not truncated: the reason carries the numbers that tell you what
            # to change (a tau of 6e4 in a 30 s window says widen or change
            # model). The canvas wraps it.
            note = f"FIT FAILED\n{fit.reason}"
            # A rejected fit that CONVERGED still has a curve, and seeing it is
            # how you work out what to change: flat through a real decay means
            # change the model, hugging the spike means move the window.
            fit_y = fit.curve(t)
        else:
            # Every fitted parameter, not just the headline tau -- a biexp's fast
            # component is the whole reason for choosing biexp.
            note = "\n".join(fit.describe())
            fit_y = fit.curve(t)

        title = f"{self._segment_display(label)} - {trace}"
        if trace == "absorbance" and self.wavelength_spin.value() > 0:
            title += f" @ {self.wavelength_spin.value():.1f} nm"
        elif trace == "absorbance" and self._wavelength is not None:
            title += f" @ {self._wavelength:.1f} nm (auto)"

        self.fit_canvas.plot_fit(t, y, fit_y, "Time (s)", TRACE_UNITS[trace],
                                 title=title, window=self._window(traces), note=note,
                                 fit_ok=bool(fit is not None and fit.ok))

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
        series, styles, errors = {}, {}, {}

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
            ylabel = "mean tau(abs) / mean tau(current)"
            title = f"Kinetic coupling (dimensionless) - {probe} (95% CI)"
        else:
            for trace in TRACES:
                if not self.trace_checks[trace].isChecked():
                    continue
                for direction in ("doping", "dedoping"):
                    vals, errs = [np.nan] * len(xs), [np.nan] * len(xs)
                    present = False
                    for x, d, _label, fits in rows:
                        if d != direction or trace not in fits:
                            continue
                        present = True
                        fit = fits[trace]
                        vals[at[x]] = fit.mean_tau if fit.ok else np.nan
                        ci = fit.mean_tau_ci95 if fit.ok else None
                        errs[at[x]] = np.nan if ci is None else ci
                    if present:
                        add(f"{trace} ({direction})", vals, trace, direction, errs)
            ylabel = "mean relaxation time (s)"
            title = f"Kinetics vs potential - {probe} (95% CI)"

        if not series:
            self.ladder_canvas.show_message("Nothing selected under Show.")
            return
        self.ladder_canvas.plot_series(xs, series, "Potential doped to (V)", ylabel,
                                       title=title, styles=styles, yerr=errors)
