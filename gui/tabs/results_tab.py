"""
Tab 4 — Results.

Segment selector, wavelength range, absorbance plot (updates after each segment
completes — no live updating), and data-folder actions. The matplotlib canvas
is wired together with the Instrument-tab preview in the plotting increment.
"""
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton, QFileDialog,
)

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

        # --- absorbance plot ---
        self.canvas = MplCanvas(ylabel="Absorbance")
        layout.addWidget(self.canvas, stretch=1)

        # --- actions ---
        btn_row = QHBoxLayout()
        self.save_plot_btn = QPushButton("Save Plot")
        self.save_plot_btn.clicked.connect(self.on_save_plot)
        self.open_folder_btn = QPushButton("Open Data Folder")
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

    def on_save_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Plot", "plot.png",
                                              "PNG (*.png);;PDF (*.pdf)")
        if path:
            self.canvas.fig.savefig(path, dpi=150)
