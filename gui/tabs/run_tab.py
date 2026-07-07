"""
Tab 3 — Run.

Sequence progress, status log, run-state banner, and Start/Stop/Abort.
Threaded acquisition (workers.py) is wired in the next increment — for now the
buttons drive the banner so the two-step coordination UX can be reviewed.
"""
from qtpy.QtCore import Qt, QThread
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QListWidget,
    QPlainTextEdit, QPushButton, QMessageBox, QSplitter,
)

from pathlib import Path

from spec_echem.experiment import build_segments
from spec_echem.data import write_run_metadata
from spec_echem.logging_config import configure_run_logging, close_run_logging
from spec_echem.potentiostat import ExternalPotentiostat, ToolkitPotentiostat
from gui.widgets.plot_canvas import MplCanvas
from gui.workers import AcquisitionWorker


class RunTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._thread = None
        self._worker = None
        self._row_for_label = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        # --- controls + run-state banner (top row: Start is where the eye lands) ---
        top_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        self.abort_btn = QPushButton("ABORT")
        self.abort_btn.setStyleSheet("color: #b00; font-weight: bold;")
        self.abort_btn.clicked.connect(self.on_abort)
        self.abort_btn.setEnabled(False)
        top_row.addWidget(self.start_btn)
        top_row.addWidget(self.stop_btn)
        top_row.addWidget(self.abort_btn)
        top_row.addSpacing(12)
        self.banner = QLabel("Idle")
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setStyleSheet(
            "background: #eef; padding: 10px; font-weight: bold; border: 1px solid #ccd;"
        )
        top_row.addWidget(self.banner, stretch=1)
        layout.addLayout(top_row)

        # --- cockpit: sequence progress (left) + last-segment plot (right) ---
        cockpit = QSplitter(Qt.Horizontal)

        seq_group = QGroupBox("Sequence Progress")
        seq_layout = QVBoxLayout(seq_group)
        self.sequence_list = QListWidget()
        seq_layout.addWidget(self.sequence_list)
        cockpit.addWidget(seq_group)

        plot_group = QGroupBox("Last Completed Segment")
        plot_layout = QVBoxLayout(plot_group)
        self.canvas = MplCanvas(ylabel="Absorbance")
        plot_layout.addWidget(self.canvas)
        cockpit.addWidget(plot_group)

        cockpit.setStretchFactor(0, 1)
        cockpit.setStretchFactor(1, 2)
        layout.addWidget(cockpit, stretch=3)

        # --- status log ---
        log_group = QGroupBox("Status Log")
        log_layout = QVBoxLayout(log_group)
        self.status_log = QPlainTextEdit()
        self.status_log.setReadOnly(True)
        log_layout.addWidget(self.status_log)
        layout.addWidget(log_group, stretch=1)

    def log(self, message):
        self.status_log.appendPlainText(message)

    def show_segment(self, absorb_df, label):
        """Render a just-completed segment's absorbance (called post-segment)."""
        self.canvas.show_absorbance(absorb_df, title=label)

    def set_banner(self, text, color="#eef"):
        self.banner.setText(text)
        self.banner.setStyleSheet(
            f"background: {color}; padding: 10px; font-weight: bold; border: 1px solid #ccd;"
        )

    # --- run control ---

    def on_start(self):
        settings = self.win.collect_settings()

        # Normalize the folder name so stray whitespace can't pollute paths/filenames
        settings["data_folder"] = settings["data_folder"].strip()

        # Validation at the GUI boundary
        if self.win.spec is None:
            QMessageBox.warning(self, "Not connected",
                                "Connect the spectrometer on the Instrument tab first.")
            return
        if self.win.dark is None or self.win.ref is None:
            QMessageBox.warning(self, "Missing calibration",
                                "Collect a dark and a reference (100%T) on the Instrument tab first.")
            return
        if not settings["data_folder"].strip():
            QMessageBox.warning(self, "No data folder",
                                "Enter a data folder name on the Parameters tab.")
            return

        segments = build_segments(settings)
        if not segments:
            QMessageBox.warning(self, "Nothing to run",
                                "Enable at least one step (CV / pre-dedoping / doping) on the Parameters tab.")
            return

        # Guard against silently overwriting a previous run. The writers use
        # mkdir(exist_ok=True) and fixed filenames, so re-running into a folder that
        # already holds data clobbers it. Make the user confirm or go rename the folder.
        run_folder = Path(settings["data_root"]) / settings["data_folder"]
        if run_folder.exists() and any(run_folder.iterdir()):
            reply = QMessageBox.warning(
                self, "Folder already exists",
                f"\"{settings['data_folder']}\" already exists and contains files.\n\n"
                "Continuing may overwrite data from a previous run. Change the Data folder "
                "name on the Parameters tab to keep it, or continue to write here anyway.",
                QMessageBox.Cancel | QMessageBox.Ok, QMessageBox.Cancel)
            if reply != QMessageBox.Ok:
                self.log("Start cancelled — folder already exists (rename it to keep the old data).")
                return

        # Fresh status log per run (mirrors the sequence-progress reset below); the
        # full history is always preserved in each run's own .log file on disk.
        self.status_log.clear()

        # Write the self-documenting run metadata and open the per-run log file
        write_run_metadata(settings, settings["data_root"], settings["data_folder"])
        _, log_path = configure_run_logging(run_folder, settings["data_folder"])
        self.log(f"Logging to {log_path.name}")

        # Expose the run folder + segment map so the Results tab can find each
        # segment's echem file (written next to the spectra in Python mode).
        self.win.run_folder = run_folder
        self.win.segments_by_label = {seg.label: seg for seg in segments}

        # Build the progress list
        self.sequence_list.clear()
        self._row_for_label = {}
        for i, seg in enumerate(segments):
            self.sequence_list.addItem("○  " + seg.label)
            self._row_for_label[seg.label] = i

        # Pick the potentiostat: Python-controlled drives the Gamry itself;
        # external means the human starts the .GSequence (the proven default).
        python_mode = settings.get("potentiostat_mode", "external") == "python"
        potentiostat = ToolkitPotentiostat(settings) if python_mode else ExternalPotentiostat()

        # Spin up the worker on its own thread
        self._thread = QThread()
        self._worker = AcquisitionWorker(
            self.win.spec, segments, self.win.dark, self.win.ref, self.win.wavelengths,
            settings["data_root"], settings["data_folder"], potentiostat,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.segment_started.connect(self.on_segment_started)
        self._worker.segment_done.connect(self.on_segment_done)
        self._worker.status.connect(self.log)
        self._worker.finished.connect(self.on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

        if python_mode:
            self.set_banner("▶ Running — Python is driving the Gamry", "#dfd")
        else:
            self.set_banner("⏳ Armed — now START the Gamry sequence", "#ffd")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.abort_btn.setEnabled(True)
        self.win.instrument_tab._set_actions_enabled(False)  # avoid concurrent spec access

    def on_stop(self):
        if self._worker is not None:
            self._worker.request_stop()
        self.set_banner("Stopping after current segment…", "#eef")
        self.log("Stop requested — will finish current segment.")
        self.stop_btn.setEnabled(False)
        self.abort_btn.setEnabled(False)

    def on_abort(self):
        reply = QMessageBox.question(
            self, "Confirm Abort",
            "Abort immediately? Data for the current segment may be incomplete.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if self._worker is not None:
            self._worker.request_abort()
        self.set_banner("Aborting…", "#fdd")
        self.log("Abort requested.")
        self.stop_btn.setEnabled(False)
        self.abort_btn.setEnabled(False)

    # --- worker signal slots (run on the GUI thread) ---

    def on_segment_started(self, label, index, total):
        row = self._row_for_label.get(label)
        if row is not None:
            self.sequence_list.item(row).setText("●  " + label)
        self.set_banner(f"Collecting: {label}  ({index}/{total})", "#eef")

    def on_segment_done(self, label, absorb_df):
        row = self._row_for_label.get(label)
        if row is not None:
            self.sequence_list.item(row).setText("✓  " + label)
        self.win.results[label] = absorb_df
        self.show_segment(absorb_df, label)
        self.win.results_tab.refresh_segments()

    def on_finished(self, reason):
        banners = {
            "done": ("Sequence complete", "#dfd"),
            "stopped": ("Stopped", "#eef"),
            "aborted": ("ABORTED", "#fdd"),
            "error": ("Error — see log", "#fdd"),
        }
        text, color = banners.get(reason, ("Idle", "#eef"))
        self.set_banner(text, color)
        close_run_logging()
        self._reset_controls()
        self.win.instrument_tab._set_actions_enabled(True)
        self._worker = None

    def _reset_controls(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.abort_btn.setEnabled(False)
