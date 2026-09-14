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
    MODELS, auto_wavelengths, default_fit_start, fit_transient, tau_ratio,
)
from spec_echem.data import echem_txt_path, segment_potential, DATA_TYPE_CV
from spec_echem.gamry_data import read_chrono
from gui.widgets.plot_canvas import MplCanvas

# The three traces fitted per segment, in table order.
TRACES = ("absorbance", "current", "charge")


class AnalysisTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._fits = {}          # label -> {trace: FitResult}
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

        # The window. Default start is the time of peak |current| — where the
        # capacitive spike ends. Computed, not guessed (see analysis.default_fit_start).
        span = QHBoxLayout()
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, 100000.0)
        self.start_spin.setDecimals(3)
        self.start_spin.setSuffix(" s")
        self.stop_spin = QDoubleSpinBox()
        self.stop_spin.setRange(0.0, 100000.0)
        self.stop_spin.setDecimals(3)
        self.stop_spin.setSuffix(" s")
        self.auto_start_check = QCheckBox("from current peak")
        self.auto_start_check.setChecked(True)
        self.auto_start_check.setToolTip(
            "Start the fit where the capacitive spike ends — the time of peak |I|.\n"
            "Uncheck to set the start by hand.")
        self.auto_start_check.toggled.connect(self._sync_start_enabled)
        span.addWidget(self.start_spin)
        span.addWidget(QLabel("to"))
        span.addWidget(self.stop_spin)
        span.addWidget(self.auto_start_check)
        form.addRow("Window:", span)

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
        form.addRow("Wavelength:", self.wavelength_spin)

        buttons = QHBoxLayout()
        self.fit_btn = QPushButton("Fit segment")
        self.fit_btn.clicked.connect(self.on_fit_segment)
        self.fit_all_btn = QPushButton("Fit all segments")
        self.fit_all_btn.clicked.connect(self.on_fit_all)
        buttons.addWidget(self.fit_btn)
        buttons.addWidget(self.fit_all_btn)
        buttons.addStretch()
        form.addRow("", buttons)

        layout.addWidget(controls)

        split = QSplitter(Qt.Vertical)

        self.table = QTableWidget(len(TRACES), 4)
        self.table.setHorizontalHeaderLabels(["trace", "tau (s)", "beta", "SD (s)"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, trace in enumerate(TRACES):
            self.table.setItem(row, 0, QTableWidgetItem(trace))
        split.addWidget(self.table)

        plot_box = QWidget()
        plot_layout = QVBoxLayout(plot_box)
        self.ratio_check = QCheckBox("show tau(abs) / tau(current) ratio")
        self.ratio_check.setToolTip(
            "Ratio uses MEAN relaxation times, not raw tau — for a stretched\n"
            "exponential the raw tau is not the physical timescale.\n"
            "A potential with a failed fit leaves a gap rather than being skipped.")
        self.ratio_check.toggled.connect(self._draw_ladder)
        plot_layout.addWidget(self.ratio_check)
        self.ladder_canvas = MplCanvas(self)
        plot_layout.addWidget(self.ladder_canvas)
        split.addWidget(plot_box)

        layout.addWidget(split, stretch=1)
        self._sync_start_enabled(True)

    def _sync_start_enabled(self, auto):
        self.start_spin.setEnabled(not auto)

    # --- data ------------------------------------------------------------

    def refresh_segments(self):
        """Repopulate from the main window's results store, keeping the selection."""
        previous = self.segment_combo.currentText()
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        for label in self.win.results:
            seg = self.win.segments_by_label.get(label)
            # CV is a sweep, not a step — there is no transient to fit.
            if seg is not None and seg.data_type == DATA_TYPE_CV:
                continue
            self.segment_combo.addItem(label)
        if previous:
            i = self.segment_combo.findText(previous)
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
            polaron, _pi = auto_wavelengths(df.values, wl)
            if polaron is None:
                return None, None
            row = int(np.abs(wl - polaron).argmin())
            self._wavelength = polaron
        return np.asarray(df.columns.values, dtype=float), df.values[row, :]

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

    # --- actions ---------------------------------------------------------

    def on_segment_changed(self, *_):
        label = self.segment_combo.currentText()
        if not label:
            return
        t, i, _q = self._echem_traces(label)
        if t is not None and len(t):
            if self.auto_start_check.isChecked():
                start = default_fit_start(t, i)
                if start is not None:
                    self.start_spin.setValue(start)
            if self.stop_spin.value() == 0.0:
                self.stop_spin.setValue(float(t[-1]))
        self._show_fits(self._fits.get(label))

    def on_fit_segment(self):
        label = self.segment_combo.currentText()
        if not label:
            return
        result = self._fit_one(label)
        if result is None:
            QMessageBox.warning(self, "Nothing to fit",
                                f"No usable data for {label}.")
            return
        self._fits[label] = result
        self._show_fits(result)
        self._draw_ladder()

    def on_fit_all(self):
        fitted = 0
        for i in range(self.segment_combo.count()):
            label = self.segment_combo.itemText(i)
            result = self._fit_one(label)
            if result is not None:
                self._fits[label] = result
                fitted += 1
        self._show_fits(self._fits.get(self.segment_combo.currentText()))
        self._draw_ladder()
        if not fitted:
            QMessageBox.warning(self, "Nothing to fit", "No segment had usable data.")

    def _fit_one(self, label):
        model = self.model_combo.currentData()
        t_a, a = self._absorbance_trace(label)
        t_e, i, q = self._echem_traces(label)
        if t_a is None and t_e is None:
            return None
        start = self.start_spin.value() if not self.auto_start_check.isChecked() else None
        if start is None and t_e is not None:
            start = default_fit_start(t_e, i)
        stop = self.stop_spin.value() or None

        out = {}
        if t_a is not None:
            out["absorbance"] = fit_transient(t_a, a, model, start, stop)
        if t_e is not None:
            out["current"] = fit_transient(t_e, i, model, start, stop)
            out["charge"] = fit_transient(t_e, q, model, start, stop)
        return out

    # --- display ---------------------------------------------------------

    def _show_fits(self, fits):
        for row, trace in enumerate(TRACES):
            fit = (fits or {}).get(trace)
            if fit is None:
                cells = ["", "", ""]
            elif not fit.ok:
                # The reason, not a plausible number — curve_fit returns confident
                # nonsense rather than raising.
                cells = [f"failed: {fit.reason[:40]}", "", ""]
            else:
                cells = [f"{fit.tau:.4g}",
                         f"{fit.beta:.3g}" if fit.beta is not None else "-",
                         f"{fit.tau_sd:.2g}"]
            for col, text in enumerate(cells, start=1):
                self.table.setItem(row, col, QTableWidgetItem(text))

    def _draw_ladder(self, *_):
        """tau (or the ratio) against the segment potential, across whatever has been
        fitted so far — so it builds as segments are fitted rather than only at the end."""
        xs, series = [], {t: [] for t in TRACES}
        ratios = []
        for i in range(self.segment_combo.count()):
            label = self.segment_combo.itemText(i)
            seg = self.win.segments_by_label.get(label)
            fits = self._fits.get(label)
            if seg is None or not fits:
                continue
            potential = segment_potential(self.win.settings, seg.data_type,
                                          seg.run_number)
            if potential is None:
                continue
            xs.append(potential)
            for trace in TRACES:
                fit = fits.get(trace)
                series[trace].append(fit.mean_tau if (fit and fit.ok) else np.nan)
            ratios.append(tau_ratio(fits.get("absorbance"), fits.get("current")))

        if not xs:
            self.ladder_canvas.show_message("Fit a segment to build this plot.")
            return
        if self.ratio_check.isChecked():
            # None -> NaN so matplotlib leaves a visible gap: a failed fit is
            # information, and silently dropping the point would hide it.
            ys = [np.nan if r is None else r for r in ratios]
            self.ladder_canvas.plot_series(
                xs, {"tau(abs)/tau(current)": ys},
                "Potential (V)", "mean tau ratio", title="Kinetic coupling")
        else:
            self.ladder_canvas.plot_series(
                xs, {t: series[t] for t in TRACES},
                "Potential (V)", "mean tau (s)", title="Kinetics vs potential")
