"""
Tab 3 — Run.

Sequence progress, status log, run-state banner, and Start/Stop/Abort.
Threaded acquisition (workers.py) is wired in the next increment — for now the
buttons drive the banner so the two-step coordination UX can be reviewed.
"""
from qtpy.QtCore import Qt, QThread, QTimer, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QListWidget,
    QPlainTextEdit, QPushButton, QMessageBox, QSplitter, QCheckBox,
)

import copy
from pathlib import Path

from spec_echem.experiment import build_segments
from spec_echem.data import write_run_metadata, DATA_TYPE_CV
from spec_echem.logging_config import (configure_run_logging, close_run_logging,
                                       get_run_logger, app_log_path)
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
        self._live_timer = None
        self._current_segment = None
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
        self.live_check = QCheckBox("Live echem")
        self.live_check.setChecked(True)
        self.live_check.setToolTip(
            "Draw the echem curve live during a Python-mode run. Uncheck to compare "
            "the spectra cadence (logged per segment) with the plot off.")
        top_row.addWidget(self.live_check)
        top_row.addSpacing(12)
        self.banner = QLabel("Idle")
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setStyleSheet(
            "background: #eef; padding: 10px; font-weight: bold; border: 1px solid #ccd;"
        )
        top_row.addWidget(self.banner, stretch=1)
        layout.addLayout(top_row)

        # --- cockpit: sequence progress (left) + plots (right) ---
        cockpit = QSplitter(Qt.Horizontal)

        seq_group = QGroupBox("Sequence Progress")
        seq_layout = QVBoxLayout(seq_group)
        self.sequence_list = QListWidget()
        seq_layout.addWidget(self.sequence_list)
        cockpit.addWidget(seq_group)

        # Right side, stacked: the live echem trace (updates DURING a Python-mode
        # segment — so there's visible feedback mid-run) above the last completed
        # segment's absorbance.
        right = QSplitter(Qt.Vertical)

        live_group = QGroupBox("Live Echem (current segment)")
        live_layout = QVBoxLayout(live_group)
        self.live_canvas = MplCanvas(xlabel="Potential (V)", ylabel="Current (A)")
        live_layout.addWidget(self.live_canvas)
        right.addWidget(live_group)

        plot_group = QGroupBox("Last Completed Segment")
        plot_layout = QVBoxLayout(plot_group)
        self.canvas = MplCanvas(ylabel="Absorbance")
        plot_layout.addWidget(self.canvas)
        right.addWidget(plot_group)

        cockpit.addWidget(right)
        cockpit.setStretchFactor(0, 1)
        cockpit.setStretchFactor(1, 2)
        layout.addWidget(cockpit, stretch=3)

        # --- status log ---
        # This pane shows THIS run only (it is cleared at Start). The full history —
        # including everything before a run, like connecting the instruments — is in
        # the app log on disk, which is what the button is for: a log nobody can find
        # is a log nobody uses.
        log_group = QGroupBox("Status Log")
        log_layout = QVBoxLayout(log_group)
        self.status_log = QPlainTextEdit()
        self.status_log.setReadOnly(True)
        log_layout.addWidget(self.status_log)
        log_btn_row = QHBoxLayout()
        log_btn_row.addStretch()
        self.open_log_btn = QPushButton("Open Log Folder")
        self.open_log_btn.setToolTip(
            "Open the folder holding spec-echem.log — the full history of this and "
            "previous sessions, including instrument connections made before a run.")
        self.open_log_btn.clicked.connect(self.on_open_log_folder)
        log_btn_row.addWidget(self.open_log_btn)
        log_layout.addLayout(log_btn_row)
        layout.addWidget(log_group, stretch=1)

    def on_open_log_folder(self):
        folder = app_log_path(self.win.settings["data_root"]).parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
        # Snapshot the settings for THIS run. collect_settings() returns the live
        # canonical dict, and the potentiostat reads potentials/paths from it per
        # segment — so without a copy a mid-run "Save Settings" (Parameters tab isn't
        # locked) would change the potentials applied to the remaining segments and
        # desync the run from its own metadata. Freeze it at Start.
        settings = copy.deepcopy(self.win.collect_settings())

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

        # Which hardware produced this folder. Connect happens long before Start, when
        # no run log exists yet, so the identities are stashed at Connect and recorded
        # here instead — the first moment there is somewhere durable to put them.
        instruments = {
            "spectrometer": self.win.spec_identity or "unknown (not connected via Connect)",
            "potentiostat": self.win.pstat_identity if
                settings.get("potentiostat_mode", "external") == "python"
                else "external — Gamry runs its own .GSequence (not queried)",
        }
        if instruments["potentiostat"] is None:
            instruments["potentiostat"] = "unknown (Connect Potentiostat not used)"

        # Write the self-documenting run metadata and open the per-run log file
        write_run_metadata(settings, settings["data_root"], settings["data_folder"],
                           instruments=instruments)
        _, log_path = configure_run_logging(run_folder, settings["data_folder"])
        self.log(f"Logging to {log_path.name}")
        get_run_logger().info("Spectrometer: %s", instruments["spectrometer"])
        get_run_logger().info("Potentiostat: %s", instruments["potentiostat"])

        # Expose the run folder + segment map so the Results tab can find each
        # segment's echem file (written next to the spectra in Python mode).
        self.win.run_folder = run_folder
        self.win.segments_by_label = {seg.label: seg for seg in segments}

        # Clear results from any previous run / loaded folder so the Results tab shows
        # ONLY this run. Segment labels repeat between runs, so a longer prior run
        # would otherwise leave stale extra segments (e.g. "Doping 6") mixed in.
        self.win.results = {}
        self.win.results_tab.refresh_segments()

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
        # Safe teardown: let the thread's own event loop delete the worker and itself
        # once it has fully stopped, then drop our Python refs — never delete a QObject
        # from the wrong thread, and never reuse/replace a still-running QThread.
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

        # Live echem feedback (Python mode): poll the potentiostat's growing
        # acq_data snapshot and redraw here on the GUI thread, throttled — this
        # never touches the acquisition thread, so it can't affect timing.
        self._current_segment = None
        if python_mode and self.live_check.isChecked():
            self.live_canvas.show_message("Waiting for the first segment…")
            self._live_timer = QTimer(self)
            self._live_timer.setInterval(400)
            self._live_timer.timeout.connect(self._update_live_echem)
            self._live_timer.start()
        elif python_mode:
            self.live_canvas.show_message("Live echem plot off (timing comparison).")
        else:
            self.live_canvas.show_message(
                "External mode — Gamry Framework shows the live echem data.")

        if python_mode:
            self.set_banner("▶ Running — Python is driving the Gamry", "#dfd")
        else:
            self.set_banner("⏳ Armed — now START the Gamry sequence", "#ffd")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.abort_btn.setEnabled(True)
        self.win.instrument_tab._set_actions_enabled(False)  # avoid concurrent spec access
        self.win.instrument_tab.lock_for_run(True)           # lock Connect/Simulated too

    def on_stop(self):
        if self._worker is not None:
            self._worker.request_stop()
        self.set_banner("Stopping after current segment…", "#eef")
        self.log("Stop requested — will finish current segment.")
        self.stop_btn.setEnabled(False)
        # Leave ABORT enabled: Stop only takes effect BETWEEN segments, so if the
        # current segment is blocked waiting for a trigger, Abort is the only escape.

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
        # Tell the live-echem timer which segment (CV → I-vs-E, chrono → I-vs-t).
        self._current_segment = self.win.segments_by_label.get(label)
        if self._live_timer is not None:
            self.live_canvas.show_message(f"{label} — waiting for data…")

    def _update_live_echem(self):
        """Timer slot (GUI thread): draw the potentiostat's growing acq_data
        snapshot. Python mode only; no-op until a segment is producing data."""
        worker, seg = self._worker, self._current_segment
        if worker is None or seg is None:
            return
        pot = worker.potentiostat
        data = pot.live_data() if pot is not None else None
        if data is None or len(data) == 0:
            return
        fields = data.dtype.names or ()
        if "im" not in fields:
            return
        current = data["im"]
        if seg.data_type == DATA_TYPE_CV and "vf" in fields:
            self.live_canvas.update_live_line(
                data["vf"], current, "Potential (V)", "Current (A)",
                title=f"{seg.label} — live")
        elif "time" in fields:
            t = data["time"]
            t0 = t[0] if len(t) else 0.0
            self.live_canvas.update_live_line(
                t - t0, current, "Time (s)", "Current (A)",
                title=f"{seg.label} — live")

    def _stop_live_timer(self):
        if self._live_timer is not None:
            self._live_timer.stop()
            self._live_timer = None

    def on_segment_done(self, label, absorb_df):
        seg = self.win.segments_by_label.get(label)
        discarded = seg is not None and not seg.save

        row = self._row_for_label.get(label)
        if row is not None:
            self.sequence_list.item(row).setText(
                ("✓  " + label + "  (data discarded)") if discarded else ("✓  " + label))

        # A discarded segment is shown live here as it happens — you still want to watch
        # it run — but it never enters win.results, which is the "data you have" view
        # behind the Results tab. Nothing was written; nothing should be reviewable.
        self.show_segment(absorb_df, label)
        if discarded:
            self.log(f"{label} complete — data discarded, nothing saved.")
            return
        self.win.results[label] = absorb_df
        self.win.results_tab.refresh_segments()

    def on_finished(self, reason):
        self._update_live_echem()   # draw the last segment's final curve
        self._stop_live_timer()
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
        self.win.instrument_tab.lock_for_run(False)          # unlock Connect/Simulated
        # Note: worker/thread refs are cleared in _on_thread_finished (after the
        # thread's event loop has actually stopped), not here — on_finished runs on
        # worker.finished, which is BEFORE the thread has finished.

    def _on_thread_finished(self):
        # Runs on QThread.finished, once the worker thread's event loop has stopped
        # and deleteLater has been scheduled. Guard against a fast restart having
        # already installed a new thread: only clear if this is still the active one.
        if self.sender() is self._thread:
            self._worker = None
            self._thread = None

    def _reset_controls(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.abort_btn.setEnabled(False)
