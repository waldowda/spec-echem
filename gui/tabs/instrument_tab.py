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
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QScrollArea,
    QPushButton, QLabel, QCheckBox, QRadioButton, QDoubleSpinBox, QSpinBox, QFileDialog,
)

from spec_echem.fakes import FakeSpectrometer
from spec_echem.potentiostat import TOOLKITPY_AVAILABLE, probe_identity
from spec_echem.settings import DEFAULT_SETTINGS
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

        # Top row: Spectrometer Connection | Potentiostat side by side (half width
        # each); Spectrometer Settings full width beneath.
        top_row = QHBoxLayout()
        top_row.addWidget(conn_group, stretch=1)
        top_row.addWidget(pstat_group, stretch=1)
        layout.addLayout(top_row)
        layout.addWidget(settings_group)

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
        self.save_dta_check.setChecked(settings.get("save_dta", True))

    def collect_into(self, settings):
        settings["integration_time_ms"] = self.integration_spin.value()
        settings["scan_averages"] = self.averages_spin.value()
        settings["potentiostat_mode"] = (
            "python" if self.pstat_python_radio.isChecked() else "external")
        settings["save_dta"] = self.save_dta_check.isChecked()

    # --- actions ---

    def _set_actions_enabled(self, enabled):
        for w in (self.apply_btn, self.collect_dark_btn, self.collect_ref_btn,
                  self.test_counts_btn, self.timing_btn):
            w.setEnabled(enabled)
        # Connect re-inits toolkitpy; forbid it during a run so it can't collide
        # with a Python-mode run driving the Gamry. Restore its normal (python +
        # toolkitpy) state when the run ends.
        if enabled:
            self._update_pstat_controls()
        else:
            self.pstat_connect_btn.setEnabled(False)
        self._update_absorbance_enabled()

    def _update_absorbance_enabled(self):
        ready = self.win.spec is not None and self.win.dark is not None and self.win.ref is not None
        self.test_absorb_btn.setEnabled(ready)
        # Can only save a dark/ref once one has been collected or loaded.
        self.save_dark_btn.setEnabled(self.win.dark is not None)
        self.save_ref_btn.setEnabled(self.win.ref is not None)

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
        if self.win.dark is not None:
            self.dark_canvas.show_spectrum(self.win.wavelengths, self.win.dark,
                                           title="Dark", ylabel="Intensity (counts)")
        if self.win.ref is not None:
            self.ref_canvas.show_spectrum(self.win.wavelengths, self.win.ref,
                                          title="Reference (100%T)", ylabel="Intensity (counts)")

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
            self.win.ref = data if data.ndim == 1 else data[:, -1]
            self.ref_status.setText(f"Reference: loaded ({len(self.win.ref)} px)")
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
        self.counts_canvas.show_spectrum(self.win.wavelengths, spectrum,
                                         title="Test (counts)", ylabel="Intensity (counts)",
                                         mark_max=True)

    def on_test_absorbance(self):
        if self.win.spec is None or self.win.dark is None or self.win.ref is None:
            return
        _, spectrum = self.win.spec.measure()
        with np.errstate(divide="ignore", invalid="ignore"):
            transmittance = (spectrum - self.win.dark) / (self.win.ref - self.win.dark)
            absorbance = -np.log10(transmittance)
        self.absorb_label.setText("A = −log₁₀((sample − dark) / (ref − dark))")
        self.absorb_canvas.show_spectrum(self.win.wavelengths, absorbance,
                                         title="Test (absorbance)", ylabel="Absorbance")

    def on_timing_test(self):
        if self.win.spec is None:
            return
        _, _, net_dif, t_dif = self.win.spec.measure_timing()
        self.timing_result.setText(
            f"total {t_dif * 1000:.1f} ms  (overhead {net_dif:.1f} ms)")
