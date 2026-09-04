"""
Tab 1 — Instrument Setup.

Connect the spectrometer (real or simulated), tune integration time / scan
averages (with an inline timing test), show a phase-aware potentiostat status,
collect dark / reference spectra with a live preview, and test-measure in raw
counts or absorbance.
"""
import logging
import numpy as np
from datetime import datetime
from pathlib import Path
from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QScrollArea, QGridLayout,
    QPushButton, QLabel, QCheckBox, QRadioButton, QDoubleSpinBox, QSpinBox, QFileDialog,
    QApplication, QMessageBox, QTabWidget, QSizePolicy,
)

# Cap spin boxes so the left column's minimum width stays small — Qt satisfies
# minimum widths before it applies stretch, so a wide left column would starve the
# plot beside it regardless of the stretch factors.
SPIN_W = 110


def _detail_label():
    """A wrapping label for text whose length we don't control — hardware error
    messages, mostly.

    A QLabel inside a layout asks for its full text width and the layout GRANTS it,
    dragging the whole window wider; it does not clip. So any message that varies in
    length needs its own wrapping label, and one that cannot drive the width at all
    (Ignored) or it re-creates the problem at the wrapped width.
    """
    label = QLabel("")
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    label.setStyleSheet("color: #b00;")
    return label

from spec_echem.bench import (
    apply_bench_defaults, load_bench_defaults, save_bench_defaults, user_bench_path,
)
from spec_echem.fakes import FakeSpectrometer
from spec_echem.linearity import (
    LinearityError, analyze_linearity, find_saturation_time, measure_linearity_series,
)
from spec_echem.potentiostat import (
    TOOLKITPY_AVAILABLE, AUTOLAB_AVAILABLE, probe_identity, autolab_identity,
)
from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.acquisition import (SPECTRUM_OVERHEAD_S, spectrum_cost_seconds,
                                    suggest_scan_averages)
from spec_echem.experiment import build_segments
from spec_echem.spectral_range import recommend_wavelength_range
from gui.widgets.plot_canvas import MplCanvas

