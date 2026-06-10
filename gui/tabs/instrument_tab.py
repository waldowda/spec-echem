"""
Tab 1 — Instrument Setup.

Connect the spectrometer (real or simulated), tune integration time / scan
averages, show a phase-aware potentiostat status, and collect dark / reference
spectra. Plot previews are placeholders until the matplotlib canvas is wired.
"""
import numpy as np
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QPushButton, QLabel, QCheckBox, QDoubleSpinBox, QSpinBox, QFileDialog,
)

from spec_echem.fakes import FakeSpectrometer
from gui.widgets.plot_canvas import MplCanvas

try:
    from spec_echem import AvantesSpectrometer
except ImportError:
    AvantesSpectrometer = None


class InstrumentTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

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
        layout.addWidget(conn_group)

        # --- Spectrometer settings ---
        settings_group = QGroupBox("Spectrometer Settings")
        form = QFormLayout(settings_group)
        self.integration_spin = QDoubleSpinBox()
        self.integration_spin.setRange(0.001, 10000.0)
        self.integration_spin.setDecimals(3)
        self.integration_spin.setSuffix(" ms")
        self.averages_spin = QSpinBox()
        self.averages_spin.setRange(1, 100000)
        self.apply_btn = QPushButton("Apply to Spectrometer")
        self.apply_btn.clicked.connect(self.on_apply)
        form.addRow("Integration time:", self.integration_spin)
        form.addRow("Scan averages:", self.averages_spin)
        form.addRow(self.apply_btn)
        layout.addWidget(settings_group)

        # --- Potentiostat status (phase-aware) ---
        pstat_group = QGroupBox("Potentiostat")
        pstat_layout = QVBoxLayout(pstat_group)
        self.pstat_status = QLabel("Gamry: standalone (runs from sequence file)")
        self.pstat_status.setStyleSheet("color: #888;")
        pstat_layout.addWidget(self.pstat_status)
        layout.addWidget(pstat_group)

        # --- Dark / Reference ---
        cal_group = QGroupBox("Dark / Reference (100%T)")
        cal_layout = QVBoxLayout(cal_group)
        dark_row = QHBoxLayout()
        self.collect_dark_btn = QPushButton("Collect New Dark")
        self.collect_dark_btn.clicked.connect(self.on_collect_dark)
        self.load_dark_btn = QPushButton("Load Dark from File")
        self.load_dark_btn.clicked.connect(self.on_load_dark)
        self.dark_status = QLabel("Dark: none")
        dark_row.addWidget(self.collect_dark_btn)
        dark_row.addWidget(self.load_dark_btn)
        dark_row.addWidget(self.dark_status)
        dark_row.addStretch()
        ref_row = QHBoxLayout()
        self.collect_ref_btn = QPushButton("Collect Reference")
        self.collect_ref_btn.clicked.connect(self.on_collect_ref)
        self.ref_status = QLabel("Reference: none")
        ref_row.addWidget(self.collect_ref_btn)
        ref_row.addWidget(self.ref_status)
        ref_row.addStretch()
        cal_layout.addLayout(dark_row)
        cal_layout.addLayout(ref_row)
        layout.addWidget(cal_group)

        # --- Preview / actions (plots deferred) ---
        action_group = QGroupBox("Preview")
        action_layout = QVBoxLayout(action_group)
        btn_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Measurement")
        self.test_btn.clicked.connect(self.on_test_measure)
        self.timing_btn = QPushButton("Run Timing Test")
        self.timing_btn.clicked.connect(self.on_timing_test)
        self.view_cal_btn = QPushButton("View Dark && 100%T")
        self.view_cal_btn.clicked.connect(self.on_view_cal)
        btn_row.addWidget(self.test_btn)
        btn_row.addWidget(self.timing_btn)
        btn_row.addWidget(self.view_cal_btn)
        btn_row.addStretch()
        self.canvas = MplCanvas(ylabel="Intensity (counts)")
        self.canvas.setMinimumHeight(220)
        self.preview_label = QLabel("Connect, then Test Measurement to preview a spectrum.")
        self.preview_label.setStyleSheet("color: #888;")
        action_layout.addLayout(btn_row)
        action_layout.addWidget(self.preview_label)
        action_layout.addWidget(self.canvas)
        layout.addWidget(action_group)

        self._set_actions_enabled(False)

    # --- settings round-trip ---

    def populate_from(self, settings):
        self.integration_spin.setValue(settings["integration_time_ms"])
        self.averages_spin.setValue(settings["scan_averages"])

    def collect_into(self, settings):
        settings["integration_time_ms"] = self.integration_spin.value()
        settings["scan_averages"] = self.averages_spin.value()

    # --- actions ---

    def _set_actions_enabled(self, enabled):
        for w in (self.apply_btn, self.collect_dark_btn, self.collect_ref_btn,
                  self.test_btn, self.timing_btn):
            w.setEnabled(enabled)

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

    def on_load_dark(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Dark Spectrum", "", "Text files (*.txt *.csv)")
        if not path:
            return
        try:
            data = np.loadtxt(path)
            self.win.dark = data if data.ndim == 1 else data[:, -1]
            self.dark_status.setText(f"Dark: loaded ({len(self.win.dark)} px)")
        except Exception as exc:  # noqa: BLE001
            self.dark_status.setText(f"Dark: load failed ({exc})")

    def on_collect_ref(self):
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self.win.ref = spectrum
        self.ref_status.setText(f"Reference: collected ({len(spectrum)} px)")

    def on_test_measure(self):
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self.preview_label.setText(
            f"Test measurement: {len(spectrum)} px, "
            f"min={spectrum.min():.0f}  max={spectrum.max():.0f} counts"
        )
        self.canvas.show_spectrum(self.win.wavelengths, spectrum,
                                  title="Test Measurement", ylabel="Intensity (counts)")

    def on_view_cal(self):
        if self.win.dark is None and self.win.ref is None:
            self.preview_label.setText("Collect dark and/or reference first.")
            return
        self.canvas.show_dark_ref(self.win.wavelengths, self.win.dark, self.win.ref)
        self.preview_label.setText("Dark & 100%T — check the reference stays in the linear range.")

    def on_timing_test(self):
        if self.win.spec is None:
            return
        _, _, net_dif, t_dif = self.win.spec.measure_timing()
        self.preview_label.setText(
            f"Timing: total {t_dif * 1000:.1f} ms  (overhead {net_dif:.1f} ms)"
        )
