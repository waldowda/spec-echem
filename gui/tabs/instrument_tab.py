"""
Tab 1 — Instrument Setup.

Connect the spectrometer (real or simulated), tune integration time / scan
averages (with an inline timing test), show a phase-aware potentiostat status,
collect dark / reference spectra with a live preview, and test-measure in raw
counts or absorbance.
"""
import numpy as np
from datetime import datetime
from pathlib import Path
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QScrollArea,
    QPushButton, QLabel, QCheckBox, QRadioButton, QDoubleSpinBox, QSpinBox, QFileDialog,
    QApplication, QMessageBox,
)

from spec_echem.fakes import FakeSpectrometer
from spec_echem.linearity import (
    LinearityError, analyze_linearity, find_saturation_time, measure_linearity_series,
)
from spec_echem.potentiostat import TOOLKITPY_AVAILABLE, probe_identity
from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.spectral_range import recommend_wavelength_range
from gui.widgets.plot_canvas import MplCanvas

try:
    from spec_echem import AvantesSpectrometer
except ImportError:
    AvantesSpectrometer = None


def _next_serial_path(folder, date, kind, ext=".txt"):
    """First unused ``{date}_{kind}_NNN{ext}`` in folder — a per-day serial so
    multiple saves in one day don't collide. (Overwriting is still possible by
    choosing an existing name in the Save dialog.) kind is "dark"/"ref"/"settings"."""
    n = 1
    while (folder / f"{date}_{kind}_{n:03d}{ext}").exists():
        n += 1
    return folder / f"{date}_{kind}_{n:03d}{ext}"


class InstrumentTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._pstat_connected = False   # last Connect-Potentiostat verify succeeded
        self._last_test_abs = None      # most recent test-absorbance, for Suggest
        self._lin_recommended = None    # integration time from the last linearity check
        self._build()

    def _build(self):
        # Scrollable body: four graphs (dark/ref + counts/absorbance) make this tall.
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # --- Connection ---
        conn_group = QGroupBox("Spectrometer Connection")
        conn_layout = QVBoxLayout(conn_group)
        self.simulated_check = QCheckBox("Simulated (no hardware)")
        if AvantesSpectrometer is None:
            self.simulated_check.setChecked(True)
            self.simulated_check.setEnabled(False)
            self.simulated_check.setText("Simulated (no hardware) — avaspec SDK not available")
        row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect Spectrometer")
        self.connect_btn.clicked.connect(self.on_connect)
        self.spec_status = QLabel("● Not connected")
        self.spec_status.setStyleSheet("color: #b00;")
        row.addWidget(self.connect_btn)
        row.addWidget(self.spec_status)
        row.addStretch()
        conn_layout.addWidget(self.simulated_check)
        conn_layout.addLayout(row)

        # --- Spectrometer settings (incl. timing test, which depends on these) ---
        settings_group = QGroupBox("Spectrometer Settings")
        form = QFormLayout(settings_group)
        self.integration_spin = QDoubleSpinBox()
        self.integration_spin.setRange(0.0001, 10000.0)
        # 4 decimals: working integration times are ~0.02-0.11 ms, so 3 decimals
        # would round the linearity recommendation to ~2 significant figures.
        self.integration_spin.setDecimals(4)
        self.integration_spin.setSuffix(" ms")
        self.averages_spin = QSpinBox()
        self.averages_spin.setRange(1, 100000)
        self.apply_btn = QPushButton("Apply to Spectrometer")
        self.apply_btn.clicked.connect(self.on_apply)
        form.addRow("Integration time:", self.integration_spin)
        form.addRow("Scan averages:", self.averages_spin)
        form.addRow(self.apply_btn)
        timing_row = QHBoxLayout()
        self.timing_btn = QPushButton("Run Timing Test")
        self.timing_btn.clicked.connect(self.on_timing_test)
        self.timing_result = QLabel("—")
        self.timing_result.setStyleSheet("color: #555;")
        timing_row.addWidget(self.timing_btn)
        timing_row.addWidget(self.timing_result)
        timing_row.addStretch()
        form.addRow("Timing:", self._wrap(timing_row))

        # --- Potentiostat control mode ---
        pstat_group = QGroupBox("Potentiostat")
        pstat_layout = QVBoxLayout(pstat_group)
        self.pstat_external_radio = QRadioButton(
            "External — start the Gamry sequence in Gamry Framework")
        self.pstat_python_radio = QRadioButton(
            "Python — drive the Gamry from here (EchemToolkitPy)")
        self.pstat_external_radio.setChecked(True)
        if not TOOLKITPY_AVAILABLE:
            self.pstat_python_radio.setEnabled(False)
            self.pstat_python_radio.setText(
                "Python — drive the Gamry from here (EchemToolkitPy) — toolkitpy not available")
        pstat_layout.addWidget(self.pstat_external_radio)
        pstat_layout.addWidget(self.pstat_python_radio)

        # Connect (Python mode): verify the Gamry is reachable + show its name/serial,
        # mirroring the spectrometer's Connect button + status dot.
        connect_row = QHBoxLayout()
        self.pstat_connect_btn = QPushButton("Connect Potentiostat")
        self.pstat_connect_btn.clicked.connect(self.on_connect_pstat)
        self.pstat_status = QLabel("● Runs from Gamry Framework")
        self.pstat_status.setStyleSheet("color: #555;")
        connect_row.addWidget(self.pstat_connect_btn)
        connect_row.addWidget(self.pstat_status)
        connect_row.addStretch()
        pstat_layout.addLayout(connect_row)

        # Python mode only: also save Gamry-native .DTA files alongside the clean .txt
        self.save_dta_check = QCheckBox("Also save Gamry .DTA files (dta/ subfolder)")
        self.save_dta_check.setChecked(True)
        self.save_dta_check.setToolTip(
            "Python mode: write native Gamry .DTA files (for Echem Analyst / archival) "
            "into a dta/ subfolder, alongside the clean analysis .txt files.")
        pstat_layout.addWidget(self.save_dta_check)

        self.pstat_external_radio.toggled.connect(self._update_pstat_controls)
        self._update_pstat_controls()

        # --- Linearity check (sits beside Spectrometer Settings, which it feeds) ---
        lin_group = QGroupBox("Linearity Check")
        lin_col = QVBoxLayout(lin_group)
        lin_note = QLabel(
            "Run with the reference solution in place and the lamp on. "
            "Ramps integration time to saturation, fits the linear region, and "
            "recommends a time 5% below the limit of linearity.")
        lin_note.setWordWrap(True)
        lin_note.setStyleSheet("color: #555;")
        lin_col.addWidget(lin_note)

        lin_form = QHBoxLayout()
        self.lin_start_spin = QDoubleSpinBox()
        self.lin_start_spin.setRange(0.0001, 10000.0)
        self.lin_start_spin.setDecimals(4)
        self.lin_start_spin.setSuffix(" ms")
        self.lin_start_spin.setToolTip("Lowest integration time in the ramp — must be safely linear")
        self.lin_stop_spin = QDoubleSpinBox()
        self.lin_stop_spin.setRange(0.0001, 10000.0)
        self.lin_stop_spin.setDecimals(4)
        self.lin_stop_spin.setSuffix(" ms")
        self.lin_stop_spin.setValue(0.150)
        self.lin_stop_spin.setToolTip("Highest integration time — should reach saturation")
        self.lin_steps_spin = QSpinBox()
        self.lin_steps_spin.setRange(5, 200)
        self.lin_steps_spin.setValue(20)
        self.lin_tol_spin = QDoubleSpinBox()
        self.lin_tol_spin.setRange(0.1, 25.0)
        self.lin_tol_spin.setDecimals(1)
        self.lin_tol_spin.setSingleStep(0.5)
        self.lin_tol_spin.setValue(2.0)
        self.lin_tol_spin.setSuffix(" %")
        self.lin_tol_spin.setToolTip(
            "Call the limit where the response falls this far below the fitted line")
        self.lin_fill_spin = QDoubleSpinBox()
        self.lin_fill_spin.setRange(10.0, 99.0)
        self.lin_fill_spin.setDecimals(0)
        self.lin_fill_spin.setValue(85.0)
        self.lin_fill_spin.setSuffix(" %")
        self.lin_fill_spin.setToolTip(
            "Keep the peak at/below this fraction of ADC full scale. The detector can "
            "stay linear almost to the clip, so this — not linearity — usually sets the "
            "working point, and leaves headroom for lamp drift.")
        for label, widget in (("Start:", self.lin_start_spin), ("Stop:", self.lin_stop_spin),
                              ("Steps:", self.lin_steps_spin), ("Tol:", self.lin_tol_spin),
                              ("Max fill:", self.lin_fill_spin)):
            lin_form.addWidget(QLabel(label))
            lin_form.addWidget(widget)
        lin_form.addStretch()
        lin_col.addLayout(lin_form)

        lin_btns = QHBoxLayout()
        self.lin_find_sat_btn = QPushButton("Find saturation")
        self.lin_find_sat_btn.setToolTip(
            "Double the integration time until the detector saturates, and put that in Stop")
        self.lin_find_sat_btn.clicked.connect(self.on_find_saturation)
        self.lin_run_btn = QPushButton("Run Linearity Check")
        self.lin_run_btn.clicked.connect(self.on_linearity_check)
        self.lin_use_btn = QPushButton("Use recommended")
        self.lin_use_btn.setToolTip("Copy the recommended integration time into Spectrometer Settings")
        self.lin_use_btn.clicked.connect(self.on_use_recommended)
        self.lin_use_btn.setEnabled(False)
        lin_btns.addWidget(self.lin_find_sat_btn)
        lin_btns.addWidget(self.lin_run_btn)
        lin_btns.addWidget(self.lin_use_btn)
        lin_btns.addStretch()
        lin_col.addLayout(lin_btns)

        self.lin_result = QLabel("Connect, then run the check.")
        self.lin_result.setWordWrap(True)
        self.lin_result.setStyleSheet("color: #888;")
        lin_col.addWidget(self.lin_result)
        self.lin_canvas = MplCanvas(xlabel="Integration time (ms)", ylabel="Counts (peak pixel)")
        self.lin_canvas.setMinimumHeight(180)
        lin_col.addWidget(self.lin_canvas)

        # Top row: Spectrometer Connection | Potentiostat side by side (half width
        # each); Spectrometer Settings | Linearity Check likewise beneath.
        top_row = QHBoxLayout()
        top_row.addWidget(conn_group, stretch=1)
        top_row.addWidget(pstat_group, stretch=1)
        layout.addLayout(top_row)
        settings_row = QHBoxLayout()
        settings_row.addWidget(settings_group, stretch=1)
        settings_row.addWidget(lin_group, stretch=1)
        layout.addLayout(settings_row)

        # --- Dark / Reference: two graphs side by side, controls above each ---
        cal_row = QHBoxLayout()

        dark_box = QGroupBox("Dark")
        dark_col = QVBoxLayout(dark_box)
        dark_btns = QHBoxLayout()
        self.collect_dark_btn = QPushButton("Collect New")
        self.collect_dark_btn.clicked.connect(self.on_collect_dark)
        self.save_dark_btn = QPushButton("Save")
        self.save_dark_btn.clicked.connect(self.on_save_dark)
        self.load_dark_btn = QPushButton("Load")
        self.load_dark_btn.clicked.connect(self.on_load_dark)
        dark_btns.addWidget(self.collect_dark_btn)
        dark_btns.addWidget(self.save_dark_btn)
        dark_btns.addWidget(self.load_dark_btn)
        dark_btns.addStretch()
        self.dark_status = QLabel("Dark: none")
        self.dark_canvas = MplCanvas(ylabel="Intensity (counts)")
        self.dark_canvas.setMinimumHeight(180)
        dark_col.addLayout(dark_btns)
        dark_col.addWidget(self.dark_status)
        dark_col.addWidget(self.dark_canvas)
        cal_row.addWidget(dark_box, stretch=1)

        ref_box = QGroupBox("Reference (100%T)")
        ref_col = QVBoxLayout(ref_box)
        ref_btns = QHBoxLayout()
        self.collect_ref_btn = QPushButton("Collect New")
        self.collect_ref_btn.clicked.connect(self.on_collect_ref)
        self.save_ref_btn = QPushButton("Save")
        self.save_ref_btn.clicked.connect(self.on_save_ref)
        self.load_ref_btn = QPushButton("Load")
        self.load_ref_btn.clicked.connect(self.on_load_ref)
        ref_btns.addWidget(self.collect_ref_btn)
        ref_btns.addWidget(self.save_ref_btn)
        ref_btns.addWidget(self.load_ref_btn)
        ref_btns.addStretch()
        self.ref_status = QLabel("Reference: none")
        self.ref_canvas = MplCanvas(ylabel="Intensity (counts)")
        self.ref_canvas.setMinimumHeight(180)
        ref_col.addLayout(ref_btns)
        ref_col.addWidget(self.ref_status)
        ref_col.addWidget(self.ref_canvas)
        cal_row.addWidget(ref_box, stretch=1)
        layout.addLayout(cal_row)

        # --- Test measurement: counts and absorbance, side by side ---
        test_row = QHBoxLayout()

        counts_box = QGroupBox("Test (counts)")
        counts_col = QVBoxLayout(counts_box)
        counts_btns = QHBoxLayout()
        self.test_counts_btn = QPushButton("Measure counts")
        self.test_counts_btn.clicked.connect(self.on_test_counts)
        counts_btns.addWidget(self.test_counts_btn)
        counts_btns.addStretch()
        self.counts_label = QLabel("Connect, then measure to preview.")
        self.counts_label.setStyleSheet("color: #888;")
        self.counts_canvas = MplCanvas(ylabel="Intensity (counts)")
        self.counts_canvas.setMinimumHeight(180)
        counts_col.addLayout(counts_btns)
        counts_col.addWidget(self.counts_label)
        counts_col.addWidget(self.counts_canvas)
        test_row.addWidget(counts_box, stretch=1)

        absorb_box = QGroupBox("Test (absorbance)")
        absorb_col = QVBoxLayout(absorb_box)
        absorb_btns = QHBoxLayout()
        self.test_absorb_btn = QPushButton("Measure absorbance")
        self.test_absorb_btn.clicked.connect(self.on_test_absorbance)
        self.test_absorb_btn.setToolTip("Needs a dark and a reference first")
        absorb_btns.addWidget(self.test_absorb_btn)
        absorb_btns.addStretch()
        self.absorb_label = QLabel("Needs a dark and a reference first.")
        self.absorb_label.setStyleSheet("color: #888;")
        self.absorb_canvas = MplCanvas(ylabel="Absorbance")
        self.absorb_canvas.setMinimumHeight(180)
        absorb_col.addLayout(absorb_btns)
        absorb_col.addWidget(self.absorb_label)
        absorb_col.addWidget(self.absorb_canvas)
        test_row.addWidget(absorb_box, stretch=1)
        layout.addLayout(test_row)

        # --- Usable wavelength window (crop the noisy lamp edges) ---
        wl_box = QGroupBox("Usable wavelength window (crops noisy lamp edges from what's collected)")
        wl_col = QVBoxLayout(wl_box)
        wl_row = QHBoxLayout()
        wl_row.addWidget(QLabel("Range:"))
        self.wl_min_spin = QDoubleSpinBox()
        self.wl_min_spin.setRange(0.0, 5000.0)
        self.wl_min_spin.setSuffix(" nm")
        self.wl_min_spin.setValue(380.0)
        self.wl_max_spin = QDoubleSpinBox()
        self.wl_max_spin.setRange(0.0, 5000.0)
        self.wl_max_spin.setSuffix(" nm")
        self.wl_max_spin.setValue(1100.0)
        wl_row.addWidget(self.wl_min_spin)
        wl_row.addWidget(QLabel("to"))
        wl_row.addWidget(self.wl_max_spin)
        wl_row.addSpacing(16)
        wl_row.addWidget(QLabel("Max noise:"))
        self.wl_maxnoise_spin = QDoubleSpinBox()
        self.wl_maxnoise_spin.setRange(0.001, 0.5)
        self.wl_maxnoise_spin.setDecimals(3)
        self.wl_maxnoise_spin.setSingleStep(0.005)
        self.wl_maxnoise_spin.setValue(0.010)
        self.wl_maxnoise_spin.setSuffix(" OD")
        self.wl_maxnoise_spin.setToolTip(
            "Trim where the test-abs noise exceeds this (set it small vs your OD signal)")
        self.wl_maxnoise_spin.valueChanged.connect(self._on_maxnoise_changed)
        wl_row.addWidget(self.wl_maxnoise_spin)
        self.wl_suggest_btn = QPushButton("Suggest from test-abs")
        self.wl_suggest_btn.setToolTip("Recommend a range from the last test-absorbance")
        self.wl_suggest_btn.clicked.connect(self.on_wl_suggest)
        self.wl_apply_btn = QPushButton("Apply range")
        self.wl_apply_btn.setToolTip("Restrict the spectrometer (and this run's output) to this range")
        self.wl_apply_btn.clicked.connect(self.on_wl_apply)
        wl_row.addWidget(self.wl_suggest_btn)
        wl_row.addWidget(self.wl_apply_btn)
        wl_row.addStretch()
        self.wl_rationale = QLabel("Full range by default. Take dark + reference + a test-absorbance, "
                                   "then Suggest to trim the noisy edges (you can override).")
        self.wl_rationale.setStyleSheet("color: #888;")
        self.wl_rationale.setWordWrap(True)
        wl_col.addLayout(wl_row)
        wl_col.addWidget(self.wl_rationale)
        layout.addWidget(wl_box)

        self._set_actions_enabled(False)

    def _wrap(self, inner_layout):
        box = QWidget()
        box.setLayout(inner_layout)
        return box

    # --- settings round-trip ---

    def populate_from(self, settings):
        self.integration_spin.setValue(settings["integration_time_ms"])
        self.averages_spin.setValue(settings["scan_averages"])
        # The linearity ramp starts from the working integration time.
        self.lin_start_spin.setValue(settings["integration_time_ms"])
        wl_min, wl_max = settings.get("wavelength_min"), settings.get("wavelength_max")
        if wl_min is not None:
            self.wl_min_spin.setValue(wl_min)
        if wl_max is not None:
            self.wl_max_spin.setValue(wl_max)
        mode = settings.get("potentiostat_mode", "external")
        if mode == "python" and self.pstat_python_radio.isEnabled():
            self.pstat_python_radio.setChecked(True)
        else:
            self.pstat_external_radio.setChecked(True)
        self.save_dta_check.setChecked(settings.get("save_dta", True))

    def collect_into(self, settings):
        settings["integration_time_ms"] = self.integration_spin.value()
        settings["scan_averages"] = self.averages_spin.value()
        # Record the window actually in effect (what the run will use), so the run
        # metadata is accurate; fall back to the spin values before connecting.
        if self.win.wavelengths is not None and len(self.win.wavelengths):
            settings["wavelength_min"] = float(self.win.wavelengths[0])
            settings["wavelength_max"] = float(self.win.wavelengths[-1])
        else:
            settings["wavelength_min"] = self.wl_min_spin.value()
            settings["wavelength_max"] = self.wl_max_spin.value()
        settings["potentiostat_mode"] = (
            "python" if self.pstat_python_radio.isChecked() else "external")
        settings["save_dta"] = self.save_dta_check.isChecked()

    # --- actions ---

    def _set_actions_enabled(self, enabled):
        self._actions_enabled = enabled
        for w in (self.apply_btn, self.collect_dark_btn, self.collect_ref_btn,
                  self.test_counts_btn, self.timing_btn, self.wl_apply_btn):
            w.setEnabled(enabled)
        # Connect re-inits toolkitpy; forbid it during a run so it can't collide
        # with a Python-mode run driving the Gamry. Restore its normal (python +
        # toolkitpy) state when the run ends.
        if enabled:
            self._update_pstat_controls()
        else:
            self.pstat_connect_btn.setEnabled(False)
        self._update_absorbance_enabled()

    def lock_for_run(self, locked):
        """Lock the spectrometer Connect + Simulated toggle while a run is active.
        These are normally always live (so they're NOT in _set_actions_enabled, which
        also runs at startup before connecting) — but mid-run a Connect would swap
        win.spec out from under the running worker AND re-enable every locked control
        via on_connect -> _set_actions_enabled(True). Locking them keeps both
        instruments' Connect buttons disabled during a run and live otherwise.
        Simulated stays force-disabled when avaspec is unavailable."""
        self.connect_btn.setEnabled(not locked)
        self.simulated_check.setEnabled(not locked and AvantesSpectrometer is not None)

    def _update_absorbance_enabled(self):
        ready = self.win.spec is not None and self.win.dark is not None and self.win.ref is not None
        self.test_absorb_btn.setEnabled(ready)
        # Can only save a dark/ref once one has been collected or loaded.
        self.save_dark_btn.setEnabled(self.win.dark is not None)
        self.save_ref_btn.setEnabled(self.win.ref is not None)
        self._update_suggest_enabled()

    def _update_suggest_enabled(self):
        """Suggest needs a test-absorbance and the tab's actions live (connected,
        not mid-run)."""
        self.wl_suggest_btn.setEnabled(
            getattr(self, "_actions_enabled", False) and self._last_test_abs is not None)

    def _set_pstat_status(self, text, color):
        self.pstat_status.setText(text)
        self.pstat_status.setStyleSheet(f"color: {color};")

    def _update_pstat_controls(self):
        python = self.pstat_python_radio.isChecked()
        self.pstat_connect_btn.setEnabled(python and TOOLKITPY_AVAILABLE)
        # .DTA files only exist in Python mode (External writes its own via Framework)
        self.save_dta_check.setEnabled(python)
        if not python:
            self._set_pstat_status("● Runs from Gamry Framework", "#555")
        elif not self._pstat_connected:
            self._set_pstat_status("● Not connected", "#b00")
        # else: keep the green "● Connected — …" so it survives run-end / re-toggle

    def on_connect_pstat(self):
        self._set_pstat_status("● Connecting…", "#555")
        try:
            label, serial = probe_identity()
        except Exception as exc:  # noqa: BLE001 — surface any toolkitpy/hardware failure
            self._pstat_connected = False
            self._set_pstat_status(f"● Connect failed: {exc}", "#b00")
            return
        label = (label or "").strip()
        who = f"{label} (serial {serial})" if label else f"serial {serial}"
        self._pstat_connected = True
        self._set_pstat_status(f"● Connected — {who}", "#080")

    def _update_cal_plot(self):
        self._plot_if_matched(self.dark_canvas, self.win.dark,
                              "Dark", "Intensity (counts)")
        self._plot_if_matched(self.ref_canvas, self.win.ref,
                              "Reference (100%T)", "Intensity (counts)")

    def _plot_if_matched(self, canvas, data, title, ylabel, **kw):
        """Plot data vs the current wavelength axis only if their lengths match;
        otherwise show a note. Prevents length-mismatch crashes when a window has
        been applied while dark/ref are from a different range."""
        wl = self.win.wavelengths
        if data is None:
            return
        if wl is None or len(wl) != len(data):
            canvas.show_message(f"{title}: {len(data)} px doesn't match the "
                                f"{0 if wl is None else len(wl)} px window.")
            return
        canvas.show_spectrum(wl, data, title=title, ylabel=ylabel, **kw)

    def _reconcile_loaded(self, arr):
        """Fit a loaded dark/ref to the current wavelength window: use as-is if it
        already matches, slice a full-range file down to the active window, or
        return None if it can't be matched."""
        wl = self.win.wavelengths
        if wl is None or len(arr) == len(wl):
            return arr
        full = getattr(self, "_full_wl", None)
        if full is not None and len(arr) == len(full):
            i0 = int(np.argmin(np.abs(np.asarray(full) - wl[0])))
            return np.asarray(arr)[i0:i0 + len(wl)]
        return None

    def on_connect(self):
        if self.simulated_check.isChecked() or AvantesSpectrometer is None:
            spec = FakeSpectrometer()
        else:
            spec = AvantesSpectrometer()
        try:
            _, serial = spec.init()
        except Exception as exc:  # noqa: BLE001 — surface any hardware init failure to the user
            self.spec_status.setText(f"● Connect failed: {exc}")
            self.spec_status.setStyleSheet("color: #b00;")
            return
        self.win.spec = spec
        _, self.win.wavelengths = spec.wavelengths()
        # A fresh connection is at the full window; remember it so loaded (full-range)
        # dark/ref files can be sliced to a narrower window later.
        self._full_wl = np.asarray(self.win.wavelengths)
        # Seed the window spin boxes with the spectrometer's actual full range.
        if len(self.win.wavelengths):
            self.wl_min_spin.setValue(float(self.win.wavelengths[0]))
            self.wl_max_spin.setValue(float(self.win.wavelengths[-1]))
        self.spec_status.setText(f"● Connected ({serial})")
        self.spec_status.setStyleSheet("color: #080;")
        self._set_actions_enabled(True)
        self.on_apply()

    def on_apply(self):
        if self.win.spec is None:
            return
        self.win.spec.set_integration_time(self.integration_spin.value())
        self.win.spec.set_scan_averages(self.averages_spin.value())

    def on_collect_dark(self):
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self.win.dark = spectrum
        self.dark_status.setText(f"Dark: collected ({len(spectrum)} px)")
        self._update_cal_plot()
        self._update_absorbance_enabled()

    def _data_root(self):
        """The data root (the Save location on the Parameters tab), falling back to
        the default. All calibration files live in subfolders under it so Save and
        Open land in the same, predictable place."""
        return (self.win.parameters_tab._widgets["data_root"].text()
                or DEFAULT_SETTINGS["data_root"])

    def on_save_dark(self):
        if self.win.dark is None:
            self.dark_status.setText("Dark: nothing to save (collect one first)")
            return
        # Standard darks folder under the current Save location (the parent dir).
        darks_dir = Path(self._data_root()) / "darks"
        darks_dir.mkdir(parents=True, exist_ok=True)
        # Pre-fill the next unused serial for today so same-day darks don't collide;
        # the Save dialog still lets you pick an existing name to overwrite.
        default_path = str(_next_serial_path(darks_dir, datetime.now().strftime("%Y%m%d"), "dark"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Dark Spectrum", default_path, "Text files (*.txt)")
        if not path:
            return
        try:
            np.savetxt(path, self.win.dark)
            self.dark_status.setText(f"Dark: saved ({Path(path).name})")
        except Exception as exc:  # noqa: BLE001
            self.dark_status.setText(f"Dark: save failed ({exc})")

    def on_load_dark(self):
        start = str(Path(self._data_root()) / "darks")
        path, _ = QFileDialog.getOpenFileName(self, "Load Dark Spectrum", start, "Text files (*.txt *.csv)")
        if not path:
            return
        try:
            data = np.loadtxt(path)
            arr = data if data.ndim == 1 else data[:, -1]
            fitted = self._reconcile_loaded(arr)
            if fitted is None:
                self.dark_status.setText(
                    f"Dark: loaded {len(arr)} px doesn't fit the current window — "
                    "reset to full range or load a matching file.")
                return
            self.win.dark = fitted
            self.dark_status.setText(f"Dark: loaded ({len(fitted)} px)")
            self._update_cal_plot()
            self._update_absorbance_enabled()
        except Exception as exc:  # noqa: BLE001
            self.dark_status.setText(f"Dark: load failed ({exc})")

    def on_collect_ref(self):
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self.win.ref = spectrum
        self.ref_status.setText(f"Reference: collected ({len(spectrum)} px)")
        self._update_cal_plot()
        self._update_absorbance_enabled()

    def on_save_ref(self):
        if self.win.ref is None:
            self.ref_status.setText("Reference: nothing to save (collect one first)")
            return
        # Standard refs folder under the current Save location (the parent dir).
        refs_dir = Path(self._data_root()) / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        default_path = str(_next_serial_path(refs_dir, datetime.now().strftime("%Y%m%d"), "ref"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Reference Spectrum", default_path, "Text files (*.txt)")
        if not path:
            return
        try:
            np.savetxt(path, self.win.ref)
            self.ref_status.setText(f"Reference: saved ({Path(path).name})")
        except Exception as exc:  # noqa: BLE001
            self.ref_status.setText(f"Reference: save failed ({exc})")

    def on_load_ref(self):
        start = str(Path(self._data_root()) / "refs")
        path, _ = QFileDialog.getOpenFileName(self, "Load Reference Spectrum", start, "Text files (*.txt *.csv)")
        if not path:
            return
        try:
            data = np.loadtxt(path)
            arr = data if data.ndim == 1 else data[:, -1]
            fitted = self._reconcile_loaded(arr)
            if fitted is None:
                self.ref_status.setText(
                    f"Reference: loaded {len(arr)} px doesn't fit the current window — "
                    "reset to full range or load a matching file.")
                return
            self.win.ref = fitted
            self.ref_status.setText(f"Reference: loaded ({len(fitted)} px)")
            self._update_cal_plot()
            self._update_absorbance_enabled()
        except Exception as exc:  # noqa: BLE001
            self.ref_status.setText(f"Reference: load failed ({exc})")

    def on_test_counts(self):
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self.counts_label.setText(
            f"Counts: {len(spectrum)} px, min={spectrum.min():.0f}  max={spectrum.max():.0f}")
        self._plot_if_matched(self.counts_canvas, spectrum,
                              "Test (counts)", "Intensity (counts)", mark_max=True)

    def on_test_absorbance(self):
        if self.win.spec is None or self.win.dark is None or self.win.ref is None:
            return
        _, spectrum = self.win.spec.measure()
        if not (len(spectrum) == len(self.win.dark) == len(self.win.ref)):
            self.absorb_label.setText(
                "Dark/reference don't match the current window — re-collect them at this range.")
            return
        with np.errstate(divide="ignore", invalid="ignore"):
            transmittance = (spectrum - self.win.dark) / (self.win.ref - self.win.dark)
            absorbance = -np.log10(transmittance)
        self.absorb_label.setText("A = −log₁₀((sample − dark) / (ref − dark))")
        self._plot_if_matched(self.absorb_canvas, absorbance,
                              "Test (absorbance)", "Absorbance")
        self._last_test_abs = np.asarray(absorbance)
        self._update_suggest_enabled()

    def on_timing_test(self):
        if self.win.spec is None:
            return
        _, _, net_dif, t_dif = self.win.spec.measure_timing()
        self.timing_result.setText(
            f"total {t_dif * 1000:.1f} ms  (overhead {net_dif:.1f} ms)")

    # --- linearity check ---

    def _restore_spectrometer_settings(self):
        """Put the user's integration time / averages back after a ramp."""
        self.win.spec.set_integration_time(self.integration_spin.value())
        self.win.spec.set_scan_averages(self.averages_spin.value())

    def on_find_saturation(self):
        """Double the integration time until saturation, and use that as Stop."""
        if self.win.spec is None:
            return
        self.lin_result.setText("Finding saturation…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.win.spec.set_scan_averages(1)
            sat = find_saturation_time(self.win.spec, self.lin_start_spin.value())
        except LinearityError as exc:
            self.lin_result.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — surface hardware errors as a note
            self.lin_result.setText(f"Find saturation failed: {exc}")
            return
        finally:
            self._restore_spectrometer_settings()
            QApplication.restoreOverrideCursor()
        # Stop just past saturation: enough to show the clip, without wasting most
        # of the ramp above it.
        stop = sat["t_sat"] * 1.05
        self.lin_stop_spin.setValue(stop)
        self.lin_result.setText(
            f"Saturates at {sat['t_sat']:.4g} ms. Highest clean point: "
            f"{sat['t_below']:.4g} ms ({sat['counts_below']:.0f} counts). "
            f"Stop set to {stop:.4g} ms — now run the check.")

    def on_linearity_check(self):
        if self.win.spec is None:
            return
        start, stop = self.lin_start_spin.value(), self.lin_stop_spin.value()
        steps = self.lin_steps_spin.value()
        if stop <= start:
            self.lin_result.setText("Stop must be greater than Start.")
            return
        times = np.linspace(start, stop, steps)

        # The ramp runs on the GUI thread: at sub-ms integration times with averaging
        # forced to 1 it finishes in well under a second. The spin allows up to 10 s
        # though, so warn before freezing the UI for a genuinely long one.
        projected_s = float(times.sum()) / 1000.0 + steps * 0.05
        if projected_s > 5.0:
            reply = QMessageBox.question(
                self, "Long linearity ramp",
                f"This ramp will take roughly {projected_s:.0f} s, and the window will "
                "be unresponsive until it finishes.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        self.lin_result.setText("Running…")
        self.lin_use_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Averaging doesn't change saturation, only speed — force 1 for the ramp.
            self.win.spec.set_scan_averages(1)
            used, counts, peak_px = measure_linearity_series(self.win.spec, times)
            result = analyze_linearity(
                used, counts,
                tolerance_pct=self.lin_tol_spin.value(),
                max_fill_frac=self.lin_fill_spin.value() / 100.0)
        except LinearityError as exc:
            self.lin_canvas.show_message(str(exc))
            self.lin_result.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.lin_result.setText(f"Linearity check failed: {exc}")
            return
        finally:
            self._restore_spectrometer_settings()
            QApplication.restoreOverrideCursor()

        title = f"Peak pixel {peak_px}"
        wl = self.win.wavelengths
        if wl is not None and peak_px < len(wl):
            title += f" ({wl[peak_px]:.0f} nm)"
        self.lin_canvas.show_linearity(used, counts, result, title=title)
        self.lin_result.setText(result["summary"])
        self._lin_recommended = result["t_recommended"]
        self.lin_use_btn.setEnabled(True)

    def on_use_recommended(self):
        if self._lin_recommended is None:
            return
        self.integration_spin.setValue(self._lin_recommended)
        self.on_apply()
        self.lin_result.setText(
            f"Integration time set to {self.integration_spin.value():.4g} ms and applied.")

    # --- usable wavelength window ---

    def _on_maxnoise_changed(self, *_):
        # Re-suggest live when the tolerance changes (only if a suggestion is possible).
        if self.wl_suggest_btn.isEnabled():
            self.on_wl_suggest()

    def on_wl_suggest(self):
        if self._last_test_abs is None or self.win.wavelengths is None:
            return
        # Guard against a stale test-abs that no longer matches the current axis.
        if len(self._last_test_abs) != len(self.win.wavelengths):
            self.wl_rationale.setText("Take a fresh test-absorbance at this range, then Suggest.")
            return
        try:
            lo, hi, rationale = recommend_wavelength_range(
                self.win.wavelengths, self._last_test_abs,
                dark=self.win.dark, ref=self.win.ref,
                max_noise=self.wl_maxnoise_spin.value())
        except Exception as exc:  # noqa: BLE001 — surface a bad suggestion as a note
            self.wl_rationale.setText(f"Could not suggest a range: {exc}")
            return
        self.wl_min_spin.setValue(lo)
        self.wl_max_spin.setValue(hi)
        self.wl_rationale.setText(rationale["summary"])

    def on_wl_apply(self):
        if self.win.spec is None:
            return
        wl_min, wl_max = self.wl_min_spin.value(), self.wl_max_spin.value()
        if wl_max <= wl_min:
            self.wl_rationale.setText("Range invalid: max must be greater than min.")
            return
        old_wl = np.asarray(self.win.wavelengths) if self.win.wavelengths is not None else None
        try:
            self.win.spec.set_wavelength_window(wl_min, wl_max)
        except Exception as exc:  # noqa: BLE001
            self.wl_rationale.setText(f"Apply failed: {exc}")
            return
        _, self.win.wavelengths = self.win.spec.wavelengths()
        new_wl = np.asarray(self.win.wavelengths)
        had_cal = self.win.dark is not None or self.win.ref is not None
        self._reslice_cal(old_wl, new_wl)   # keep dark/ref aligned; clear on a widen
        now_cal = self.win.dark is not None
        self._update_cal_plot()
        self._update_absorbance_enabled()
        msg = f"Applied {new_wl[0]:.0f}–{new_wl[-1]:.0f} nm ({len(new_wl)} px)."
        if had_cal and now_cal:
            msg += " Dark/reference re-sliced to match."
        elif had_cal and not now_cal:
            msg += " Dark/reference cleared — re-collect at this range."
        self.wl_rationale.setText(msg)

    def _reslice_cal(self, old_wl, new_wl):
        """After the window narrows, slice dark/ref (aligned with old_wl) down to
        new_wl so they stay matched to the run data — no re-collect. If new_wl isn't
        a sub-range of old_wl (a widen beyond what was collected), clear them."""
        if old_wl is None or (self.win.dark is None and self.win.ref is None):
            return
        contained = (len(new_wl) <= len(old_wl)
                     and new_wl[0] >= old_wl[0] - 1e-6
                     and new_wl[-1] <= old_wl[-1] + 1e-6)
        if contained:
            i0 = int(np.argmin(np.abs(old_wl - new_wl[0])))
            sl = slice(i0, i0 + len(new_wl))
            if self.win.dark is not None and len(self.win.dark) == len(old_wl):
                self.win.dark = np.asarray(self.win.dark)[sl]
            if self.win.ref is not None and len(self.win.ref) == len(old_wl):
                self.win.ref = np.asarray(self.win.ref)[sl]
        else:
            self.win.dark = None
            self.win.ref = None
            self._last_test_abs = None
