"""
Tab 4 — Results.

Segment selector, wavelength range, absorbance plot (updates after each segment
completes — no live updating), and data-folder actions. The matplotlib canvas
is wired together with the Instrument-tab preview in the plotting increment.
"""
from pathlib import Path

import numpy as np

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton, QFileDialog, QSplitter, QMessageBox,
    QProgressDialog, QApplication,
)

from spec_echem.analysis import probe_wavelength
from spec_echem.data import (
    echem_txt_path, read_spectra_absorbance, discover_run_segments, DATA_TYPE_CV,
    DATA_TYPE_DOPING, segment_potential_text, segment_potential,
)
from spec_echem.experiment import Segment
from spec_echem.gamry_data import read_cv, read_chrono
from gui.widgets.plot_canvas import MplCanvas


# How many segments the Segment dropdown shows before it needs scrolling. Qt's
# default is 10, which silently hid the tail of a 14-segment run (2026-09-11).
# 26 covers a 0.0-1.2 V ladder in 0.1 V steps; longer ones scroll, which is fine —
# what matters is that nothing is invisible AND unscrollable.
SEGMENT_COMBO_VISIBLE = 26


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
        # A 0.2-0.7 V ladder in 0.1 V steps is 14 reviewable segments, and Qt's
        # default maxVisibleItems is 10 — so on 2026-09-11 the Results tab appeared
        # to be missing Doping/Dedoping 4 and 5. The data was all there; the 10th
        # entry was simply the last one visible. A run that looks like it lost the
        # end of its ladder is exactly the wrong thing for this tab to imply.
        self.segment_combo.setMaxVisibleItems(SEGMENT_COMBO_VISIBLE)
        # Qt ignores maxVisibleItems when a style uses a NATIVE popup (Windows does).
        # This forces the list-view popup, which honours it and scrolls beyond it.
        self.segment_combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
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

        # Three views of the same optical data. Spectra is the original and stays the
        # default; the other two are what make this tab useful DURING a run rather
        # than only after it.
        view_row = QHBoxLayout()
        self.view_combo = QComboBox()
        self.view_combo.addItem("Spectra (all times)", "spectra")
        self.view_combo.addItem("Kinetics (one wavelength)", "kinetics")
        self.view_combo.addItem("Modulation (across the ladder)", "modulation")
        self.view_combo.setToolTip(
            "Modulation is the one to watch while a run is going: absorbance at the\n"
            "end of each step, against potential. A film that stops modulating has\n"
            "stopped being worth the rest of the ladder.")
        self.view_combo.currentIndexChanged.connect(self.on_segment_changed)
        self.analysis_wl = QDoubleSpinBox()
        self.analysis_wl.setRange(0.0, 5000.0)
        self.analysis_wl.setDecimals(1)
        self.analysis_wl.setSuffix(" nm")
        self.analysis_wl.setSpecialValueText("auto (polaron)")
        self.analysis_wl.setValue(0.0)
        self.analysis_wl.setToolTip(
            "0 = the band whose absorbance GROWS most across the segment, which is\n"
            "the polaron. Set a value to follow another band, e.g. the pi-pi* bleach.")
        self.analysis_wl.valueChanged.connect(self.on_segment_changed)
        view_row.addWidget(self.view_combo)
        view_row.addWidget(QLabel("at"))
        view_row.addWidget(self.analysis_wl)
        # "auto (polaron)" said nothing about WHICH wavelength it picked, so the
        # number only existed inside a plot legend the user might not be looking at.
        self.auto_wl_label = QLabel("")
        self.auto_wl_label.setStyleSheet("color: #555;")
        self.auto_wl_label.setToolTip("The wavelength automatic selection resolved to.")
        view_row.addWidget(self.auto_wl_label)
        view_row.addStretch()
        ctrl_form.addRow("Optical view:", view_row)

        layout.addWidget(ctrl_group)

        # --- plots: absorbance (optical) above electrochemistry, stacked ---
        # Vertical here (not side by side): with only two plots and the absorbance
        # colorbar taking width, stacking gives wider, better-proportioned graphs.
        plots = QSplitter(Qt.Vertical)

        abs_box = QGroupBox("Absorbance (optical)")
        abs_layout = QVBoxLayout(abs_box)
        self.canvas = MplCanvas(ylabel="Absorbance")
        self.canvas.mpl_connect("button_press_event", self._on_spectra_click)
        self.canvas.setToolTip(
            "Click the spectrum to set the analysis wavelength.\n"
            "The red line marks the wavelength the Kinetics and\n"
            "Modulation views are sampling.")
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
        self.load_run_btn = QPushButton("Load Run…")
        self.load_run_btn.setToolTip(
            "Open a previously saved run folder and view its spectra + echem here.")
        self.load_run_btn.clicked.connect(self.on_load_run)
        self.save_plot_btn = QPushButton("Save Plots")
        self.save_plot_btn.clicked.connect(self.on_save_plot)
        self.open_folder_btn = QPushButton("Open Data Folder")
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        btn_row.addWidget(self.load_run_btn)
        btn_row.addWidget(self.save_plot_btn)
        btn_row.addWidget(self.open_folder_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # --- segment selection / plotting ---

    def refresh_segments(self):
        """Repopulate the dropdown from the main window's results store, keeping the
        current selection if it still exists — so a mid-run completion doesn't yank
        the user back to the first segment while they're inspecting another."""
        current = self.segment_combo.currentText()
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        labels = list(self.win.results.keys())
        self.segment_combo.addItems(labels)
        if current in labels:
            self.segment_combo.setCurrentText(current)
        self.segment_combo.blockSignals(False)
        self.on_segment_changed()

    def _segment_title(self, label):
        """'Doping 4' -> 'Doping 4  (+0.600 V)'.

        Falls back to the bare label when the segment is not in this run's map — a
        loaded folder from another session, say — rather than guessing a potential
        from the current settings, which would be worse than none.
        """
        seg = self.win.segments_by_label.get(label)
        if seg is None:
            return label
        text = self.win.segment_potential_text(seg)
        return f"{label}  ({text})" if text else label

    def on_segment_changed(self, *_):
        label = self.segment_combo.currentText()
        if not label or label not in self.win.results:
            return
        absorb_df = self.win.results[label]
        view = self.view_combo.currentData() if hasattr(self, "view_combo") else "spectra"
        if view == "kinetics":
            self._plot_kinetics(label, absorb_df)
        elif view == "modulation":
            self._plot_modulation()
        else:
            # "Doping 4" says which segment, not which experiment. The potential is what
            # the reader actually wants, and it comes from data.segment_potential_text()
            # so the title cannot drift from what the driver applied.
            self.canvas.show_absorbance(
                absorb_df, title=self._segment_title(label),
                wl_min=self.wl_min.value(), wl_max=self.wl_max.value(),
                mark_wl=self._chosen_wavelength(absorb_df, label),
            )
        self._plot_echem(label)

    def _on_spectra_click(self, event):
        """Click the spectrum to set the analysis wavelength.

        Only in the spectra view: on the kinetics and modulation views the x-axis is
        time and potential, so a click there means nothing about wavelength.
        """
        if self.view_combo.currentData() != "spectra":
            return
        if event.inaxes is not self.canvas.ax or event.xdata is None:
            return
        # setValue re-runs on_segment_changed, which redraws with the marker moved.
        self.analysis_wl.setValue(round(float(event.xdata), 1))

    def _chosen_wavelength(self, absorb_df, label):
        """The wavelength to follow: the user's, or the polaron band.

        Automatic goes through analysis.probe_wavelength, which knows that the polaron
        GROWS on doping but DECAYS on dedoping. This used to take the growing band
        unconditionally, so every dedoping segment followed pi-pi* while the control
        said "auto (polaron)".
        """
        wl = np.asarray(absorb_df.index.values, dtype=float)
        requested = self.analysis_wl.value()
        if requested > 0:
            # Blank the readout here too, or it keeps showing the last automatic pick
            # beside a box that now says something else.
            self._show_resolved_wavelength(None)
            return float(wl[int(np.abs(wl - requested).argmin())])

        seg = self.win.segments_by_label.get(label)
        if seg is not None and seg.data_type == DATA_TYPE_CV:
            # A CV returns to where it started, so A(end) - A(start) is ~0 and the
            # signed difference has no polaron to find -- it was handing back whatever
            # drifted most, 521.9 nm on 20250710_P3HT9010_KPF6. Pick a band by hand
            # to watch one during a sweep.
            self._show_resolved_wavelength(None)
            return None

        doping = seg is None or seg.data_type == DATA_TYPE_DOPING
        resolved = probe_wavelength(absorb_df.values, wl, doping=doping)
        self._show_resolved_wavelength(resolved)
        return resolved

    def _show_resolved_wavelength(self, value):
        """Put the automatically chosen wavelength beside the control.

        Set here, in the one place that resolves it, so the readout cannot disagree
        with what was plotted. Blank when the user typed a value -- the box shows it.
        """
        if value is None or self.analysis_wl.value() > 0:
            self.auto_wl_label.setText("")
        else:
            self.auto_wl_label.setText(f"= {value:.1f} nm")

    def _plot_kinetics(self, label, absorb_df):
        """Absorbance vs time at one wavelength, for this segment — did the step reach
        steady state, and how fast?"""
        chosen = self._chosen_wavelength(absorb_df, label)
        if chosen is None:
            seg = self.win.segments_by_label.get(label)
            self.canvas.show_message(
                "A CV sweeps back to where it started, so there is no band that\n"
                "grows across it. Type a wavelength to follow one."
                if seg is not None and seg.data_type == DATA_TYPE_CV
                else "Not enough time points for a kinetics trace.")
            return
        wl = np.asarray(absorb_df.index.values, dtype=float)
        row = int(np.abs(wl - chosen).argmin())
        t = np.asarray(absorb_df.columns.values, dtype=float)
        self.canvas.plot_series(
            t, {f"{chosen:.0f} nm": absorb_df.values[row, :]},
            "Time (s)", "Absorbance",
            title=f"{self._segment_title(label)} — kinetics")

    def _plot_modulation(self):
        """Absorbance at the END of each DOPING step, against potential — one point
        per rung, so it BUILDS during a run.

        This is the plot that earns the tab: on 2026-09-11 a film collapsed after a
        +0.8 V excursion and nothing said so until the files were analysed later, by
        which time the next run had been spent on a dead sample.

        Doping only. Dean: "they are not part of the main ladder... I am not sure even
        including them is useful." Every dedoping segment is held at the same potential,
        so they piled onto one x inside the doping curve and dragged the line back
        across it. The full both-directions comparison lives on tab 5, which plots
        dedoping against the potential it was doped TO; this one stays the glance.
        """
        xs, ys, chosen = [], [], None
        for lbl, df in self.win.results.items():
            seg = self.win.segments_by_label.get(lbl)
            if seg is None or seg.data_type != DATA_TYPE_DOPING or df is None or df.empty:
                continue
            potential = self.win.segment_potential(seg)
            if potential is None:
                continue
            if chosen is None:
                # One wavelength for the WHOLE ladder, from the first usable segment:
                # comparing modulation across potentials means a fixed probe.
                chosen = self._chosen_wavelength(df, lbl)
            if chosen is None:
                continue
            wl = np.asarray(df.index.values, dtype=float)
            row = int(np.abs(wl - chosen).argmin())
            xs.append(potential)
            ys.append(float(df.values[row, -1]))          # end of the step
        if not xs:
            self.canvas.show_message(
                "No completed doping segments yet.\n"
                "The modulation curve builds one point per doping step.")
            return
        order = np.argsort(xs)
        self.canvas.plot_series(
            np.asarray(xs)[order], {f"{chosen:.0f} nm": np.asarray(ys)[order]},
            "Potential (V)", "Absorbance at end of step",
            title="Modulation across the doping ladder")

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
                                   "No run folder yet — run a sequence or Load Run… first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def on_load_run(self):
        """Open a previously saved run folder and load its spectra + echem into the
        Results view — so past data can be reviewed without re-running a sequence.
        Reconstructs the absorbance matrices and segment map from the files on disk.
        """
        # Don't clobber a live run's in-memory results.
        if getattr(self.win.run_tab, "_worker", None) is not None:
            QMessageBox.information(self, "Run in progress",
                "A sequence is running — wait for it to finish before loading a past run.")
            return

        start = str(self.win.run_folder) if self.win.run_folder else self._default_start_dir()
        folder = QFileDialog.getExistingDirectory(self, "Open a previous run folder", start)
        if not folder:
            return

        segs = discover_run_segments(folder)
        if not segs:
            QMessageBox.warning(self, "No run data",
                "No spectra files found there.\n\n"
                "Choose a run folder containing CVspectra.txt / spectra(N).txt / etc.")
            return

        # A big folder takes seconds per segment and the window simply froze, with no
        # way to tell a slow load from a hung one. Dean asked for a progress window
        # that becomes the "X segments loaded" box.
        progress = QProgressDialog("Reading run…", "Cancel", 0, len(segs), self)
        progress.setWindowTitle("Loading run")
        progress.setWindowModality(Qt.WindowModal)
        # 250 ms, not 0: a one-segment folder loads in 0.2 s and a dialog that
        # flashes up and vanishes is worse than none. Qt only shows it if the load
        # outlasts this. MEASURED: 0.20 s per segment, 2.6 s for a 13-segment run.
        progress.setMinimumDuration(250)
        progress.setValue(0)

        def tick(done, total, name):
            progress.setLabelText(f"Reading {name}\n({done} of {total})")
            progress.setValue(done)
            QApplication.processEvents()   # modal, so this cannot re-enter the load
            return not progress.wasCanceled()

        results, segments_by_label, errors, cancelled = self._read_segments(segs, tick)
        progress.close()

        if cancelled:
            QMessageBox.information(
                self, "Load cancelled",
                f"Stopped after {len(results)} of {len(segs)} segment(s). "
                "Nothing was changed.")
            return

        if not results:
            QMessageBox.warning(self, "Could not read run",
                                "\n".join(errors) or "No readable spectra files.")
            return

        self.win.results = results
        self.win.segments_by_label = segments_by_label
        self.win.run_folder = Path(folder)
        self.win.loaded_run_settings = _read_run_settings(Path(folder))
        self.win._potential_cache.clear()
        if not self.win.loaded_run_settings:
            errors.append("no run metadata — potentials are not labelled")
        self.refresh_segments()
        self.win.analysis_tab.refresh_segments()

        msg = f"Loaded {len(results)} segment(s) from:\n{folder}"
        if errors:
            msg += "\n\nSkipped:\n" + "\n".join(errors)
        QMessageBox.information(self, "Run loaded", msg)

    def _read_segments(self, segs, on_progress=None):
        """Read every segment's absorbance, reporting progress.

        Split out of on_load_run so it can be tested without driving a modal dialog.
        `on_progress(done, total, name)` returns False to stop; a stopped load leaves
        the window untouched rather than half-populated.
        """
        results, segments_by_label, errors = {}, {}, []
        total = len(segs)
        for i, (label, data_type, run_number, path) in enumerate(segs):
            if on_progress is not None and not on_progress(i, total, path.name):
                return results, segments_by_label, errors, True
            try:
                results[label] = read_spectra_absorbance(path)
            except Exception as exc:  # noqa: BLE001 — skip a bad file, report it, keep the rest
                errors.append(f"{path.name}: {exc}")
                continue
            segments_by_label[label] = Segment(
                label=label, data_type=data_type, run_number=run_number,
                num_points=0, delta_time=0.0, trigger=False)
        if on_progress is not None:
            on_progress(total, total, "done")
        return results, segments_by_label, errors, False

    def _default_start_dir(self):
        """Where the Load Run… dialog opens — the configured data root if we can
        read it, else the dialog's default."""
        try:
            return self.win.parameters_tab._widgets["data_root"].text() or ""
        except Exception:  # noqa: BLE001 — best-effort convenience only
            return ""


def _read_run_settings(folder):
    """The settings a run was performed with, from its metadata JSON.

    Returns {} when there is none (older runs, or a folder assembled by hand). The
    caller must NOT fall back to the live Parameters tab: 20260709_P3HT_01 was run at
    0.3/0.5/0.7 V and the current defaults give 0.2/0.3/0.4, so every graph title read
    "+0.400 V" for a segment held at +0.700 V.
    """
    import json
    try:
        path = folder / f"{folder.name}_metadata.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text()).get("settings") or {}
    except Exception:  # noqa: BLE001 — a damaged metadata file must not block review
        return {}
