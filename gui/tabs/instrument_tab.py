"""
Tab 1 — Instrument Setup.

Connect the spectrometer (real or simulated), tune integration time / scan
averages (with an inline timing test), show a phase-aware potentiostat status,
collect dark / reference spectra with a live preview, and test-measure in raw
counts or absorbance.
"""
import numpy as np
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QPushButton, QLabel, QCheckBox, QRadioButton, QDoubleSpinBox, QSpinBox, QFileDialog,
)

from spec_echem.fakes import FakeSpectrometer
from spec_echem.potentiostat import TOOLKITPY_AVAILABLE, probe_identity
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

        # --- Spectrometer settings (incl. timing test, which depends on these) ---
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
        timing_row = QHBoxLayout()
        self.timing_btn = QPushButton("Run Timing Test")
        self.timing_btn.clicked.connect(self.on_timing_test)
        self.timing_result = QLabel("—")
        self.timing_result.setStyleSheet("color: #555;")
        timing_row.addWidget(self.timing_btn)
        timing_row.addWidget(self.timing_result)
        timing_row.addStretch()
        form.addRow("Timing:", self._wrap(timing_row))
        layout.addWidget(settings_group)

        # --- Potentiostat control mode ---
        pstat_group = QGroupBox("Potentiostat Control")
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

        # Identify (Python mode): confirm the Gamry is reachable + show its serial
        id_row = QHBoxLayout()
        self.pstat_identify_btn = QPushButton("Identify Potentiostat")
        self.pstat_identify_btn.clicked.connect(self.on_identify_pstat)
        self.pstat_status = QLabel("—")
        self.pstat_status.setStyleSheet("color: #555;")
        id_row.addWidget(self.pstat_identify_btn)
        id_row.addWidget(self.pstat_status)
        id_row.addStretch()
        pstat_layout.addLayout(id_row)
        self.pstat_external_radio.toggled.connect(self._update_pstat_controls)
        self._update_pstat_controls()
        layout.addWidget(pstat_group)

        # --- Dark / Reference: buttons on the left, live plot on the right ---
        cal_group = QGroupBox("Dark / Reference (100%T)")
        cal_outer = QHBoxLayout(cal_group)
        cal_left = QVBoxLayout()
        dark_row = QHBoxLayout()
        self.collect_dark_btn = QPushButton("Collect New Dark")
        self.collect_dark_btn.clicked.connect(self.on_collect_dark)
        self.load_dark_btn = QPushButton("Load Dark from File")
        self.load_dark_btn.clicked.connect(self.on_load_dark)
        dark_row.addWidget(self.collect_dark_btn)
        dark_row.addWidget(self.load_dark_btn)
        dark_row.addStretch()
        self.dark_status = QLabel("Dark: none")
        self.collect_ref_btn = QPushButton("Collect Reference")
        self.collect_ref_btn.clicked.connect(self.on_collect_ref)
        self.ref_status = QLabel("Reference: none")
        cal_left.addLayout(dark_row)
        cal_left.addWidget(self.dark_status)
        cal_left.addWidget(self.collect_ref_btn)
        cal_left.addWidget(self.ref_status)
        cal_left.addStretch()
        cal_outer.addLayout(cal_left, stretch=1)
        self.cal_canvas = MplCanvas(ylabel="Intensity (counts)")
        self.cal_canvas.setMinimumHeight(180)
        cal_outer.addWidget(self.cal_canvas, stretch=2)
        layout.addWidget(cal_group)

        # --- Test measurement: counts or absorbance ---
        test_group = QGroupBox("Test Measurement")
        test_layout = QVBoxLayout(test_group)
        btn_row = QHBoxLayout()
        self.test_counts_btn = QPushButton("Test (counts)")
        self.test_counts_btn.clicked.connect(self.on_test_counts)
        self.test_absorb_btn = QPushButton("Test (absorbance)")
        self.test_absorb_btn.clicked.connect(self.on_test_absorbance)
        self.test_absorb_btn.setToolTip("Needs a dark and a reference first")
        btn_row.addWidget(self.test_counts_btn)
        btn_row.addWidget(self.test_absorb_btn)
        btn_row.addStretch()
        self.preview_label = QLabel("Connect, then Test to preview a spectrum.")
        self.preview_label.setStyleSheet("color: #888;")
        self.test_canvas = MplCanvas(ylabel="Intensity (counts)")
        self.test_canvas.setMinimumHeight(200)
        test_layout.addLayout(btn_row)
        test_layout.addWidget(self.preview_label)
        test_layout.addWidget(self.test_canvas)
        layout.addWidget(test_group)

        self._set_actions_enabled(False)

    def _wrap(self, inner_layout):
        box = QWidget()
        box.setLayout(inner_layout)
        return box

    # --- settings round-trip ---

    def populate_from(self, settings):
        self.integration_spin.setValue(settings["integration_time_ms"])
        self.averages_spin.setValue(settings["scan_averages"])
        mode = settings.get("potentiostat_mode", "external")
        if mode == "python" and self.pstat_python_radio.isEnabled():
            self.pstat_python_radio.setChecked(True)
        else:
            self.pstat_external_radio.setChecked(True)

    def collect_into(self, settings):
        settings["integration_time_ms"] = self.integration_spin.value()
        settings["scan_averages"] = self.averages_spin.value()
        settings["potentiostat_mode"] = (
            "python" if self.pstat_python_radio.isChecked() else "external")

    # --- actions ---

    def _set_actions_enabled(self, enabled):
        for w in (self.apply_btn, self.collect_dark_btn, self.collect_ref_btn,
                  self.test_counts_btn, self.timing_btn):
            w.setEnabled(enabled)
        self._update_absorbance_enabled()

    def _update_absorbance_enabled(self):
        ready = self.win.spec is not None and self.win.dark is not None and self.win.ref is not None
        self.test_absorb_btn.setEnabled(ready)

    def _update_pstat_controls(self):
        python = self.pstat_python_radio.isChecked()
        self.pstat_identify_btn.setEnabled(python and TOOLKITPY_AVAILABLE)
        self.pstat_status.setText("—" if python else "Gamry runs from Gamry Framework")
        self.pstat_status.setStyleSheet("color: #555;")

    def on_identify_pstat(self):
        self.pstat_status.setText("Identifying…")
        self.pstat_status.setStyleSheet("color: #555;")
        try:
            label, serial = probe_identity()
        except Exception as exc:  # noqa: BLE001 — surface any toolkitpy/hardware failure
            self.pstat_status.setText(f"Identify failed: {exc}")
            self.pstat_status.setStyleSheet("color: #b00;")
            return
        label = (label or "").strip()
        who = f"{label} (serial {serial})" if label else f"serial {serial}"
        self.pstat_status.setText(f"Gamry connected — {who}")
        self.pstat_status.setStyleSheet("color: #080;")

    def _update_cal_plot(self):
        self.cal_canvas.show_dark_ref(self.win.wavelengths, self.win.dark, self.win.ref)

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
        self._update_cal_plot()
        self._update_absorbance_enabled()

    def on_load_dark(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Dark Spectrum", "", "Text files (*.txt *.csv)")
        if not path:
            return
        try:
            data = np.loadtxt(path)
            self.win.dark = data if data.ndim == 1 else data[:, -1]
            self.dark_status.setText(f"Dark: loaded ({len(self.win.dark)} px)")
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

    def on_test_counts(self):
        if self.win.spec is None:
            return
        _, spectrum = self.win.spec.measure()
        self.preview_label.setText(
            f"Counts: {len(spectrum)} px, min={spectrum.min():.0f}  max={spectrum.max():.0f}")
        self.test_canvas.show_spectrum(self.win.wavelengths, spectrum,
                                       title="Test (counts)", ylabel="Intensity (counts)")

    def on_test_absorbance(self):
        if self.win.spec is None or self.win.dark is None or self.win.ref is None:
            return
        _, spectrum = self.win.spec.measure()
        with np.errstate(divide="ignore", invalid="ignore"):
            transmittance = (spectrum - self.win.dark) / (self.win.ref - self.win.dark)
            absorbance = -np.log10(transmittance)
        self.preview_label.setText("Absorbance = −log₁₀((sample − dark) / (ref − dark))")
        self.test_canvas.show_spectrum(self.win.wavelengths, absorbance,
                                       title="Test (absorbance)", ylabel="Absorbance")

    def on_timing_test(self):
        if self.win.spec is None:
            return
        _, _, net_dif, t_dif = self.win.spec.measure_timing()
        self.timing_result.setText(
            f"total {t_dif * 1000:.1f} ms  (overhead {net_dif:.1f} ms)")