# Under the spec_echem package logger so setup actions — which all happen before any
# run exists — land in the app log rather than vanishing.
logger = logging.getLogger("spec_echem.gui.instrument")

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
        self._last_test_spectrum = None  # most recent Test scan, RAW counts (transient)
        self._last_test_abs = None       # same scan as absorbance, for Suggest
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
        self.spec_detail = _detail_label()
        conn_layout.addWidget(self.simulated_check)
        conn_layout.addLayout(row)
        conn_layout.addWidget(self.spec_detail)

        # --- Spectrometer settings (incl. timing test, which depends on these) ---
        settings_group = QGroupBox("Spectrometer Settings")
        form = QFormLayout(settings_group)
        self.integration_spin = QDoubleSpinBox()
        self.integration_spin.setRange(0.0001, 10000.0)
        # 4 decimals: working integration times are ~0.02-0.11 ms, so 3 decimals
        # would round the linearity recommendation to ~2 significant figures.
        self.integration_spin.setDecimals(4)
        self.integration_spin.setSuffix(" ms")
        self.integration_spin.setMaximumWidth(SPIN_W)
        self.averages_spin = QSpinBox()
        self.averages_spin.setRange(1, 100000)
        self.averages_spin.setMaximumWidth(SPIN_W)
        self.apply_btn = QPushButton("Apply to Spectrometer")
        self.apply_btn.clicked.connect(self.on_apply)
        form.addRow("Integration time:", self.integration_spin)
        form.addRow("Scan averages:", self.averages_spin)
        # Advisory only. The cost of a spectrum is set here; the step it has to fit
        # inside is set on the Parameters tab — so the collision between them is
        # invisible on either tab alone. Nothing below blocks a run, and every path
        # through it is wrapped: a broken advisory must never break the tab.
        self.cadence_note = QLabel("—")
        self.cadence_note.setWordWrap(True)
        form.addRow("Per spectrum:", self.cadence_note)
        self.integration_spin.valueChanged.connect(self._update_cadence_note)
        self.averages_spin.valueChanged.connect(self._update_cadence_note)
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

        # Wavelength range: a spectrometer setting like the two above. Normally set BY EYE
        # once per lamp/ND combo, during setup and BEFORE dark/ref — so dark, reference and
        # the run are all collected at the same cropped length. (The Test-absorbance tab can
        # suggest values into these boxes, but that's the rare path, not the normal one.)
        wl_range_row = QHBoxLayout()
        self.wl_min_spin = QDoubleSpinBox()
        self.wl_min_spin.setRange(0.0, 5000.0)
        self.wl_min_spin.setSuffix(" nm")
        self.wl_min_spin.setMaximumWidth(SPIN_W)
        self.wl_max_spin = QDoubleSpinBox()
        self.wl_max_spin.setRange(0.0, 5000.0)
        self.wl_max_spin.setSuffix(" nm")
        self.wl_max_spin.setMaximumWidth(SPIN_W)
        self.wl_min_spin.setValue(DEFAULT_SETTINGS["wavelength_min"] or 380.0)
        self.wl_max_spin.setValue(DEFAULT_SETTINGS["wavelength_max"] or 1100.0)
        wl_range_row.addWidget(self.wl_min_spin)
        wl_range_row.addWidget(QLabel("to"))
        wl_range_row.addWidget(self.wl_max_spin)
        wl_range_row.addStretch()
        form.addRow("Wavelength range:", self._wrap(wl_range_row))

        wl_btn_row = QHBoxLayout()
        self.wl_apply_btn = QPushButton("Apply range")
        self.wl_apply_btn.setToolTip(
            "Restrict the spectrometer (and this run's output) to this range. "
            "Re-slices an existing dark/reference to match; clears them if you widen "
            "beyond what they cover.")
        self.wl_apply_btn.clicked.connect(self.on_wl_apply)
        self.wl_reset_btn = QPushButton("Reset")
        self.wl_reset_btn.setToolTip("Back to the spectrometer's full range (no crop)")
        self.wl_reset_btn.clicked.connect(self.on_wl_reset)
        wl_btn_row.addWidget(self.wl_apply_btn)
        wl_btn_row.addWidget(self.wl_reset_btn)
        wl_btn_row.addStretch()
        form.addRow("", self._wrap(wl_btn_row))
        self.wl_status = QLabel("Full range.")
        self.wl_status.setStyleSheet("color: #888;")
        self.wl_status.setWordWrap(True)
        form.addRow("", self.wl_status)

        # --- Potentiostat control mode ---
        pstat_group = QGroupBox("Potentiostat")
        pstat_layout = QVBoxLayout(pstat_group)
        self.pstat_external_radio = QRadioButton(
            "External — start the Gamry sequence in Gamry Framework")
        self.pstat_python_radio = QRadioButton(
            "Python — drive the Gamry from here (EchemToolkitPy)")
        self.pstat_autolab_radio = QRadioButton(
            "Autolab — drive a Metrohm Autolab from here (Autolab SDK)")
        # External stays the default on every machine: it is the proven path and the
        # only one that works with no vendor stack installed.
        self.pstat_external_radio.setChecked(True)
        if not TOOLKITPY_AVAILABLE:
            self.pstat_python_radio.setEnabled(False)
            self.pstat_python_radio.setText(
                "Python — drive the Gamry from here (EchemToolkitPy) — toolkitpy not available")
        if not AUTOLAB_AVAILABLE:
            self.pstat_autolab_radio.setEnabled(False)
            self.pstat_autolab_radio.setText(
                "Autolab — drive a Metrohm Autolab from here (Autolab SDK) — pythonnet not available")
        pstat_layout.addWidget(self.pstat_external_radio)
        pstat_layout.addWidget(self.pstat_python_radio)
        pstat_layout.addWidget(self.pstat_autolab_radio)

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
        self.pstat_detail = _detail_label()
        pstat_layout.addLayout(connect_row)
        pstat_layout.addWidget(self.pstat_detail)

        # Python mode only: also save Gamry-native .DTA files alongside the clean .txt
        self.save_dta_check = QCheckBox("Also save Gamry .DTA files (dta/ subfolder)")
        self.save_dta_check.setChecked(True)
        self.save_dta_check.setToolTip(
            "Python mode: write native Gamry .DTA files (for Echem Analyst / archival) "
            "into a dta/ subfolder, alongside the clean analysis .txt files.")
        pstat_layout.addWidget(self.save_dta_check)

        self.pstat_external_radio.toggled.connect(self._update_pstat_controls)
        self.pstat_autolab_radio.toggled.connect(self._update_pstat_controls)
        self._update_pstat_controls()

        # --- Linearity check (sits beside Spectrometer Settings, which it feeds) ---
        lin_group = QGroupBox("Linearity Check")
        lin_col = QVBoxLayout(lin_group)
        lin_note = QLabel("Run with the reference solution in place and the lamp on.")
        lin_note.setWordWrap(True)
        lin_note.setStyleSheet("color: #555;")
        lin_note.setToolTip(
            "Ramps integration time to saturation, fits the linear region, and recommends "
            "a working integration time — the tighter of 5% below the limit of linearity "
            "or the Max fill fraction of ADC full scale.")
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
        # Two compact rows, not one long one: a single row of five label+spin pairs gave
        # the left column a large MINIMUM width, and Qt honours minimums before it
        # applies stretch — so the left box hogged the width and squeezed the plot on
        # the right into a tall, skinny strip.
        lin_form = QGridLayout()
        for col, (label, widget) in enumerate((
                ("Start:", self.lin_start_spin), ("Stop:", self.lin_stop_spin),
                ("Steps:", self.lin_steps_spin))):
            widget.setMaximumWidth(SPIN_W)
            lin_form.addWidget(QLabel(label), 0, col * 2)
            lin_form.addWidget(widget, 0, col * 2 + 1)
        for col, (label, widget) in enumerate((
                ("Tol:", self.lin_tol_spin), ("Max fill:", self.lin_fill_spin))):
            widget.setMaximumWidth(SPIN_W)
            lin_form.addWidget(QLabel(label), 1, col * 2)
            lin_form.addWidget(widget, 1, col * 2 + 1)
        lin_form.setColumnStretch(6, 1)
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
        # Capped: a matplotlib canvas expands without limit, so on a large screen it
        # balloons and drags the whole tab with it. This cap also sets the height of
        # the row — the dark/reference plot opposite fills to match it.
        self.lin_canvas.setMinimumHeight(200)
        self.lin_canvas.setMaximumHeight(260)
        lin_col.addWidget(self.lin_canvas)

        # Top row: Spectrometer Connection | Potentiostat side by side (half width each).
        top_row = QHBoxLayout()
        top_row.addWidget(conn_group, stretch=1)
        top_row.addWidget(pstat_group, stretch=1)
        layout.addLayout(top_row)

        # --- Dark / Reference: TABBED, so only one plot is shown at a time. Stacking
        # them made this tab enormous; you inspect them one at a time anyway, and the
        # single plot balances the height of the (tall) Settings+Linearity column beside it.
        cal_tabs = QTabWidget()

        dark_box = QWidget()
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
        self.dark_canvas.setMinimumHeight(240)
        dark_col.addLayout(dark_btns)
        dark_col.addWidget(self.dark_status)
        # All spare height goes to the plot, not into gaps between the controls.
        dark_col.addWidget(self.dark_canvas, stretch=1)
        cal_tabs.addTab(dark_box, "Dark")

        ref_box = QWidget()
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
        self.ref_canvas.setMinimumHeight(240)
        ref_col.addLayout(ref_btns)
        ref_col.addWidget(self.ref_status)
        ref_col.addWidget(self.ref_canvas, stretch=1)
        cal_tabs.addTab(ref_box, "Reference (100%T)")

        # --- Test (sample): measure whatever is in the beam RIGHT NOW, non-destructively.
        # This never writes win.ref — which is the whole point. The reference is collected
        # with a blank FTO insert; then the blank comes out, the sample goes in, and the
        # electrodes get hooked up. You need to check the beam AFTER that swap, and
        # "just re-collect the reference" would record the SAMPLE as the reference.
        # One Measure takes one spectrum; the view toggle re-renders that same scan as raw
        # counts (saturation / lamp check, works with no dark/ref) or as absorbance.
        absorb_box = QWidget()
        absorb_col = QVBoxLayout(absorb_box)
        absorb_btns = QHBoxLayout()
        self.test_btn = QPushButton("Measure")
        self.test_btn.clicked.connect(self.on_measure_test)
        self.test_btn.setToolTip(
            "Measure what's in the beam now. Does NOT overwrite the dark or reference.")
        absorb_btns.addWidget(self.test_btn)
        absorb_btns.addSpacing(12)
        absorb_btns.addWidget(QLabel("View:"))
        self.view_counts_radio = QRadioButton("Counts")
        self.view_abs_radio = QRadioButton("Absorbance")
        self.view_counts_radio.setChecked(True)
        self.view_abs_radio.setToolTip("Needs a dark and a reference")
        self.view_counts_radio.toggled.connect(self._render_test_view)
        absorb_btns.addWidget(self.view_counts_radio)
        absorb_btns.addWidget(self.view_abs_radio)
        absorb_btns.addStretch()
        self.test_label = QLabel(
            "Measure the beam as it is now — does not overwrite the reference.")
        self.test_label.setStyleSheet("color: #888;")
        self.test_canvas = MplCanvas(ylabel="Intensity (counts)")
        self.test_canvas.setMinimumHeight(200)
        absorb_col.addLayout(absorb_btns)
        absorb_col.addWidget(self.test_label)
        absorb_col.addWidget(self.test_canvas, stretch=1)

        suggest_row = QHBoxLayout()
        suggest_row.addWidget(QLabel("Max noise:"))
        self.wl_maxnoise_spin = QDoubleSpinBox()
        self.wl_maxnoise_spin.setRange(0.001, 0.5)
        self.wl_maxnoise_spin.setDecimals(3)
        self.wl_maxnoise_spin.setSingleStep(0.005)
        self.wl_maxnoise_spin.setValue(0.010)
        self.wl_maxnoise_spin.setSuffix(" OD")
        self.wl_maxnoise_spin.setMaximumWidth(SPIN_W)
        self.wl_maxnoise_spin.setToolTip(
            "Trim where the test-abs noise exceeds this (set it small vs your OD signal)")
        self.wl_maxnoise_spin.valueChanged.connect(self._on_maxnoise_changed)
        suggest_row.addWidget(self.wl_maxnoise_spin)
        self.wl_suggest_btn = QPushButton("Suggest range from this")
        self.wl_suggest_btn.setToolTip(
            "Fill the Range boxes in Spectrometer Settings from this test-absorbance's "
            "noise — then click Apply range there")
        self.wl_suggest_btn.clicked.connect(self.on_wl_suggest)
        suggest_row.addWidget(self.wl_suggest_btn)
        suggest_row.addStretch()
        absorb_col.addLayout(suggest_row)
        self.wl_rationale = QLabel(
            "Optional: suggests a wavelength range from the noise in this spectrum.")
        self.wl_rationale.setStyleSheet("color: #888;")
        self.wl_rationale.setWordWrap(True)
        absorb_col.addWidget(self.wl_rationale)
        cal_tabs.addTab(absorb_box, "Test (sample)")

        cal_box = QGroupBox("Spectra")
        cal_box_col = QVBoxLayout(cal_box)
        cal_box_col.addWidget(cal_tabs)

        # Main row: [Spectrometer Settings over Linearity Check] | [tabbed spectra].
        # The left column is tall (it owns the linearity plot), so showing one spectrum
        # at a time on the right keeps the two columns the same height — no stretched-out
        # empty group boxes.
        main_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(settings_group)
        left_col.addWidget(lin_group)
        left_col.addStretch()
        main_row.addLayout(left_col, stretch=1)
        main_row.addWidget(cal_box, stretch=1)
        layout.addLayout(main_row)

        # --- Bench defaults: the settings that describe THIS RIG (lamp, ND filter, data
        # root, Gamry mode), not this experiment. Saved explicitly — never automatically,
        # or a one-off tweak for an odd sample would silently become the rig's default.
        bench_group = QGroupBox("Bench defaults (this rig — not the experiment)")
        bench_col = QVBoxLayout(bench_group)
        bench_btns = QHBoxLayout()
        self.bench_save_btn = QPushButton("Save as defaults")
        self.bench_save_btn.setToolTip(
            "Remember the current wavelength range, integration time, scan averages, "
            "linearity ramp, data root and Gamry mode as this machine's defaults. "
            "Sample/folder/CV parameters are NOT saved — those belong to an experiment.")
        self.bench_save_btn.clicked.connect(self.on_bench_save)
        self.bench_restore_btn = QPushButton("Restore factory defaults")
        self.bench_restore_btn.setToolTip(
            "Discard this machine's bench file and fall back to the lab defaults "
            "(config/defaults.ini). Does not touch experiment settings.")
        self.bench_restore_btn.clicked.connect(self.on_bench_restore)
        self.bench_open_btn = QPushButton("Open folder")
        self.bench_open_btn.setToolTip(
            "Open the config folder — bench.ini (this rig) sits beside defaults.ini "
            "(the lab-wide defaults). Both are plain text; edit with the app closed.")
        self.bench_open_btn.clicked.connect(self.on_bench_open)
        bench_btns.addWidget(self.bench_save_btn)
        bench_btns.addWidget(self.bench_restore_btn)
        bench_btns.addWidget(self.bench_open_btn)
        bench_btns.addStretch()
        bench_col.addLayout(bench_btns)
        self.bench_status = QLabel("")
        self.bench_status.setStyleSheet("color: #888;")
        self.bench_status.setWordWrap(True)
        bench_col.addWidget(self.bench_status)
        layout.addWidget(bench_group)
        self._report_bench_state()
        # Spare height goes here, not into the plots.
        layout.addStretch(1)

        self._set_actions_enabled(False)
        self._update_cal_plot()   # placeholders, not empty 0-1 axes

    # --- bench defaults ---

    def _report_bench_state(self):
        """Say what was loaded and from where. A config file nobody can find is just a
        registry with extra steps — and a hand-editable file WILL eventually contain a
        typo, so any warnings must be visible rather than swallowed."""
        path = user_bench_path()
        warnings = getattr(self.win, "bench_warnings", [])
        loaded = getattr(self.win, "bench_loaded", [])
        if warnings:
            self.bench_status.setText("⚠ " + "  ".join(warnings) + f"\nFile: {path}")
            self.bench_status.setStyleSheet("color: #b00;")
            return
        self.bench_status.setStyleSheet("color: #888;")
        if path.exists():
            self.bench_status.setText(f"Loaded {len(loaded)} settings for this rig.\nFile: {path}")
        else:
            self.bench_status.setText(
                f"Using the lab defaults (config/defaults.ini) — no bench file yet.\n"
                f"Save as defaults writes: {path}")

    def on_bench_open(self):
        """Open the config folder in the file manager — a hand-editable file you can't
        find is no better than a registry key."""
        folder = user_bench_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def on_bench_save(self):
        settings = self.win.collect_settings()   # every tab's widgets -> the settings dict
        try:
            path = save_bench_defaults(settings)
        except OSError as exc:
            self.bench_status.setText(f"Could not save bench defaults: {exc}")
            self.bench_status.setStyleSheet("color: #b00;")
            return
        self.bench_status.setStyleSheet("color: #080;")
        self.bench_status.setText(f"Saved as this rig's defaults.\nFile: {path}")

    def on_bench_restore(self):
        path = user_bench_path()
        if path.exists():
            reply = QMessageBox.question(
                self, "Restore factory defaults",
                f"Delete this machine's bench file and fall back to the lab defaults?\n\n"
                f"{path}\n\nExperiment settings are not affected.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            try:
                path.unlink()
            except OSError as exc:
                self.bench_status.setText(f"Could not remove the bench file: {exc}")
                self.bench_status.setStyleSheet("color: #b00;")
                return
        # Re-derive from code defaults + the repo lab defaults, and push into the widgets.
        settings = DEFAULT_SETTINGS.copy()
        values, warnings = load_bench_defaults()
        apply_bench_defaults(settings, values)
        self.win.bench_warnings = warnings
        self.win.bench_loaded = sorted(values)
        self.win.apply_settings(settings)
        self._report_bench_state()

    def showEvent(self, event):
        """Refresh the cadence note whenever this tab comes forward — the step
        length it compares against lives on the Parameters tab and can change
        while this tab is hidden."""
        super().showEvent(event)
        self._update_cadence_note()

    def _update_cadence_note(self):
        """What one spectrum costs, against the tightest step in this experiment.

        Advisory: it names the numbers and suggests a scan-averages count that
        would fit. It never changes a setting and never stops a run. The whole
        body is guarded — this label is a convenience, and a convenience that can
        raise would take the Instrument tab down with it.
        """
        try:
            integration = self.integration_spin.value()
            averages = self.averages_spin.value()
            cost = spectrum_cost_seconds(integration, averages)

            tightest = None
            try:
                segments = build_segments(self.win.collect_settings())
                if segments:
                    tightest = min(segments, key=lambda seg: seg.delta_time)
            except Exception:  # noqa: BLE001 — no experiment defined yet is normal
                tightest = None

            if tightest is None or tightest.delta_time <= 0:
                self.cadence_note.setText(f"~{cost * 1000:.0f} ms")
                self.cadence_note.setStyleSheet("color: #555;")
                return

            slot = tightest.delta_time
            fits = suggest_scan_averages(integration, slot)
            head = (f"~{cost * 1000:.0f} ms "
                    f"({integration:.4g} ms x {averages} + "
                    f"{SPECTRUM_OVERHEAD_S * 1000:.0f} ms overhead) vs a "
                    f"{slot * 1000:.0f} ms step ({tightest.label}).")

            if cost > slot:
                tail = (f" Does NOT fit: spectra will land ~{cost * 1000:.0f} ms apart"
                        f" and this step may outlive the electrochemistry.")
                tail += (f" About {fits} averages would fit." if fits >= 1 else
                         " Even 1 average does not fit — use a coarser CV step or a"
                         " longer delta time.")
                colour = "#b00020"
            elif cost > 0.8 * slot:
                tail = (f" Fits, but only {(slot - cost) * 1000:.0f} ms spare"
                        f" — {fits} averages is the ceiling here.")
                colour = "#a86400"
            else:
                tail = f" Fits (up to {fits} averages)."
                colour = "#555"

            self.cadence_note.setText(head + tail)
            self.cadence_note.setStyleSheet(f"color: {colour};")
        except Exception:  # noqa: BLE001 — advisory only; stay quiet and harmless
            try:
                self.cadence_note.setText("—")
                self.cadence_note.setStyleSheet("color: #555;")
            except Exception:  # noqa: BLE001
                pass

    def _wrap(self, inner_layout):
        box = QWidget()
        box.setLayout(inner_layout)
        return box

    # --- settings round-trip ---

    def populate_from(self, settings):
        self.integration_spin.setValue(settings["integration_time_ms"])
        self.averages_spin.setValue(settings["scan_averages"])
        # The linearity ramp is lamp-dependent, so it comes from the bench defaults.
        self.lin_start_spin.setValue(settings.get("lin_start_ms", settings["integration_time_ms"]))
        self.lin_stop_spin.setValue(settings.get("lin_stop_ms", 0.15))
        self.lin_steps_spin.setValue(int(settings.get("lin_steps", 20)))
        self.lin_tol_spin.setValue(settings.get("lin_tolerance_pct", 2.0))
        self.lin_fill_spin.setValue(settings.get("lin_max_fill_pct", 85.0))
        wl_min, wl_max = settings.get("wavelength_min"), settings.get("wavelength_max")
        if wl_min is not None:
            self.wl_min_spin.setValue(wl_min)
        if wl_max is not None:
            self.wl_max_spin.setValue(wl_max)
        # An unavailable mode falls back to External rather than selecting a radio the
        # machine cannot honour — a saved "autolab" on the Gamry rig must not disarm it.
        mode = settings.get("potentiostat_mode", "external")
        if mode == "python" and self.pstat_python_radio.isEnabled():
            self.pstat_python_radio.setChecked(True)
        elif mode == "autolab" and self.pstat_autolab_radio.isEnabled():
            self.pstat_autolab_radio.setChecked(True)
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
        if self.pstat_python_radio.isChecked():
            settings["potentiostat_mode"] = "python"
        elif self.pstat_autolab_radio.isChecked():
            settings["potentiostat_mode"] = "autolab"
        else:
            settings["potentiostat_mode"] = "external"
        settings["save_dta"] = self.save_dta_check.isChecked()
        settings["lin_start_ms"] = self.lin_start_spin.value()
        settings["lin_stop_ms"] = self.lin_stop_spin.value()
        settings["lin_steps"] = self.lin_steps_spin.value()
        settings["lin_tolerance_pct"] = self.lin_tol_spin.value()
        settings["lin_max_fill_pct"] = self.lin_fill_spin.value()

    # --- actions ---

    def _set_actions_enabled(self, enabled):
        self._actions_enabled = enabled
        # Load belongs here too: a dark/ref is only meaningful against the connected
        # spectrometer's wavelength axis. Left ungated, Load "succeeded" with no axis
        # and the plot then reported a nonsense "0 px window".
        for w in (self.apply_btn, self.collect_dark_btn, self.collect_ref_btn,
                  self.load_dark_btn, self.load_ref_btn,
                  self.timing_btn, self.wl_apply_btn, self.wl_reset_btn,
                  self.test_btn,
                  self.lin_run_btn, self.lin_find_sat_btn):
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
        # Measure itself only needs a spectrometer — raw counts are exactly what you want
        # BEFORE a dark/ref exist (optics alignment) and AFTER the sample swap. It's the
        # absorbance VIEW that needs a dark and a reference.
        ready = self.win.spec is not None and self.win.dark is not None and self.win.ref is not None
        self.view_abs_radio.setEnabled(ready)
        if not ready and self.view_abs_radio.isChecked():
            self.view_counts_radio.setChecked(True)   # fall back rather than show nothing
        # Can only save a dark/ref once one has been collected or loaded.
        self.save_dark_btn.setEnabled(self.win.dark is not None)
        self.save_ref_btn.setEnabled(self.win.ref is not None)
        self._update_suggest_enabled()

    def _update_suggest_enabled(self):
        """Suggest needs a test-absorbance and the tab's actions live (connected,
        not mid-run)."""
        self.wl_suggest_btn.setEnabled(
            getattr(self, "_actions_enabled", False) and self._last_test_abs is not None)

    def _set_pstat_status(self, text, color, detail=""):
        """`text` goes inline and must stay short; `detail` is for messages whose
        length we don't control (toolkitpy errors) and gets the wrapping label."""
        self.pstat_status.setText(text)
        self.pstat_status.setStyleSheet(f"color: {color};")
        self.pstat_detail.setText(detail)

    def _update_pstat_controls(self):
        python = self.pstat_python_radio.isChecked()
        autolab = self.pstat_autolab_radio.isChecked()
        driven = python or autolab          # Python is holding the instrument
        available = TOOLKITPY_AVAILABLE if python else AUTOLAB_AVAILABLE
        # Respect the run lock: toggling the mode radios during a run must NOT
        # re-enable Connect, which would re-init the vendor stack and collide with the
        # thread driving the live run. _actions_enabled is False during a run.
        self.pstat_connect_btn.setEnabled(
            driven and available and getattr(self, "_actions_enabled", True))
        # .DTA is a Gamry format written by toolkitpy — meaningless in the other two
        # modes (External writes its own via Framework; the Autolab has no .DTA).
        self.save_dta_check.setEnabled(python)
        if not driven:
            self._set_pstat_status("● Runs from Gamry Framework", "#555")
        elif not self._pstat_connected:
            self._set_pstat_status("● Not connected", "#b00")
        # else: keep the green "● Connected — …" so it survives run-end / re-toggle

    def on_connect_pstat(self):
        """Verify the selected potentiostat is reachable and report WHICH unit it is.

        Both probes are read-only: the Gamry one opens and reads its label/serial, the
        Autolab one connects and disconnects without touching the cell.
        """
        self._set_pstat_status("● Connecting…", "#555")
        autolab = self.pstat_autolab_radio.isChecked()
        try:
            if autolab:
                who = autolab_identity(self.win.settings)
            else:
                label, serial = probe_identity()
                label = (label or "").strip()
                who = f"{label} (serial {serial})" if label else f"Gamry serial {serial}"
        except Exception as exc:  # noqa: BLE001 — surface any vendor/hardware failure
            self._pstat_connected = False
            logger.warning("Potentiostat connect failed: %s", exc)
            self._set_pstat_status("● Connect failed", "#b00", detail=str(exc))
            return
        self._pstat_connected = True
        self.win.pstat_identity = who
        logger.info("Potentiostat connected: %s", who)
        self._set_pstat_status(f"● Connected — {who}", "#080")

    def _update_cal_plot(self):
        # Dark is unannotated on purpose: it is detector noise / stray light, so its
        # "peak" means nothing. The reference peak IS meaningful (it's the counts test),
        # so it gets the max-counts annotation that Test (counts) used to provide.
        self._plot_if_matched(self.dark_canvas, self.win.dark,
                              "Dark", "Intensity (counts)")
        self._plot_if_matched(self.ref_canvas, self.win.ref,
                              "Reference (100%T)", "Intensity (counts)", mark_max=True)
        # The Test scan lives on the same wavelength axis, so it must be redrawn with the
        # others — applying a window used to leave it stale at the old width, which is
        # exactly the plot you're looking at when you click Apply after Suggest.
        self._render_test_view()

    def _plot_if_matched(self, canvas, data, title, ylabel, empty_msg=None, **kw):
        """Plot data vs the current wavelength axis only if their lengths match;
        otherwise show a note. Prevents length-mismatch crashes when a window has
        been applied while dark/ref are from a different range."""
        wl = self.win.wavelengths
        if data is None:
            canvas.show_message(empty_msg or f"{title}: none yet — Collect New or Load.")
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
        if wl is None:
            return None          # no axis yet — nothing to reconcile against
        if len(arr) == len(wl):
            return arr
        full = getattr(self, "_full_wl", None)
        if full is not None and len(arr) == len(full):
            i0 = int(np.argmin(np.abs(np.asarray(full) - wl[0])))
            return np.asarray(arr)[i0:i0 + len(wl)]
        return None

    def _spectrometer_detail(self, spec, serial):
        """One line naming the connected detector: pixels and full calibrated span."""
        kind = "Simulated spectrometer" if isinstance(spec, FakeSpectrometer) else "Avantes"
        bits = [f"{kind} · serial {serial}"]
        full = getattr(self, "_full_wl", None)
        if full is not None and len(full):
            bits.append(f"{len(full)} px · {float(full[0]):.1f}–{float(full[-1]):.1f} nm")
        return "   ".join(bits)

    def on_connect(self):
        if self.simulated_check.isChecked() or AvantesSpectrometer is None:
            spec = FakeSpectrometer()
        else:
            spec = AvantesSpectrometer()
        try:
            _, serial = spec.init()
        except Exception as exc:  # noqa: BLE001 — surface any hardware init failure to the user
            logger.warning("Spectrometer connect failed: %s", exc)
            # Short text inline, the variable-length message in the wrapping label —
            # see _detail_label(): an unwrapped inline message widens the window.
            self.spec_status.setText("● Connect failed")
            self.spec_status.setStyleSheet("color: #b00;")
            self.spec_detail.setText(str(exc))
            return
        self.win.spec = spec
        _, self.win.wavelengths = spec.wavelengths()
        # A fresh connection is at the full window; remember it so loaded (full-range)
        # dark/ref files can be sliced to a narrower window later.
        self._full_wl = np.asarray(self.win.wavelengths)
        self.win.spec_identity = (f"simulated ({serial})"
                                  if isinstance(spec, FakeSpectrometer)
                                  else f"Avantes serial {serial}")
        logger.info("Spectrometer connected: %s", self.win.spec_identity)
        self.spec_status.setText(f"● Connected ({serial})")
        self.spec_status.setStyleSheet("color: #080;")
        # Which detector is this, in terms you can check against the instrument on the
        # bench? A serial alone doesn't distinguish a ULS2048L from a VRS2048CL-EVO;
        # the pixel count and reported span do. Also replaces any previous failure text.
        self.spec_detail.setText(self._spectrometer_detail(spec, serial))
        self._set_actions_enabled(True)
        self.on_apply()

        # Clamp the wavelength spin boxes to what THIS spectrometer actually reports
        # (its calibrated span) — they otherwise accept 0–5000 nm regardless of the
        # hardware, so a crop tuned for one detector silently misapplies to another.
        full_lo = float(self._full_wl[0]) if len(self._full_wl) else None
        full_hi = float(self._full_wl[-1]) if len(self._full_wl) else None
        if full_lo is not None:
            for spin in (self.wl_min_spin, self.wl_max_spin):
                spin.setRange(full_lo, full_hi)

        # Apply this rig's saved wavelength crop automatically — "it comes up the way I
        # left it" — UNLESS it clearly doesn't belong to the spectrometer now connected
        # (a different unit, or a stale settings JSON): then come up at the full range
        # with the saved values parked in the boxes for an explicit Apply, rather than
        # silently clamping a window drawn for another detector.
        wl_min = self.win.settings.get("wavelength_min")
        wl_max = self.win.settings.get("wavelength_max")
        have_saved = wl_min is not None and wl_max is not None
        fits = (have_saved and full_lo is not None
                and self._window_fits(float(wl_min), float(wl_max), full_lo, full_hi))
        if have_saved and fits:
            self.wl_min_spin.setValue(float(wl_min))
            self.wl_max_spin.setValue(float(wl_max))
            self._apply_window(self.wl_min_spin.value(), self.wl_max_spin.value())
            self.wl_status.setText(
                f"Spectrometer {full_lo:.0f}–{full_hi:.0f} nm · " + self.wl_status.text())
        elif have_saved and full_lo is not None:   # saved crop doesn't fit this unit
            self.wl_min_spin.setValue(float(wl_min))
            self.wl_max_spin.setValue(float(wl_max))
            self.wl_status.setText(
                f"Saved crop {float(wl_min):.0f}–{float(wl_max):.0f} nm doesn't fit this "
                f"spectrometer's {full_lo:.0f}–{full_hi:.0f} nm range — showing full range. "
                "Adjust and Apply.")
        elif len(self.win.wavelengths):
            self.wl_min_spin.setValue(float(self.win.wavelengths[0]))
            self.wl_max_spin.setValue(float(self.win.wavelengths[-1]))
            self.wl_status.setText(
                f"Full range: {self._full_wl[0]:.0f}–{self._full_wl[-1]:.0f} nm "
                f"({len(self._full_wl)} px). Set your lamp's usable range, then Apply.")

    @staticmethod
    def _window_fits(wl_min, wl_max, full_lo, full_hi):
        """False only when the saved crop was clearly drawn for a different detector —
        i.e. it overlaps this spectrometer's calibrated span by less than half its own
        width. A small edge mismatch (e.g. a 400 nm setting vs a 410 nm floor) still fits."""
        overlap = max(0.0, min(wl_max, full_hi) - max(wl_min, full_lo))
        want = max(1e-9, wl_max - wl_min)
        return overlap >= 0.5 * want

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
        logger.info("Dark collected (%d px, max %.0f counts)",
                    len(spectrum), np.max(spectrum))
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
        if self.win.spec is None:
            self.dark_status.setText("Dark: connect the spectrometer first.")
            return
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
        logger.info("Reference collected (%d px, max %.0f counts)",
                    len(spectrum), np.max(spectrum))
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
        if self.win.spec is None:
            self.ref_status.setText("Reference: connect the spectrometer first.")
            return
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

    def on_measure_test(self):
        """One spectrum of whatever is in the beam now. Stored transiently — never
        written to win.dark / win.ref, so it is safe to run after the blank FTO has been
        swapped for the sample."""
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self._last_test_spectrum = np.asarray(spectrum)
        self._last_test_abs = self._absorbance_of(self._last_test_spectrum)
        self._update_absorbance_enabled()
        self._render_test_view()

    def _absorbance_of(self, spectrum):
        """A = -log10((sample - dark) / (ref - dark)), or None if dark/ref aren't usable."""
        dark, ref = self.win.dark, self.win.ref
        if dark is None or ref is None:
            return None
        if not (len(spectrum) == len(dark) == len(ref)):
            return None
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.asarray(-np.log10((spectrum - dark) / (ref - dark)))

    def _render_test_view(self, *_):
        """Re-render the LAST scan in the selected view — no re-measurement, so counts and
        absorbance always describe the same spectrum."""
        if self.view_abs_radio.isChecked():
            self._plot_if_matched(
                self.test_canvas, self._last_test_abs, "Test (absorbance)", "Absorbance",
                empty_msg="Absorbance needs a dark and a reference — collect them, then Measure.")
            self.test_label.setText("A = −log₁₀((sample − dark) / (ref − dark))")
        else:
            spectrum = self._last_test_spectrum
            self._plot_if_matched(
                self.test_canvas, spectrum, "Test (counts)", "Intensity (counts)",
                mark_max=True, empty_msg="Test: none yet — press Measure.")
            if spectrum is None:
                self.test_label.setText(
                    "Measure the beam as it is now — does not overwrite the reference.")
            else:
                self.test_label.setText(
                    f"Counts: {len(spectrum)} px, min={spectrum.min():.0f}  "
                    f"max={spectrum.max():.0f}")
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
        # A degenerate absorbance (e.g. a dark that isn't really dark, so ref-dark ~ 0)
        # yields NaN noise and a zero-width band. Don't push that into the Range boxes.
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            self.wl_rationale.setText(
                "Couldn't read a usable noise floor from this test-absorbance — check the "
                "dark (lamp blocked) and reference (blank, lamp on), or set the range by eye.")
            return
        self.wl_min_spin.setValue(lo)
        self.wl_max_spin.setValue(hi)
        self.wl_rationale.setText(rationale["summary"])

    def on_wl_apply(self):
        if self.win.spec is None:
            return
        wl_min, wl_max = self.wl_min_spin.value(), self.wl_max_spin.value()
        if wl_max <= wl_min:
            self.wl_status.setText("Range invalid: max must be greater than min.")
            return
        self._apply_window(wl_min, wl_max)

    def on_wl_reset(self):
        """Back to the spectrometer's full calibrated range (undo the crop)."""
        if self.win.spec is None:
            return
        full = getattr(self, "_full_wl", None)
        self._apply_window(None, None)
        if full is not None and len(full):
            self.wl_min_spin.setValue(float(full[0]))
            self.wl_max_spin.setValue(float(full[-1]))

    def _apply_window(self, wl_min, wl_max):
        """Set the spectrometer's window (None,None = full) and keep dark/ref aligned."""
        old_wl = np.asarray(self.win.wavelengths) if self.win.wavelengths is not None else None
        try:
            self.win.spec.set_wavelength_window(wl_min, wl_max)
        except Exception as exc:  # noqa: BLE001
            self.wl_status.setText(f"Apply failed: {exc}")
            return
        _, self.win.wavelengths = self.win.spec.wavelengths()
        new_wl = np.asarray(self.win.wavelengths)
        had_cal = self.win.dark is not None or self.win.ref is not None
        self._reslice_cal(old_wl, new_wl)   # keep dark/ref aligned; clear on a widen
        now_cal = self.win.dark is not None
        self._update_cal_plot()
        self._refresh_cal_status()          # the labels must not out-live the data
        self._update_absorbance_enabled()
        msg = f"{new_wl[0]:.0f}–{new_wl[-1]:.0f} nm ({len(new_wl)} px)."
        if had_cal and now_cal:
            msg += " Dark/reference re-sliced to match."
        elif had_cal and not now_cal:
            msg += " Dark/reference cleared — re-collect at this range."
        self.wl_status.setText(msg)

    def _refresh_cal_status(self):
        """Rewrite the dark/ref labels from the ACTUAL stored data after a window change.
        Otherwise they keep claiming 'collected (702 px)' after a widen cleared them, or
        keep the pre-slice pixel count after a narrow — a label that outlives its data."""
        for arr, label, name in ((self.win.dark, self.dark_status, "Dark"),
                                 (self.win.ref, self.ref_status, "Reference")):
            if arr is None:
                label.setText(f"{name}: cleared — re-collect at this range.")
            else:
                label.setText(f"{name}: ready ({len(arr)} px)")

    def _reslice_cal(self, old_wl, new_wl):
        """After the window narrows, slice dark/ref/test-abs (aligned with old_wl) down
        to new_wl so they stay matched to the run data — no re-collect. If new_wl isn't
        a sub-range of old_wl (a widen beyond what was collected), clear them.

        The test-absorbance belongs here too: A(lambda) doesn't change when you crop, so
        slicing it is exact, and leaving it out stranded it at the old width."""
        if old_wl is None:
            return
        contained = (len(new_wl) <= len(old_wl)
                     and new_wl[0] >= old_wl[0] - 1e-6
                     and new_wl[-1] <= old_wl[-1] + 1e-6)
        i0 = int(np.argmin(np.abs(old_wl - new_wl[0]))) if contained else 0
        sl = slice(i0, i0 + len(new_wl))

        def fit(arr):
            """Put arr on the new axis, or drop it. Never leave it stale: a stored array
            whose length no longer matches the axis can't be plotted or used, and silently
            keeping it is what made Apply look like it did nothing."""
            if arr is None:
                return None
            if len(arr) == len(new_wl):
                return arr                                  # already on the new axis
            if contained and len(arr) == len(old_wl):
                return np.asarray(arr)[sl]                  # narrowed: slice it down
            return None                                     # widened / unrelated: drop it

        self.win.dark = fit(self.win.dark)
        self.win.ref = fit(self.win.ref)
        self._last_test_spectrum = fit(self._last_test_spectrum)
        self._last_test_abs = fit(self._last_test_abs)
