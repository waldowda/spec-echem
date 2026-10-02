"""
The figure preview: one dialog, reused by every plot that can be saved.

A saved figure is rendered at a size CHOSEN here, not inherited from the window it
was drawn in -- see MplCanvas.render_to_figure. This dialog is where that size is
chosen, and it shows the actual output rather than a hint of it.

Deliberately NOT a figure editor. Size, dpi, format, provenance, and the data behind
the plot. If axis-label or title fields ever appear here the design has failed, and
the CSV is the answer to whatever prompted them.
"""
import logging
from pathlib import Path

import matplotlib
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                               NavigationToolbar2QT)
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                            QLabel, QMessageBox, QPushButton, QScrollArea,
                            QSpinBox, QVBoxLayout, QWidget)

from spec_echem.build_info import build_id

logger = logging.getLogger(__name__)

# (label, (width_in, height_in), base font pt)
#
# Each preset carries its OWN font size and RE-RENDERS; it never scales another
# preset's output. That is the whole point of having presets. A 6.5 in figure shrunk
# to a 3.25 in journal column takes its 10 pt text down to an effective 5 pt, which
# is the usual way a good-looking draft figure dies in proof -- confirmed by eye on
# 2026-09-30, where the scaled-down version was "overpowered by the size of the
# axis". The single-column preset draws at 8 pt from the start instead.
# Single column is EXACTLY half of double column, so the only thing that differs
# between them is scale and font -- which is what makes comparing the two honest.
# An earlier 3.25 x 2.4 was a slightly different shape for no reason.
#
# Its text is still proportionally larger than the double's, and that is not a bug
# to tune away: matching the double's proportions at half the width needs 5 pt,
# which is below what journals accept (typically 6-8 pt at final size). Legibility
# at print size sets a floor, so a single-column figure always looks label-heavy on
# screen and correct on paper. Use double column unless a journal demands 3.25 in.
PRESETS = [
    ("Double column — 6.5 × 4.5 in", (6.5, 4.5), 10),
    ("Single column — 3.25 × 2.25 in", (3.25, 2.25), 7),
    ("Wide — 9.0 × 4.5 in", (9.0, 4.5), 10),
    ("Slide — 10 × 7.5 in", (10.0, 7.5), 14),
]
DEFAULT_PRESET = 0


def preset_rc(pt):
    """The full font set for a preset, from its base size.

    matplotlib defaults axes.titlesize to 'large' = 1.2x the base, which makes the
    TITLE the heaviest thing on a small figure -- at 7 pt base it renders at 8.4 pt
    and dominates (observed 2026-09-30). Here the title only names the segment, so
    it is set equal to the axis labels rather than above them; journals usually drop
    figure titles altogether. Ticks go one point below, which is the usual house
    style and keeps the numbers from competing with the data.
    """
    return {
        "font.size": pt,
        "axes.titlesize": pt,
        "axes.labelsize": pt,
        "xtick.labelsize": pt - 1,
        "ytick.labelsize": pt - 1,
        "legend.fontsize": pt - 1,
        "figure.titlesize": pt,
    }

# The preview is drawn at screen resolution and SAVED at the chosen dpi. Same
# figure, same inches, same point sizes -- only the pixel count differs, so what is
# on screen is what lands in the file.
DISPLAY_DPI = 100


class PlotToolbar(NavigationToolbar2QT):
    """Home / pan / zoom only -- used BOTH in this dialog and on the live tabs.

    The save button is removed rather than kept alongside ours: two save buttons on
    one figure is worse than one ambiguous one, and the toolbar's writes at whatever
    size the widget happens to be, at the figure's own dpi, with no provenance --
    which is the entire failure this work exists to remove. 'Subplots' goes too: the
    layout is managed, and nudging it by hand produces something no preset can
    reproduce.
    """
    toolitems = [t for t in NavigationToolbar2QT.toolitems
                 if t[0] not in ("Save", "Subplots", "Customize")]


