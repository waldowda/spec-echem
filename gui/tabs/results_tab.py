"""
Tab 4 — Results.

Segment selector, wavelength range, absorbance plot (updates after each segment
completes — no live updating), and data-folder actions. The matplotlib canvas
is wired together with the Instrument-tab preview in the plotting increment.
"""
from pathlib import Path

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton, QFileDialog, QSplitter, QMessageBox,
)

from spec_echem.data import echem_txt_path, DATA_TYPE_CV
from spec_echem.gamry_data import read_cv, read_chrono
from gui.widgets.plot_canvas import MplCanvas


class ResultsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        # --- selector / range controls ---
        ctrl_group = QGroupBox("View")
        ctrl_form = QFormLayout(ctrl_group)
        self.segment_combo = QComboBox()
        self.segment_combo.currentTextChanged.connect(self.on_segment_changed)
        ctrl_form.addRow("Segment:", self.segment_combo)

        range_row = QHBoxLayout()
        self.wl_min = QDoubleSpinBox()
        self.wl_min.setRange(0.0, 5000.0)
        self.wl_min.setValue(380.0)
        self.wl_min.setSuffix(" nm")
        self.wl_max = QDoubleSpinBox()
        self.wl_max.setRange(0.0, 5000.0)
        self.wl_max.setValue(1100.0)
        self.wl_max.setSuffix(" nm")
        range_row.addWidget(QLabel("min"))
        range_row.addWidget(self.wl_min)
        range_row.addWidget(QLabel("max"))
        range_row.addWidget(self.wl_max)
        range_row.addStretch()
        self.replot_btn = QPushButton("Apply Range")
        self.replot_btn.clicked.connect(self.on_segment_changed)
        range_row.addWidget(self.replot_btn)
        ctrl_form.addRow("Wavelength range:", range_row)
        layout.addWidget(ctrl_group)

        # --- plots: absorbance (optical) above electrochemistry, stacked ---
        # Vertical here (not side by side): with only two plots and the absorbance
        # colorbar taking width, stacking gives wider, better-proportioned graphs.
        plots = QSplitter(Qt.Vertical)

        abs_box = QGroupBox("Absorbance (optical)")
        abs_layout = QVBoxLayout(abs_box)
        self.canvas = MplCanvas(ylabel="Absorbance")
        abs_layout.addWidget(self.canvas)
        plots.addWidget(abs_box)

        echem_box = QGroupBox("Electrochemistry")
        echem_layout = QVBoxLayout(echem_box)
        self.echem_canvas = MplCanvas(xlabel="Potential (V)", ylabel="Current (A)")
        echem_layout.addWidget(self.echem_canvas)
        plots.addWidget(echem_box)

        plots.setStretchFactor(0, 1)
        plots.setStretchFactor(1, 1)
        layout.addWidget(plots, stretch=1)

        # --- actions ---
        btn_row = QHBoxLayout()
        self.save_plot_btn = QPushButton("Save Plots")
        self.save_plot_btn.clicked.connect(self.on_save_plot)
        self.open_folder_btn = QPushButton("Open Data Folder")
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        btn_row.addWidget(self.save_plot_btn)
        btn_row.addWidget(self.open_folder_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # --- segment selection / plotting ---

    def refresh_segments(self):
        """Repopulate the dropdown from the main window's results store."""
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        self.segment_combo.addItems(list(self.win.results.keys()))
        self.segment_combo.blockSignals(False)
        self.on_segment_changed()

    def on_segment_changed(self, *_):
        label = self.segment_combo.currentText()
        if not label or label not in self.win.results:
            return
        absorb_df = self.win.results[label]
        self.canvas.show_absorbance(
            absorb_df, title=label,
            wl_min=self.wl_min.value(), wl_max=self.wl_max.value(),
        )
        self._plot_echem(label)

    def _plot_echem(self, label):
        """Show the segment's electrochemistry (I-vs-E for CV, I-vs-t for chrono).
        Echem files are written in Python mode; in External mode they come from the
        Gamry Framework + conversion, so a missing file is normal, not an error.
        Sets self._has_echem so Save Plots knows whether the echem panel holds a plot."""
        self._has_echem = False
        seg = self.win.segments_by_label.get(label)
        if seg is None or self.win.run_folder is None:
            self.echem_canvas.show_message("No echem data yet — run a sequence.")
            return
        path = echem_txt_path(self.win.run_folder, seg.data_type, seg.run_number)
        if not path.exists():
            self.echem_canvas.show_message(
                "No echem file for this segment.\n\n"
                "Python mode saves echem data here;\n"
                "External mode records it via Gamry Framework.")
            return
        try:
            if seg.data_type == DATA_TYPE_CV:
                self.echem_canvas.show_cv(read_cv(path), title=label)
            else:
                self.echem_canvas.show_chrono(read_chrono(path), title=label)
            self._has_echem = True
        except Exception as exc:  # noqa: BLE001 — surface a bad/short file as a note, not a crash
            self.echem_canvas.show_message(f"Could not read echem file:\n{exc}")

    def on_save_plot(self):
        """Save the absorbance and (when present) echem plots as two files, named
        from the chosen base with _absorbance / _echem suffixes so both segments'
        views are captured, not just the optical one."""
        label = self.segment_combo.currentText() or "plot"
        start = str(self.win.run_folder / label) if self.win.run_folder else label
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plots (absorbance + echem)", start + ".png",
            "PNG (*.png);;PDF (*.pdf)")
        if not path:
            return
        p = Path(path)
        abs_path = p.with_name(f"{p.stem}_absorbance{p.suffix}")
        self.canvas.fig.savefig(abs_path, dpi=150)
        saved = [abs_path.name]
        if getattr(self, "_has_echem", False):
            echem_path = p.with_name(f"{p.stem}_echem{p.suffix}")
            self.echem_canvas.fig.savefig(echem_path, dpi=150)
            saved.append(echem_path.name)
        QMessageBox.information(self, "Saved", "Saved:\n" + "\n".join(saved))

    def on_open_folder(self):
        """Open the run folder in the OS file browser (Explorer / Finder)."""
        folder = self.win.run_folder
        if folder is None or not Path(folder).exists():
            QMessageBox.information(self, "No data folder",
                                   "No run folder yet — run a sequence first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
