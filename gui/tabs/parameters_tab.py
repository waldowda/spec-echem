"""
Tab 2 — Parameters.

Experiment settings bound 1:1 to the keys in spec_echem.settings. Load/Save
round-trips the full settings dict via JSON. Doping/dedoping/prededoping
potential fields are documentation-only in this phase (the Gamry sequence file
holds the real potentials) — labeled "recorded for reference".
"""
from pathlib import Path

from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QScrollArea,
    QPushButton, QLabel, QLineEdit, QPlainTextEdit, QCheckBox,
    QDoubleSpinBox, QSpinBox, QFileDialog,
)

from spec_echem.settings import load_settings, save_settings

REF_NOTE = "  (recorded for reference)"


class ParametersTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._widgets = {}   # settings key -> widget
        self._build()

    # --- widget factories that register by settings key ---

    def _dspin(self, key, lo, hi, decimals=3, step=0.1, suffix=""):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(decimals)
        w.setSingleStep(step)
        if suffix:
            w.setSuffix(suffix)
        self._widgets[key] = w
        return w

    def _ispin(self, key, lo, hi):
        w = QSpinBox()
        w.setRange(lo, hi)
        self._widgets[key] = w
        return w

    def _line(self, key):
        w = QLineEdit()
        self._widgets[key] = w
        return w

    def _check(self, key, label):
        w = QCheckBox(label)
        self._widgets[key] = w
        return w

    def _hint(self, widget, text):
        """Wrap a field with a grey example/format hint to its right."""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(widget)
        label = QLabel(text)
        label.setStyleSheet("color: #888;")
        row.addWidget(label)
        row.addStretch()
        return box

    def _build(self):
        outer = QVBoxLayout(self)

        # Load / Save row
        btn_row = QHBoxLayout()
        self.load_btn = QPushButton("Load Settings File")
        self.load_btn.clicked.connect(self.on_load)
        self.save_btn = QPushButton("Save Settings File")
        self.save_btn.clicked.connect(self.on_save)
        btn_row.addWidget(self.load_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        # Scrollable form body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # --- Sample info ---
        sample_group = QGroupBox("Sample Info")
        sform = QFormLayout(sample_group)
        sform.addRow("Sample name:", self._hint(self._line("sample_name"), "e.g. P3HT 95:05"))
        sform.addRow("Electrolyte:", self._hint(self._line("electrolyte"), "e.g. 0.1 M KPF6 / MeCN"))

        # Notes — full width, taller
        sform.addRow(QLabel("Notes:"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setFixedHeight(80)
        self.notes_edit.setPlaceholderText(
            "Film prep, atmosphere, anything worth recording with the data…")
        self._widgets["notes"] = self.notes_edit
        sform.addRow(self.notes_edit)

        # Data folder name + save-location browser + resolved full path
        sform.addRow("Data folder name:", self._hint(self._line("data_folder"), "YYYYMMDD_Description"))
        loc_box = QWidget()
        loc_row = QHBoxLayout(loc_box)
        loc_row.setContentsMargins(0, 0, 0, 0)
        self.data_root_edit = QLineEdit()
        self.data_root_edit.setReadOnly(True)
        self._widgets["data_root"] = self.data_root_edit
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.on_browse_root)
        loc_row.addWidget(self.data_root_edit)
        loc_row.addWidget(browse_btn)
        sform.addRow("Save location:", loc_box)
        self.full_path_label = QLabel()
        self.full_path_label.setStyleSheet("color: #555;")
        self.full_path_label.setWordWrap(True)
        sform.addRow("Full path:", self.full_path_label)
        self._widgets["data_folder"].textChanged.connect(self._update_full_path)
        self.data_root_edit.textChanged.connect(self._update_full_path)

        sform.addRow(self._check("trigger", "Wait for Gamry trigger"))
        layout.addWidget(sample_group)

        # --- Cyclic voltammetry ---
        cv_group = QGroupBox("Cyclic Voltammetry")
        cv_form = QFormLayout(cv_group)
        cv_form.addRow(self._check("cv_enabled", "Include CV"))
        cv_form.addRow("Cycles:", self._ispin("cv_cycles", 1, 1000))
        cv_form.addRow("Total voltage:", self._hint(
            self._dspin("cv_total_voltage", 0.0, 100.0, 3, 0.1, " V"),
            "total sweep path, e.g. 0→0.7→−0.5→0 = 2.4 V"))
        cv_form.addRow("Step size:", self._dspin("cv_step_size", 0.1, 1000.0, 1, 1.0, " mV"))
        cv_form.addRow("Scan rate:", self._dspin("cv_scan_rate", 0.1, 10000.0, 1, 10.0, " mV/s"))
        layout.addWidget(cv_group)

        # --- Pre-dedoping ---
        pre_group = QGroupBox("Pre-dedoping Baseline")
        pre_form = QFormLayout(pre_group)
        pre_form.addRow(self._check("prededoping_enabled", "Include pre-dedoping"))
        pre_form.addRow("Potential (vs Vref):" + REF_NOTE,
                        self._dspin("prededoping_potential", -10.0, 10.0, 3, 0.05, " V"))
        pre_form.addRow("Duration:", self._dspin("prededoping_time", 0.1, 100000.0, 1, 1.0, " s"))
        layout.addWidget(pre_group)

        # --- Doping / dedoping ---
        dope_group = QGroupBox("Doping / Dedoping Cycles")
        dope_form = QFormLayout(dope_group)
        dope_form.addRow(self._check("doping_enabled", "Include doping/dedoping"))
        dope_form.addRow("Doping start (vs Vref):" + REF_NOTE,
                         self._dspin("doping_potential_start", -10.0, 10.0, 3, 0.05, " V"))
        dope_form.addRow("Doping end (vs Vref):" + REF_NOTE,
                         self._dspin("doping_potential_end", -10.0, 10.0, 3, 0.05, " V"))
        dope_form.addRow("Doping step (vs Vref):" + REF_NOTE,
                         self._dspin("doping_potential_step", -10.0, 10.0, 3, 0.05, " V"))
        dope_form.addRow("Dedoping potential (vs Vref):" + REF_NOTE,
                         self._dspin("dedoping_potential", -10.0, 10.0, 3, 0.05, " V"))
        dope_form.addRow("Step duration:", self._dspin("chrono_time", 0.1, 100000.0, 1, 1.0, " s"))
        dope_form.addRow("Time between spectra:",
                         self._dspin("chrono_delta_time", 0.001, 100.0, 3, 0.01, " s"))
        layout.addWidget(dope_group)

        layout.addStretch()

    # --- settings round-trip ---

    def populate_from(self, settings):
        for key, w in self._widgets.items():
            if key not in settings:
                continue
            value = settings[key]
            if isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, (QDoubleSpinBox, QSpinBox)):
                w.setValue(value)
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText(str(value))
            elif isinstance(w, QLineEdit):
                w.setText(str(value))

    def collect_into(self, settings):
        for key, w in self._widgets.items():
            if isinstance(w, QCheckBox):
                settings[key] = w.isChecked()
            elif isinstance(w, (QDoubleSpinBox, QSpinBox)):
                settings[key] = w.value()
            elif isinstance(w, QPlainTextEdit):
                settings[key] = w.toPlainText()
            elif isinstance(w, QLineEdit):
                settings[key] = w.text()

    # --- data folder location ---

    def on_browse_root(self):
        start = self.data_root_edit.text() or ""
        path = QFileDialog.getExistingDirectory(
            self, "Choose where experiment folders are saved (you can create a new folder)", start)
        if path:
            self.data_root_edit.setText(path)

    def _update_full_path(self, *_):
        root = self.data_root_edit.text()
        name = self._widgets["data_folder"].text()
        self.full_path_label.setText(str(Path(root) / name) if (root or name) else "")

    # --- load / save ---

    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Settings", "", "JSON files (*.json)")
        if not path:
            return
        settings = load_settings(path)
        self.win.apply_settings(settings)

    def on_save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Settings", "settings.json", "JSON files (*.json)")
        if not path:
            return
        self.win.collect_settings()
        save_settings(self.win.settings, path)