class FigureDialog(QDialog):
    """Preview a plot at output size, then save it and/or its data.

    draw      -- zero-arg callable invoking a draw method on `canvas`
    csv       -- zero-arg callable returning a DataFrame, or None for no CSV
    basename  -- default filename stem; the CSV shares it, so the pair cannot
                 separate once they are in a folder together
    out_dir   -- where the file dialogs open, normally {run_folder}/figures
    run_id    -- names the run in the provenance line
    """

    def __init__(self, parent, canvas, draw, title="Save figure",
                 csv=None, basename="figure", out_dir=None, run_id=None,
                 note=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._canvas, self._draw, self._csv = canvas, draw, csv
        self._basename, self._out_dir, self._run_id = basename, out_dir, run_id
        # A caveat the PLOT wants to carry, e.g. what shading on it means. Rides
        # with the provenance stamp rather than being its own control: both are
        # prose about the figure, and both are unwanted in a journal submission.
        self._note = note
        self._fig = None
        self._preview = None
        self._build()
        self._render()

    # --- layout ---------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._scroll, 1)

        self._toolbar_holder = QWidget()
        QHBoxLayout(self._toolbar_holder).setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._toolbar_holder)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Size:"))
        self.preset_combo = QComboBox()
        for label, size, pt in PRESETS:
            self.preset_combo.addItem(label, (size, pt))
        self.preset_combo.setCurrentIndex(DEFAULT_PRESET)
        self.preset_combo.currentIndexChanged.connect(self._render)
        opts.addWidget(self.preset_combo)

        opts.addWidget(QLabel("dpi:"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(300)
        opts.addWidget(self.dpi_spin)

        # Off by default for a single deliberate save, on for a save-all: a run
        # stamp is invaluable on a talk slide and unwanted in a submission.
        self.provenance_check = QCheckBox("Stamp run and build on the figure")
        self.provenance_check.toggled.connect(self._render)
        opts.addWidget(self.provenance_check)
        opts.addStretch()
        layout.addLayout(opts)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save figure…")
        self.save_btn.clicked.connect(self.on_save_figure)
        buttons.addWidget(self.save_btn)
        self.csv_btn = QPushButton("Save data (CSV)…")
        self.csv_btn.clicked.connect(self.on_save_csv)
        # Hidden, not disabled: a dead control invites a click and explains nothing.
        # A spectra view is a wavelength x time matrix already archived as .h5 and
        # .txt, so a CSV there would be a worse copy of something on disk.
        self.csv_btn.setVisible(self._csv is not None)
        buttons.addWidget(self.csv_btn)
        buttons.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

    # --- rendering ------------------------------------------------------

    def provenance_text(self):
        """The stamp, or None. Kept short -- it sits under the axes."""
        if not self.provenance_check.isChecked():
            return None
        parts = [p for p in (self._run_id, f"spec-echem {build_id()}") if p]
        stamp = " · ".join(parts)
        return f"{stamp}\n{self._note}" if self._note else stamp

    def _render(self, *_):
        size, pt = self.preset_combo.currentData()
        # rc_context, not a global rcParams write: the main window's live canvases
        # are drawn from the same defaults and must not inherit this.
        with matplotlib.rc_context(preset_rc(pt)):
            self._fig = self._canvas.render_to_figure(
                self._draw, figsize=size, dpi=DISPLAY_DPI,
                footnote=self.provenance_text())

        preview = FigureCanvasQTAgg(self._fig)
        preview.setFixedSize(int(size[0] * DISPLAY_DPI), int(size[1] * DISPLAY_DPI))
        self._scroll.setWidget(preview)
        self._preview = preview

        holder = self._toolbar_holder.layout()
        while holder.count():
            old = holder.takeAt(0).widget()
            if old is not None:
                old.deleteLater()
        holder.addWidget(PlotToolbar(preview, self._toolbar_holder))
        self._scroll.setMinimumSize(
            min(1100, preview.width() + 24), min(700, preview.height() + 24))

    # --- saving ---------------------------------------------------------

    def _ask_path(self, suffix, filters):
        start = str((self._out_dir / f"{self._basename}{suffix}")
                    if self._out_dir is not None else f"{self._basename}{suffix}")
        if self._out_dir is not None:
            try:
                self._out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.debug("could not create %s: %s", self._out_dir, exc)
        path, _ = QFileDialog.getSaveFileName(self, "Save", start, filters)
        return path

    def on_save_figure(self):
        # PNG first: lossless, which a line plot needs -- JPEG rings around sharp
        # edges and a plot is nothing but sharp edges and text. PDF/SVG are here
        # because they are what a journal actually wants.
        path = self._ask_path(
            ".png", "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tif)")
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=self.dpi_spin.value())
        except Exception as exc:        # noqa: BLE001 — a bad path must not crash
            QMessageBox.warning(self, "Could not save", str(exc))
            return
        self.save_btn.setText("Saved ✓")

    def on_save_csv(self):
        path = self._ask_path(".csv", "CSV (*.csv)")
        if not path:
            return
        try:
            frame = self._csv()
            if frame is None or frame.empty:
                QMessageBox.information(self, "Nothing to write",
                                        "This plot has no tabular data.")
                return
            write_csv(frame, path, self._header_lines())
        except Exception as exc:        # noqa: BLE001
            QMessageBox.warning(self, "Could not save", str(exc))
            return
        self.csv_btn.setText("Saved ✓")

    def _header_lines(self):
        """Provenance for the CSV. ALWAYS written, unlike the figure's stamp: a data
        file outlives the context that produced it, and a figure at least shows its
        own axes."""
        lines = []
        if self._run_id:
            lines.append(f"run: {self._run_id}")
        lines.append(f"produced by: spec-echem {build_id()}")
        lines.append(f"figure: {self._basename}")
        return lines


def write_csv(frame, path, header_lines=()):
    """A DataFrame with `#` provenance above it. Read back with comment='#'."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in header_lines:
            fh.write(f"# {line}\n")
        frame.to_csv(fh, index=False)


def save_figure(canvas, draw, path, preset=DEFAULT_PRESET, dpi=300,
                provenance=None, csv_frame=None, csv_header=()):
    """Render and write one figure without opening the preview.

    The save-all path: same geometry, same fonts and the same provenance rules as a
    single save, so a batch cannot quietly differ from what the preview showed.
    Returns the paths written.
    """
    _label, size, pt = PRESETS[preset]
    with matplotlib.rc_context(preset_rc(pt)):
        fig = canvas.render_to_figure(draw, figsize=size, dpi=DISPLAY_DPI,
                                      footnote=provenance)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi)
    written = [path]
    # The figure and its numbers share a basename so the pair cannot separate once
    # they are in a folder together.
    if csv_frame is not None and not csv_frame.empty:
        csv_path = path.with_suffix(".csv")
        write_csv(csv_frame, csv_path, csv_header)
        written.append(csv_path)
    return written


def open_figure_dialog(parent, canvas, win, basename, title="Save figure",
                       note=None):
    """Open the preview for whatever `canvas` last drew.

    The one entry point the tabs use, so the five save buttons cannot drift apart.
    Files default to {run_folder}/figures -- the same reasoning as the per-run log:
    a figure found a year later says which run produced it by where it sits.
    """
    draw = canvas.last_draw()
    if draw is None:
        QMessageBox.information(parent, "Nothing to save",
                                "There is no plot here yet.")
        return None
    run_folder = getattr(win, "run_folder", None)
    frame = canvas.last_data()
    dialog = FigureDialog(
        parent, canvas, draw, title=title,
        # Re-read at save time rather than capturing the frame now: the plot can be
        # redrawn while the dialog is open on a non-modal day.
        csv=(lambda: canvas.last_data()) if frame is not None else None,
        basename=basename,
        out_dir=(run_folder / "figures") if run_folder is not None else None,
        run_id=run_folder.name if run_folder is not None else None,
        note=note)
    dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
    return dialog
