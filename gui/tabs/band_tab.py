"""
Tab 6 — Band Fits.

Fit the transient at EVERY wavelength in a range, for one segment or for the whole
ladder. A relaxation time that VARIES across an absorption band says the band is not
one species relaxing — which a single probe wavelength cannot show.

A tab rather than the dialog this began as, for three reasons: an all-segment fit is
tens of seconds of compute and must survive being looked away from; you want the
strip plot visible WHILE reading a single fit on tab 5; and it needs a tab's worth of
furniture. It also ends an invisible dependency — the dialog inherited tab 5's model
and window, which had to be explained in a label. This owns its own, pre-filled from
tab 5 the first time it is shown.
"""
import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel, QComboBox,
    QScrollArea,
    QCheckBox, QDoubleSpinBox, QPushButton, QTableWidget, QTableWidgetItem,
    QSplitter, QMessageBox, QFileDialog, QProgressDialog, QApplication,
)

from spec_echem.analysis import MODELS, fit_band
from spec_echem.data import DATA_TYPE_CV, DATA_TYPE_DOPING
from gui.segment_labels import prepare_segment_combo, segment_display
from gui.widgets.plot_canvas import MplCanvas
from gui.widgets.figure_dialog import open_figure_dialog, PlotToolbar
from gui.forms import form_layout

# Which tau quantities each model actually has. A single generic "tau" curve hid the
# difference: FitResult.tau is the SLOWER component for biexp, while the single-fit
# legend headlines mean tau. Reported 2026-09-30 — a single fit read 0.948 s and the
# band read 2.5 s at the same wavelength. Both right, one label doing two jobs.
BAND_CURVES = {
    "exp": [("tau", "tau")],
    "biexp": [("tau1", "tau1 (fast)"), ("tau2", "tau2 (slow)"),
              ("tau_mean", "mean tau")],
    "stretched": [("tau", "tau"), ("tau_mean", "mean tau")],
}
# Which quantity the hollow "needs review" markers sit on: drawing every curve twice
# would make the plot unreadable.
BAND_PRIMARY = {"exp": "tau", "biexp": "tau2", "stretched": "tau"}

# How wide a band the range opens with, centred on the wavelength tab 5 was using.
# A starting point the user immediately adjusts, not a claim.
DEFAULT_SPAN_NM = 100.0

# The components at one potential are NOT offset sideways. A dodge was tried and
# removed (2026-10-01): shifting tau1 and tau2 off their position made the axis read
# as a continuous potential axis, so a point drawn at +0.42 looked like a
# measurement at +0.42. The axis IS continuous now, which makes a dodge a plain
# falsehood rather than a presentational liberty. Overlap between components is the
# cheaper problem -- they are drawn in different colours, and every point is at the
# potential it was measured at.


class BandTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.win = main_window
        self._band = None          # one segment
        self._ladder = None        # [(label, potential, direction, BandFit)]
        # Which of the two is ON SCREEN. They used to be mutually exclusive --
        # fitting one segment threw the ladder away and vice versa -- so selecting a
        # segment after "Fit all segments" showed nothing new and the only way to
        # see its band was to fit it AGAIN, although Fit all had already done it.
        self._showing = None       # None | "one" | "ladder"
        self._frame = None         # the table currently shown
        self._seeded = False
        # The segment the USER chose, kept outside the combo so that
        # rebuilding it cannot forget. See _remember_segment.
        self._wanted_label = None
        # Set by whichever plot was last drawn; appended to the status line
        # by _append_sign_note. Never put on the figure.
        self._sign_note = ""
        self._build()

    # --- layout ---------------------------------------------------------

    def _build(self):
        # Scrollable body: the canvases have a minimum height, so without somewhere
        # to overflow to the WINDOW could not get shorter -- it would simply refuse.
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        scroll.setWidget(page)
        outer.addWidget(scroll)

        controls = QGroupBox("Band")
        form = form_layout(controls)

        self.segment_combo = QComboBox()
        prepare_segment_combo(self.segment_combo)
        self.segment_combo.currentIndexChanged.connect(self._remember_segment)
        self.segment_combo.currentIndexChanged.connect(self.on_segment_changed)
        form.addRow("Segment:", self.segment_combo)

        wl_row = QHBoxLayout()
        self.start_spin = self._spin(" nm", 0.0, 5000.0)
        self.stop_spin = self._spin(" nm", 0.0, 5000.0)
        wl_row.addWidget(self.start_spin)
        wl_row.addWidget(QLabel("to"))
        wl_row.addWidget(self.stop_spin)
        wl_row.addStretch()
        form.addRow("Wavelengths:", wl_row)

        # OWN model and window, not tab 5's. Sharing them means changing the model
        # there would silently invalidate a band fit sitting here.
        self.model_combo = QComboBox()
        for name in MODELS:
            self.model_combo.addItem(name, name)
        form.addRow("Model:", self.model_combo)

        win_row = QHBoxLayout()
        self.t_start = self._spin(" s", 0.0, 1e6)
        self.t_stop = self._spin(" s", 0.0, 1e6)
        win_row.addWidget(self.t_start)
        win_row.addWidget(QLabel("to"))
        win_row.addWidget(self.t_stop)
        win_row.addWidget(QLabel("(0 = whole segment)"))
        win_row.addStretch()
        form.addRow("Time window:", win_row)

        # The PLOT's potential range, not the fit's. Everything is still fitted and
        # still exported: filtering the fit would delete data from the CSV on the
        # strength of a threshold guess, and the principle here is that the software
        # raises concerns and the scientist decides.
        #
        # It exists because sub-threshold segments are not a fit-quality problem any
        # rejection rule can catch. MEASURED on a real ladder 2026-09-30: at +0.20 V
        # tau2 reached 136 s against 2-8 s above +0.40, and those points PASSED —
        # they converge with small formal errors while fitting what is essentially
        # noise. The pathology is physical, and it is not even monotonic: +0.10 was
        # tamer than +0.20, which is where the film is part-doped and a biexponential
        # can trade a real fast component against an arbitrarily slow one.
        vg_row = QHBoxLayout()
        self.vg_min = self._spin(" V", -10.0, 10.0)
        self.vg_max = self._spin(" V", -10.0, 10.0)
        self.vg_min.setValue(-10.0)
        self.vg_max.setValue(10.0)
        self.vg_min.setDecimals(2)
        self.vg_max.setDecimals(2)
        for spin in (self.vg_min, self.vg_max):
            spin.valueChanged.connect(self._redraw_ladder)
        vg_row.addWidget(self.vg_min)
        vg_row.addWidget(QLabel("to"))
        vg_row.addWidget(self.vg_max)
        self.log_check = QCheckBox("log tau axis")
        self.log_check.setToolTip(
            "A log axis shows two decades of tau at once, so a sub-threshold\n"
            "segment no longer flattens the rest onto the bottom of the plot.\n"
            "The mean +/- SD whisker is clipped where it would reach zero or below,\n"
            "and the status line says how many.")
        self.log_check.toggled.connect(self._redraw_ladder)
        vg_row.addWidget(self.log_check)
        vg_row.addStretch()
        form.addRow("Plot potentials:", vg_row)

        buttons = QHBoxLayout()
        self.fit_btn = QPushButton("Fit this segment")
        self.fit_btn.clicked.connect(self.on_fit_segment)
        self.fit_all_btn = QPushButton("Fit all segments")
        self.fit_all_btn.setToolTip(
            "The SAME wavelength range on every segment, plotted against the\n"
            "potential the film was doped to — doping and dedoping separately.")
        self.fit_all_btn.clicked.connect(self.on_fit_all)
        self.ladder_btn = QPushButton("Show all segments")
        self.ladder_btn.setToolTip(
            "Back to the tau-against-potential plot, without refitting.")
        self.ladder_btn.setEnabled(False)
        self.ladder_btn.clicked.connect(self.on_show_ladder)
        self.save_btn = QPushButton("Save CSV…")
        self.save_btn.clicked.connect(self.on_save_csv)
        self.save_btn.setEnabled(False)
        buttons.addWidget(self.fit_btn)
        buttons.addWidget(self.fit_all_btn)
        buttons.addWidget(self.ladder_btn)
        buttons.addWidget(self.save_btn)
        buttons.addStretch()
        form.addRow("", buttons)

        layout.addWidget(controls)

        self.status = QLabel("Choose a segment and a wavelength range.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        split = QSplitter(Qt.Vertical)
        plot_box = QWidget()
        plot_layout = QVBoxLayout(plot_box)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = MplCanvas(self, xlabel="Wavelength (nm)", ylabel="tau (s)")
        # Pan, zoom, HOME (unzoom), for three lines. Zoom covers the ad-hoc looking
        # that neither the potential range nor a log axis can: those are standing
        # decisions, this is "what is going on just there". Saving is NOT done from
        # here -- it goes through the preview, which renders at a chosen size rather
        # than at whatever this widget happens to be.
        self.toolbar = PlotToolbar(self.canvas, plot_box)
        self.save_fig_btn = QPushButton("Save figure…")
        self.save_fig_btn.setToolTip(
            "Preview this plot at a fixed publication size and save it,\n"
            "with the numbers behind it as a CSV.")
        self.save_fig_btn.clicked.connect(self.on_save_figure)
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas)
        save_row = QHBoxLayout()
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addStretch()
        save_row.addWidget(self.save_fig_btn)
        plot_layout.addLayout(save_row)
        split.addWidget(plot_box)
        self.table = QTableWidget(0, 0, self)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        split.addWidget(self.table)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, stretch=1)

    def _spin(self, suffix, lo, hi):
        spin = QDoubleSpinBox(self)
        spin.setRange(lo, hi)
        spin.setDecimals(1)
        spin.setSuffix(suffix)
        return spin

    # --- coming into view -----------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_segments()
        if not self._seeded:
            self.seed_from_analysis()
            self._seeded = True

    def seed_from_analysis(self, centre=None):
        """Pre-fill from tab 5, so the tab opens where you were already looking
        rather than needing to be configured from scratch."""
        tab = getattr(self.win, "analysis_tab", None)
        if tab is None:
            return
        model = tab.model_combo.currentData()
        i = self.model_combo.findData(model)
        if i >= 0:
            self.model_combo.setCurrentIndex(i)
        self.t_start.setValue(tab.start_spin.value())
        self.t_stop.setValue(tab.stop_spin.value())

        df = self._frame_for(self._current_label())
        if df is None:
            return
        wl = np.asarray(df.index.values, dtype=float)
        centre = centre or getattr(tab, "_wavelength", None) or float(np.median(wl))
        self.start_spin.setRange(float(wl.min()), float(wl.max()))
        self.stop_spin.setRange(float(wl.min()), float(wl.max()))
        self.start_spin.setValue(max(float(wl.min()), centre - DEFAULT_SPAN_NM / 2))
        self.stop_spin.setValue(min(float(wl.max()), centre + DEFAULT_SPAN_NM / 2))

    def on_save_figure(self):
        """Preview and save whatever this tab last plotted -- a single segment's
        band or the all-segment ladder, whichever is on screen."""
        # The sign-flip note travels with the PROVENANCE stamp: on when that is on,
        # absent when it is off. Shading with no caption is fine on screen, where
        # the status line explains it, but a saved figure that will be stamped
        # should carry its own explanation.
        open_figure_dialog(self, self.canvas, self.win, self._figure_basename(),
                           "Save band figure", note=self._sign_note or None)

    def _figure_basename(self):
        """Names WHICH plot it is. The two look nothing alike and answer different
        questions, so a folder holding both must not call them the same thing."""
        model = self.model_combo.currentData()
        if self._showing == "ladder":
            return f"band_ladder_{model}"
        label = (self._current_label() or "band").replace(" ", "")
        return f"{label}_band_{model}"

    def _remember_segment(self, *_):
        """Hold the chosen segment OUTSIDE the widget.

        Reading it back off the combo at refresh time is not enough: run_tab clears
        win.results at the start of a run, and showEvent refreshes on every show, so
        a single refresh against an empty result set wiped the only record of what
        was selected and every refresh afterwards landed on the first segment. The
        selection is the user's, and it should outlive the widget being rebuilt.
        """
        label = self.segment_combo.currentData()
        if label:
            self._wanted_label = label

    def refresh_segments(self):
        # A CV is excluded, not merely skipped on the ladder. Its spectra are taken
        # DURING a sweep, so "absorbance against time" there is a sweep response,
        # not a relaxation, and a tau fitted to it would be a number with no
        # meaning. Pre-dedoping stays: it is a genuine hold, and only its place on
        # the ladder is undefined.
        labels = [label for label in self.win.results
                  if not (self.win.segments_by_label.get(label) is not None
                          and self.win.segments_by_label[label].data_type
                          == DATA_TYPE_CV)]
        current = [self.segment_combo.itemData(i)
                   for i in range(self.segment_combo.count())]
        if labels == current:
            # Nothing changed, so do not rebuild. showEvent calls this on EVERY
            # show, and each rebuild re-reads every segment's echem file to label
            # it -- and gives one more chance to lose the selection.
            return

        previous = self._current_label() or self._wanted_label
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        for label in labels:
            self.segment_combo.addItem(segment_display(self.win, label), label)
        if previous:
            i = self.segment_combo.findData(previous)
            if i >= 0:
                self.segment_combo.setCurrentIndex(i)
            # Kept even when it is not there: a run in progress has not written
            # that segment YET, and it should come back when it does.
        self.segment_combo.blockSignals(False)

    def _current_label(self):
        return self.segment_combo.currentData()

    def _frame_for(self, label):
        df = self.win.results.get(label) if label else None
        return None if df is None or df.empty else df

    def _window(self):
        """0 means 'the whole segment' at both ends, matching tab 5."""
        start = self.t_start.value() or None
        stop = self.t_stop.value() or None
        return start, stop

    # --- fitting --------------------------------------------------------

    def reset_for_new_run(self):
        """Forget everything the PREVIOUS run put here.

        Nothing cleared this tab -- not loading a run, not starting one -- so a band
        or a ladder outlived the data it was fitted to. Latent until selecting a
        segment began showing its stored fit, which turned it into the previous
        run's result appearing under this run's segment name.
        """
        self._band = None
        self._ladder = None
        self._showing = None
        self._wanted_label = None
        self._sign_note = ""
        self._excluded = 0
        self._offscreen = 0
        self._clipped = 0
        self.ladder_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.status.setText("Choose a segment and a wavelength range.")
        self.canvas.show_message("Fit a segment to see tau across the band.")

    def on_segment_changed(self, *_):
        """Show the newly chosen segment's band if it has already been fitted.

        After "Fit all segments" every segment HAS a fit -- they are all in
        self._ladder -- so selecting one should show it rather than leaving the
        ladder up and asking for a refit that would repeat work already done.
        """
        label = self._current_label()
        if not label or not self._ladder:
            return
        if not self._show_stored(label):
            # Fitted segments are on screen one at a time; a segment with no fit of
            # its own leaves the ladder up rather than blanking the plot.
            return

    def _show_stored(self, label):
        """Draw a segment's band from the ladder, without refitting. True if shown."""
        entry = next((e for e in (self._ladder or []) if e[0] == label), None)
        if entry is None:
            return False
        self._band = entry[3]
        self._showing = "one"
        self._draw_one(label)
        self._fill_table(self._band.table())
        self.status.setText(
            f"{label}:  " + self._summary_text(self._band.summary())
            + "   — from Fit all segments; Show all segments goes back")
        self._append_sign_note()
        self.ladder_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        return True

    def on_show_ladder(self):
        """Back to the ladder, from fits already in hand."""
        if not self._ladder:
            return
        self._band = None
        self._showing = "ladder"
        self._fill_table(self._ladder_frame())
        self._draw_ladder()

    def on_fit_segment(self):
        label = self._current_label()
        df = self._frame_for(label)
        if df is None:
            QMessageBox.information(self, "No segment",
                                    "Choose a segment with data first.")
            return
        t = np.asarray(df.columns.values, dtype=float)
        start, stop = self._window()
        try:
            self._band = fit_band(df.values,
                                  np.asarray(df.index.values, dtype=float), t,
                                  self.start_spin.value(), self.stop_spin.value(),
                                  model=self.model_combo.currentData(),
                                  t_start=start, t_stop=stop)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._showing = "one"
        self._draw_one(label)
        self._fill_table(self._band.table())
        self.status.setText(f"{label}:  " + self._summary_text(self._band.summary()))
        self._append_sign_note()

    def on_fit_all(self):
        """The SAME range on every segment. Cancellable: biexp across a hundred
        wavelengths and a dozen segments is tens of seconds."""
        labels = [self.segment_combo.itemData(i)
                  for i in range(self.segment_combo.count())]
        if not labels:
            QMessageBox.information(self, "No segments", "Load or run a sequence first.")
            return
        model = self.model_combo.currentData()
        start, stop = self._window()

        # Partitioned BEFORE the dialog opens, not skipped inside the loop. A
        # pre-dedope has no rung so it is never fitted here, and counting it made
        # the dialog promise "1 of 17" while doing 16 -- and consume a tick of the
        # bar doing nothing. The count now describes the work.
        fittable, skipped = [], []
        for label in labels:
            seg = self.win.segments_by_label.get(label)
            potential = self.win.doped_to(seg) if seg is not None else None
            # No potential means no place on the ladder -- a CV sweeps and
            # pre-dedoping is a single baseline, not a rung.
            if self._frame_for(label) is None or seg is None or potential is None:
                skipped.append(label)
            else:
                fittable.append((label, seg, float(potential)))

        progress = QProgressDialog("Fitting bands…", "Cancel", 0, len(fittable), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        results = []
        for i, (label, seg, potential) in enumerate(fittable):
            progress.setLabelText(f"Fitting {label} ({i + 1} of {len(fittable)})…")
            progress.setValue(i)
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            df = self._frame_for(label)
            try:
                band = fit_band(df.values, np.asarray(df.index.values, dtype=float),
                                np.asarray(df.columns.values, dtype=float),
                                self.start_spin.value(), self.stop_spin.value(),
                                model=model, t_start=start, t_stop=stop)
            except ValueError as exc:
                self.status.setText(str(exc))
                progress.close()
                return
            direction = "doping" if seg.data_type == DATA_TYPE_DOPING else "dedoping"
            results.append((label, potential, direction, band))
        progress.setValue(len(fittable))

        if not results:
            self.status.setText(
                "No segment could be placed on the ladder. "
                + (f"Skipped: {', '.join(skipped)}." if skipped else ""))
            return
        self._ladder = results
        self._band = None
        self._showing = "ladder"
        self.ladder_btn.setEnabled(True)
        self._fill_table(self._ladder_frame())
        cancelled = progress.wasCanceled()
        parts = [f"{len(results)} segment(s) fitted"]
        if cancelled:
            parts.append("CANCELLED — the rest were not fitted")
        if skipped:
            # Named, not silently dropped: a CV and a pre-dedope have no rung.
            parts.append(f"no ladder potential for {', '.join(skipped)}")
        total = {"n": 0, "ok": 0, "no_convergence": 0, "low_snr": 0, "converged": 0}
        for _l, _p, _d, band in results:
            for key, value in band.summary().items():
                total[key] += value
        parts.append(self._summary_text(total))
        self._base_status = ".  ".join(parts)
        self.status.setText(self._base_status)
        self._draw_ladder()

    @staticmethod
    def _summary_text(s):
        parts = [f"{s['ok']} of {s['n']} wavelengths fitted"]
        if s["no_convergence"]:
            parts.append(f"{s['no_convergence']} did not converge")
        if s["converged"] - s["ok"]:
            parts.append(f"{s['converged'] - s['ok']} need review")
        if s["low_snr"]:
            parts.append(f"{s['low_snr']} below the noise threshold "
                         f"(fitted anyway, flagged in the table)")
        return ", ".join(parts)

    # --- plots ----------------------------------------------------------

    def _curves_for(self, model):
        return BAND_CURVES.get(model, BAND_CURVES["exp"])

    # Amber, as the "needs review" edge and text already are elsewhere, at a tint
    # that reads as a region rather than competing with the markers on top of it.
    SIGN_SHADE = "#e07b00"
    SIGN_ALPHA = 0.12

    @staticmethod
    def _sign_runs(wl, flagged):
        """Contiguous [lo, hi] wavelength runs where the prefactors differ in sign.

        Runs rather than per-point marks: a competing process occupies a BAND, and
        100 individual glyphs would say the same thing less clearly.
        """
        runs, start = [], None
        for i, bad in enumerate(list(flagged) + [False]):
            if bad and start is None:
                start = i
            elif not bad and start is not None:
                lo, hi = wl[start], wl[i - 1]
                half = (wl[1] - wl[0]) / 2.0 if len(wl) > 1 else 0.5
                runs.append((lo - half, hi + half))
                start = None
        return runs

    SIGN_NOTE = ("shaded: the two prefactors differ in SIGN there — competing "
                 "processes (e.g. a bipolaron taking absorbance from the polaron), "
                 "or a poor fit")

    def _draw_one(self, label):
        """tau against wavelength for a single segment."""
        frame = self._band.table()
        wl = frame["wavelength_nm"].to_numpy()
        ok = frame["ok"].to_numpy(dtype=bool)
        model = self._band.model

        series = []
        for column, name in self._curves_for(model):
            if column not in frame:
                continue
            y = frame[column].to_numpy(dtype=float)
            good = ok & np.isfinite(y)
            if np.any(good):
                series.append((wl[good], y[good], name,
                               {"marker": "o", "markersize": 3.5}))
        primary = BAND_PRIMARY.get(model, "tau")
        if primary in frame:
            y = frame[primary].to_numpy(dtype=float)
            bad = (~ok) & np.isfinite(y)
            if np.any(bad):
                series.append((wl[bad], y[bad], "needs review",
                               {"marker": "o", "markersize": 4, "linestyle": "none",
                                "markerfacecolor": "none"}))
        if not series:
            self.canvas.show_message("No wavelength in this band produced a fit.")
            return
        flagged = (frame["mixed_signs"].to_numpy(dtype=bool)
                   if "mixed_signs" in frame else np.zeros(len(frame), dtype=bool))
        runs = self._sign_runs(wl, ok & flagged)
        self._sign_note = ""
        if runs:
            spans = ", ".join(f"{lo:.0f}-{hi:.0f} nm" for lo, hi in runs)
            self._sign_note = (f"{self.SIGN_NOTE}: {int((ok & flagged).sum())} of "
                               f"{int(ok.sum())} fitted wavelengths, {spans}")
        self.canvas.plot_multi_xy(
            series, "Wavelength (nm)", "tau (s)",
            title=f"{label} — tau vs wavelength  ({model}, {self._window_text()})")
        # The shading STAYS here: on this plot it is spatial, showing WHERE in the
        # band the competition is, which is the question the plot answers. Its
        # explanation lives below the graph rather than on the figure.
        for lo, hi in runs:
            self.canvas.ax.axvspan(lo, hi, color=self.SIGN_SHADE,
                                   alpha=self.SIGN_ALPHA, zorder=0, lw=0)
        if model == "stretched" and "beta" in frame:
            self._beta_twin(wl, frame["beta"].to_numpy(dtype=float), ok)
        # Registered as ONE composition: the beta axis is added after the recorded
        # call, so exporting the recorded call alone would drop it.
        self.canvas.record_draw(lambda: self._draw_one(label),
                                lambda: self._band.table() if self._band else None)

    def _beta_twin(self, x, beta, ok):
        """beta on its own right axis: dimensionless and 0-1, so sharing the tau
        axis would flatten it or push tau off the top. fig.clear() drops the twin on
        each redraw, so they cannot accumulate."""
        good = ok & np.isfinite(beta)
        if not np.any(good):
            return
        ax2 = self.canvas.ax.twinx()
        ax2.plot(np.asarray(x)[good], beta[good], color="#2ca02c", lw=1.0,
                 marker="s", markersize=3)
        ax2.set_ylabel("beta (stretch exponent)", color="#2ca02c")
        ax2.tick_params(axis="y", labelcolor="#2ca02c")
        ax2.set_ylim(0, 1.05)
        self.canvas.draw_idle()

    def _fit_conditions(self, band):
        """'740-860 nm, 0.0-20.0 s' -- read from the FIT, not from the controls.

        The spin boxes can be changed after a fit, and a title built from them would
        then describe a band nobody fitted. BandFit records its own range, so the
        plot can only say what was actually done.
        """
        parts = [f"{band.wl_start:.0f}-{band.wl_stop:.0f} nm"]
        if band.t_first is not None or band.t_last is not None:
            first = "start" if band.t_first is None else f"{band.t_first:g}"
            last = "end" if band.t_last is None else f"{band.t_last:g}"
            parts.append(f"{first}-{last} s")
        return ", ".join(parts)

    def _draw_ladder(self):
        """Every tau in the band, at each potential, doping and dedoping separately.

        LINEAR x, in volts. It was categorical at first -- evenly spaced rungs with
        the potential on the tick -- which reads well for an even ladder but says
        something false about an uneven one, and is not how the downstream analysis
        plots tau against Vg. Points therefore sit at their actual potential, and a
        0.05 V step looks like half a 0.1 V step because it is one.

        Ticks are still placed AT the measured potentials rather than left to
        matplotlib: every rung is then labelled with the value it was held at, which
        is the one thing the categorical version did better.

        The whisker is mean +/- SD over the PASSED fits only. Rejected ones are drawn
        hollow so they are visible but do not move the statistic, and the axis is
        scaled to the passed fits -- a single bad fit can return tau = 1e4 s and would
        otherwise flatten every real point onto the bottom of the plot. Any rejected
        point outside the axes is COUNTED in the status line rather than silently
        dropped.
        """
        model = self._ladder[0][3].model
        conditions = self._fit_conditions(self._ladder[0][3])
        beta_row = model == "stretched"
        lo, hi = self.vg_min.value(), self.vg_max.value()
        shown = [r for r in self._ladder if lo <= r[1] <= hi]
        self._excluded = len(self._ladder) - len(shown)
        if not shown:
            self.canvas.show_message(
                f"No segment between {lo:+.2f} and {hi:+.2f} V. "
                f"All {len(self._ladder)} are outside that range.")
            return
        directions = [d for d in ("doping", "dedoping")
                      if any(r[2] == d for r in shown)]

        fig = self.canvas.fig
        fig.clear()
        self.canvas._live_line = None
        rows = 2 if beta_row else 1
        axes = fig.subplots(rows, len(directions), squeeze=False)
        self.canvas.ax = axes[0][0]

        offscreen = 0
        self._clipped = 0
        sign_notes = []
        for col, direction in enumerate(directions):
            entries = sorted((r for r in shown if r[2] == direction),
                             key=lambda r: r[1])
            xs = np.array([p for _l, p, _d, _b in entries], dtype=float)
            labels = [f"{p:+.2f}" for p in xs]

            ax = axes[0][col]
            # BEFORE _strip, which sets the limits: a log axis cannot take a lower
            # bound of zero or below, and setting linear limits first left
            # matplotlib to pick its own — every point squashed against the top.
            if self.log_check.isChecked():
                ax.set_yscale("log")
            offscreen += self._strip(ax, entries, xs, model)
            sign_notes += self._shade_sign_flips(ax, entries, xs, direction)
            ax.set_xticks(xs)
            ax.set_xticklabels(labels)
            # A real axis puts the end rungs on the frame, where half of each strip
            # would be clipped. One tenth of the span, or 50 mV for a single rung.
            span = float(xs.max() - xs.min())
            pad = span * 0.1 if span > 0 else 0.05
            ax.set_xlim(float(xs.min()) - pad, float(xs.max()) + pad)
            # The band is the whole premise of the plot and was nowhere on it: a
            # tau ladder means nothing without the wavelengths it was fitted over.
            # Per panel rather than as one suptitle, so a figure cropped to one
            # direction still carries its own conditions.
            ax.set_title(f"{direction} — {model}\n{conditions}", fontsize="medium")
            ax.set_ylabel("tau (s)")
            ax.grid(alpha=0.3)
            if col == 0 and len(self._curves_for(model)) > 1:
                # Only when something was actually labelled: every fit in the
                # panel can fail, and asking then is just a warning on the console.
                if ax.get_legend_handles_labels()[1]:
                    ax.legend(fontsize=7, loc="best", framealpha=0.9)

            if beta_row:
                bax = axes[1][col]
                self._strip(bax, entries, xs, model, column="beta")
                bax.set_xticks(xs)
                bax.set_xticklabels(labels)
                bax.set_ylabel("beta")
                bax.set_ylim(0, 1.05)
                bax.grid(alpha=0.3)
                bax.set_xlabel("potential the film was doped to (V)")
            else:
                ax.set_xlabel("potential the film was doped to (V)")

        fig.tight_layout()
        # Below the graph, never ON it: this is a prompt to the scientist to go and
        # look, not a caption the saved figure should carry (asked for directly).
        self._sign_note = (f"{self.SIGN_NOTE}: " + ";  ".join(sign_notes)
                           if sign_notes else "")
        self.canvas.draw_idle()
        # This plot is composed on the figure directly -- its own subplots, strips
        # and whiskers -- so no draw method recorded it and Save figure wrote
        # the SINGLE-segment plot that came before it, under the ladder's name
        # (reported 2026-10-01). Registering the composition fixes both halves.
        self.canvas.record_draw(self._draw_ladder, self._ladder_frame)
        self._offscreen = offscreen
        self._note_plot_limits()

    def _append_sign_note(self):
        """Put the competing-processes note BELOW the graph.

        Not on the figure: a saved plot should not carry it (asked for directly),
        and this is a prompt to go and look rather than a caption.
        """
        if self._sign_note:
            self.status.setText(self.status.text().rstrip(".")
                                + ".  " + self._sign_note + ".")

    def _note_plot_limits(self):
        """Say what the plot is not showing. A point removed by a range or pushed
        off a rescaled axis is still a measurement; silence about it is how a
        trimmed plot starts being read as the whole dataset."""
        notes = []
        if getattr(self, "_excluded", 0):
            notes.append(f"{self._excluded} segment(s) outside the plotted "
                         f"potential range (still fitted, still in the CSV)")
        if getattr(self, "_offscreen", 0):
            notes.append(f"{self._offscreen} rejected point(s) outside the axes")
        if getattr(self, "_clipped", 0):
            notes.append(f"{self._clipped} whisker(s) clipped at the log axis floor")
        if notes:
            self.status.setText(self.status.text().rstrip(".")
                                + ".  Not shown: " + "; ".join(notes) + ".")
        self._append_sign_note()

    def _redraw_ladder(self):
        """The range and the axis scale change the VIEW, so redraw without refitting.
        Nothing is recomputed — the fits are unchanged."""
        if self._ladder:
            # Reset BEFORE drawing: _draw_ladder appends its "not shown" notes to
            # whatever is there, so resetting afterwards would erase them.
            self.status.setText(getattr(self, "_base_status", ""))
            self._draw_ladder()

    def _shade_sign_flips(self, ax, entries, xs, direction):
        """Count, per rung, how many fitted wavelengths have opposite-sign
        prefactors. Returns one note per rung that has any.

        NOT shaded. Shading reads as spatial, and on a plot whose x is potential it
        was only a distracting backdrop (2026-10-01) -- unlike the single-segment
        plot, where it marks WHERE in the band the competition is. The counts go
        below the graph instead, to prompt a look rather than decorate the figure.
        """
        notes = []
        for j, (_lbl, _pot, _dir, band) in enumerate(entries):
            frame = band.table()
            if "mixed_signs" not in frame:
                continue
            ok = frame["ok"].to_numpy(dtype=bool)
            flagged = frame["mixed_signs"].to_numpy(dtype=bool) & ok
            if not ok.any() or not flagged.any():
                continue
            notes.append(f"{direction} {xs[j]:+.2f} V: {int(flagged.sum())}/"
                         f"{int(ok.sum())}")
        return notes

    def _strip(self, ax, entries, xs, model, column=None):
        """One potential's worth of taus as a vertical strip, with mean +/- SD.
        Returns how many rejected points fell outside the drawn range."""
        columns = ([(column, column)] if column
                   else self._curves_for(model))
        offscreen = 0
        # Which series have had their label used. It used to be "the first segment
        # carries the labels", which puts nothing in the legend when that segment
        # happens to have no passing fits -- matplotlib then warns "No artists with
        # labels found to put in legend" and the panel comes out unlabelled, which
        # is worse than the warning (2026-10-01).
        labelled = set()
        for k, (name, label) in enumerate(columns):
            for j, (_lbl, _pot, _dir, band) in enumerate(entries):
                frame = band.table()
                if name not in frame:
                    continue
                ok = frame["ok"].to_numpy(dtype=bool)
                y = frame[name].to_numpy(dtype=float)
                good = ok & np.isfinite(y)
                x = xs[j]
                if np.any(good):
                    ax.plot(np.full(good.sum(), x), y[good], "o", markersize=2.5,
                            alpha=0.45, color=f"C{k}",
                            label=None if name in labelled else label)
                    labelled.add(name)
                    mean, sd = float(np.mean(y[good])), float(np.std(y[good]))
                    lower = sd
                    if self.log_check.isChecked() and mean - sd <= 0:
                        # A log axis cannot draw a whisker reaching zero. Clip it to
                        # just above the floor and COUNT it, rather than dropping the
                        # arm silently and showing a tighter spread than there is.
                        lower = mean * 0.999
                        self._clipped += 1
                    ax.errorbar(x, mean, yerr=[[lower], [sd]], fmt="_",
                                color=f"C{k}", markersize=14, capsize=4, lw=1.6,
                                zorder=3)
                bad = (~ok) & np.isfinite(y)
                if np.any(bad):
                    ax.plot(np.full(bad.sum(), x), y[bad], "o", markersize=3.5,
                            markerfacecolor="none", color=f"C{k}", alpha=0.7)
        # Scale to the PASSED fits, then count what that leaves outside.
        if not column:
            passed = np.concatenate(
                [band.table()[name].to_numpy(dtype=float)[
                    band.table()["ok"].to_numpy(dtype=bool)]
                 for name, _l in columns for _a, _b, _c, band in entries
                 if name in band.table()] or [np.array([np.nan])])
            passed = passed[np.isfinite(passed)]
            if ax.get_yscale() == "log":
                # Multiplicative padding, and only over POSITIVE values: on a log
                # axis "min - 10%" is meaningless and a non-positive bound is
                # rejected outright.
                passed = passed[passed > 0]
            if passed.size:
                if ax.get_yscale() == "log":
                    lo, hi = passed.min() / 2.0, passed.max() * 2.0
                else:
                    pad = 0.1 * (passed.max() - passed.min()
                                 or abs(passed.max()) or 1.0)
                    lo, hi = passed.min() - pad, passed.max() + pad
                ax.set_ylim(lo, hi)
                for _lbl, _pot, _dir, band in entries:
                    frame = band.table()
                    for name, _l in columns:
                        if name not in frame:
                            continue
                        y = frame[name].to_numpy(dtype=float)
                        bad = (~frame["ok"].to_numpy(dtype=bool)) & np.isfinite(y)
                        offscreen += int(np.sum((y[bad] < lo) | (y[bad] > hi)))
        return offscreen

    def _window_text(self):
        start, stop = self._window()
        if start is None and stop is None:
            return "whole segment"
        return f"{start if start is not None else 'start'}–" \
               f"{stop if stop is not None else 'end'} s"

    # --- table and export -----------------------------------------------

    def _ladder_frame(self):
        """Long format: one row per (segment, wavelength). The most useful artifact
        of a 2-D result — every view in this tab, and several we have not built, can
        be drawn from this one file elsewhere."""
        import pandas as pd
        frames = []
        for label, potential, direction, band in self._ladder:
            frame = band.table()
            frame.insert(0, "direction", direction)
            frame.insert(0, "doped_to_V", potential)
            frame.insert(0, "segment", label)
            frames.append(frame)
        return pd.concat(frames, ignore_index=True)

    def _fill_table(self, frame):
        self._frame = frame
        self.table.setColumnCount(len(frame.columns))
        self.table.setHorizontalHeaderLabels([str(c) for c in frame.columns])
        self.table.setRowCount(len(frame))
        for r in range(len(frame)):
            for c in range(len(frame.columns)):
                value = frame.iloc[r, c]
                text = ("" if isinstance(value, float) and np.isnan(value)
                        else f"{value:.6g}" if isinstance(value, float)
                        else str(value))
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.save_btn.setEnabled(True)

    def on_save_csv(self):
        if self._frame is None:
            return
        base = ("band_all_segments" if self._ladder
                else f"{self._current_label()}_band")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save band fits", f"{base}.csv", "CSV (*.csv)")
        if not path:
            return
        self._frame.to_csv(path, index=False)
        QMessageBox.information(self, "Saved", f"Written to:\n\n{path}")
