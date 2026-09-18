"""
Tab 4 — Results.

Segment selector, wavelength range, absorbance plot (updates after each segment
completes — no live updating), and data-folder actions. The matplotlib canvas
is wired together with the Instrument-tab preview in the plotting increment.
"""
from pathlib import Path

import textwrap

import numpy as np

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton, QFileDialog, QSplitter, QMessageBox,
    QProgressDialog, QApplication, QCheckBox,
)

from spec_echem.analysis import (cv_probe_wavelength, probe_wavelength, density_of_states,
                                 scan_rate_from_sweep, fit_gaussian_dos,
                                 fit_exponential_tail, dos_equilibrium_check)
from spec_echem.data import (
    echem_txt_path, read_spectra_absorbance, discover_run_segments, DATA_TYPE_CV,
    DATA_TYPE_DOPING, segment_potential_text, segment_potential,
)
from spec_echem.experiment import Segment
from spec_echem.gamry_data import (read_cv, read_chrono, POTENTIAL_COL,
                                   CURRENT_COL)
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
        # This forces the list-view popup, which honors it and scrolls beyond it.
        self.segment_combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
        # Index, not text: the visible text carries the potential, so it is not the
        # key into win.results -- itemData is.
        self.segment_combo.currentIndexChanged.connect(self.on_segment_changed)
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
        # Requested: another option here rather than a new tab -- a DOS is another view of
        # the CV that is already selected, so it needs no new navigation.
        # Under development: the method is sound but not yet validated on a
        # quasi-equilibrium CV (scan-rate series pending), and has no capacitive
        # baseline subtraction. The label says so wherever the plot can be seen.
        # Kept short: a QComboBox sizes to its longest item, and the long form set a
        # minimum window width on the Win11 rig.
        self.view_combo.addItem("Density of states (CV) — in dev.", "dos")
        self.view_combo.setToolTip(
            "Modulation is the one to watch while a run is going: absorbance at the\n"
            "end of each step, against potential. A film that stops modulating has\n"
            "stopped being worth the rest of the ladder.")
        self.view_combo.currentIndexChanged.connect(self.on_segment_changed)
        # The box always shows the wavelength in use, as a number. "auto" is a
        # separate checkbox because it is a STATE, not a value: it resolves per
        # segment (the polaron grows on doping and decays on dedoping), so the
        # number changes as you step through a run while auto stays on.
        self.analysis_wl = QDoubleSpinBox()
        self.analysis_wl.setRange(0.0, 5000.0)
        self.analysis_wl.setDecimals(1)
        self.analysis_wl.setSuffix(" nm")
        self.analysis_wl.setValue(0.0)
        self.analysis_wl.setToolTip(
            "The wavelength followed. Type a value, or click the spectrum, to follow\n"
            "a band of your choosing -- that turns auto off.")
        self.analysis_wl.valueChanged.connect(self._on_wl_edited)
        self.wl_auto = QCheckBox("auto")
        self.wl_auto.setChecked(True)
        self.wl_auto.setToolTip(
            "Follow the polaron band for each segment: the band that GROWS on\n"
            "doping, the one that DECAYS on dedoping, and on a CV the one that grows\n"
            "most by the most-doped point of the sweep.")
        self.wl_auto.toggled.connect(self.on_segment_changed)
        view_row.addWidget(self.view_combo)
        self.at_label = QLabel("at")
        view_row.addWidget(self.at_label)
        view_row.addWidget(self.analysis_wl)
        view_row.addWidget(self.wl_auto)

        # DOS-only controls. Outside the doping range the current is double-layer
        # charging, not the distribution being measured -- on one CV, 42% of every
        # curve sat below 0 V. Both directions get the SAME window so they stay
        # comparable.
        self.dos_widgets = []
        self.dos_vmin = QDoubleSpinBox()
        self.dos_vmin.setRange(-10.0, 10.0)
        self.dos_vmin.setDecimals(3)
        self.dos_vmin.setSingleStep(0.05)
        self.dos_vmin.setSuffix(" V")
        self.dos_vmin.setValue(-0.5)
        self.dos_vmin.setToolTip(
            "Lowest potential included — applied to BOTH directions, so the two stay\n"
            "comparable. Below the doping onset the current is capacitive, but cutting\n"
            "at 0 V truncates the reducing distribution while leaving the oxidizing one\n"
            "untouched, so the default reaches into the dedoping region equally.")
        self.dos_vmax = QDoubleSpinBox()
        self.dos_vmax.setRange(-10.0, 10.0)
        self.dos_vmax.setDecimals(3)
        self.dos_vmax.setSingleStep(0.05)
        self.dos_vmax.setSuffix(" V")
        # Filled with the CV's own maximum when it is plotted -- the number, not a
        # placeholder: "sweep max" said nothing a reader could check. Refilled for
        # each new CV unless the user has changed it (see _seed_dos_vmax).
        self.dos_vmax.setValue(0.7)
        self._dos_vmax_seeded = None
        self.dos_energy_y = QCheckBox("energy on Y")
        self.dos_energy_y.setToolTip(
            "Energy vertical, DOS horizontal — the solid-state convention, for\n"
            "reading the DOS against a band or energy-level diagram.\n"
            "Unchecked gives energy horizontal, as the electrochemical DOS papers\n"
            "plot it. Same data either way; it is a transpose.")
        for widget, label in ((QLabel("  DOS range:"), None), (self.dos_vmin, None),
                              (QLabel("to"), None), (self.dos_vmax, None),
                              (self.dos_energy_y, None)):
            view_row.addWidget(widget)
            self.dos_widgets.append(widget)
        for w in (self.dos_vmin, self.dos_vmax):
            w.valueChanged.connect(self.on_segment_changed)
        self.dos_energy_y.toggled.connect(self.on_segment_changed)
        view_row.addStretch()
        ctrl_form.addRow("Optical view:", view_row)
        self._sync_view_controls()

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
        current = self._current_label()
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        # Shown with its potential, as on the Analysis tab -- requested: the bare
        # "Dedoping 4" said nothing about which step it was.
        for label in self.win.results:
            self.segment_combo.addItem(self._segment_title(label), label)
        i = self.segment_combo.findData(current) if current else -1
        if i >= 0:
            self.segment_combo.setCurrentIndex(i)
        self.segment_combo.blockSignals(False)
        self.on_segment_changed()

    def _current_label(self):
        """The segment's real label -- the key into win.results, not the text shown."""
        return self.segment_combo.currentData()

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

    def _sync_view_controls(self):
        """Show only the controls the selected view uses.

        Separate from on_segment_changed, and run BEFORE its no-data early return:
        inside it, nothing was hidden until a run was loaded, so the DOS controls sat
        beside the spectra view on an empty tab.
        """
        view = self.view_combo.currentData() if hasattr(self, "view_combo") else "spectra"
        for widget in getattr(self, "dos_widgets", []):
            widget.setVisible(view == "dos")
        # The wavelength does nothing to a DOS, which comes from the current alone.
        # Showing it there implied otherwise, and cost width the DOS controls need.
        for widget in (getattr(self, "at_label", None), getattr(self, "analysis_wl", None),
                       getattr(self, "wl_auto", None)):
            if widget is not None:
                widget.setVisible(view != "dos")
        return view

    def on_segment_changed(self, *_):
        view = self._sync_view_controls()
        label = self._current_label()
        if not label or label not in self.win.results:
            return
        absorb_df = self.win.results[label]
        if view == "kinetics":
            self._plot_kinetics(label, absorb_df)
        elif view == "modulation":
            self._plot_modulation()
        elif view == "dos":
            self._plot_dos(label, absorb_df)
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
        wavelength = round(float(event.xdata), 1)
        # setValue re-runs on_segment_changed, which redraws with the marker moved.
        self.analysis_wl.setValue(wavelength)

        # Carry it to the Analysis tab. Requested: "if the vertical line has been clicked
        # / selected in tab 4, then that WL should be used instead of Auto(polaron)
        # as there was likely some intention of the user on that WL." A click is a
        # deliberate choice of band; leaving tab 5 on automatic would quietly fit
        # somewhere else. Typing in the box is NOT propagated -- that is often just
        # reading a value off the spectrum.
        self.win.analysis_tab.wavelength_spin.setValue(wavelength)

    def _chosen_wavelength(self, absorb_df, label):
        """The wavelength to follow: the user's, or the polaron band.

        Automatic goes through analysis.probe_wavelength, which knows that the polaron
        GROWS on doping but DECAYS on dedoping. This used to take the growing band
        unconditionally, so every dedoping segment followed pi-pi* while the control
        said "auto (polaron)".
        """
        wl = np.asarray(absorb_df.index.values, dtype=float)
        if not self.wl_auto.isChecked():
            requested = self.analysis_wl.value()
            return float(wl[int(np.abs(wl - requested).argmin())])

        seg = self.win.segments_by_label.get(label)
        if seg is not None and seg.data_type == DATA_TYPE_CV:
            # A CV returns to where it started, so end-minus-start sees only drift.
            # Compared against the most-doped spectrum instead: 799 nm on the
            # 20250710 reference run. This used to return nothing, leaving the
            # kinetics view blank until a wavelength was typed.
            resolved = cv_probe_wavelength(absorb_df.values, wl)
        else:
            doping = seg is None or seg.data_type == DATA_TYPE_DOPING
            resolved = probe_wavelength(absorb_df.values, wl, doping=doping)
        # Shown in the box itself, set in the one place that resolves it so the
        # number cannot disagree with what was plotted. Signals blocked: this is
        # the program writing, not the user choosing, so auto must stay on.
        if resolved is not None:
            self.analysis_wl.blockSignals(True)
            self.analysis_wl.setValue(float(resolved))
            self.analysis_wl.blockSignals(False)
        return resolved

    def _on_wl_edited(self, *_):
        """The user typed a wavelength: that is a choice, so auto goes off."""
        self.wl_auto.blockSignals(True)
        self.wl_auto.setChecked(False)
        self.wl_auto.blockSignals(False)
        self.on_segment_changed()

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
        +0.8 V excursion and nothing said so until the files were analyzed later, by
        which time the next run had been spent on a dead sample.

        Doping only. Requested: "they are not part of the main ladder... I am not sure even
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

    def _plot_dos(self, label, absorb_df=None):
        """Density of states from the CV — g(E) = i / (v·e·V_film).

        Last cycle only, forward and reverse as SEPARATE curves: the film is not the
        same on cycle 1 as on cycle 3, and hysteresis between the directions is a real
        effect that averaging would hide.

        NOT subtracted: the capacitive baseline. Double-layer charging is not density
        of states, but whatever is removed changes the answer, so it stays visible
        until there is a decision about how to remove it (docs/analysis-design.md).
        """
        seg = self.win.segments_by_label.get(label)
        if seg is None or seg.data_type != DATA_TYPE_CV:
            self.canvas.show_message(
                "Density of states is computed from a CV sweep.\n"
                "Select the CV segment.")
            return
        path = echem_txt_path(self.win.run_folder, seg.data_type, seg.run_number) \
            if self.win.run_folder else None
        if path is None or not path.exists():
            self.canvas.show_message("No echem file for the CV — nothing to compute.")
            return

        try:
            df = read_cv(path)
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the tab
            self.canvas.show_message(f"Could not read the CV:\n{exc}")
            return

        # MEASURED first, nominal second -- the same rule as segment potentials. The
        # CV file has no time column, but its spectra file does, and total path swept
        # over elapsed time is the rate. On the reference run that gives 98.4 mV/s
        # against a nominal 100, and it works for runs with no metadata at all.
        times = np.asarray(absorb_df.columns.values, dtype=float) \
            if absorb_df is not None and not absorb_df.empty else None
        duration = float(times.max() - times.min()) if times is not None and times.size \
            else None
        rate = scan_rate_from_sweep(df[POTENTIAL_COL].to_numpy(float), duration)
        source = "measured"
        if rate is None:
            rate_mv = self.win.label_settings().get("cv_scan_rate")
            if not rate_mv:
                self.canvas.show_message(
                    "No scan rate: the CV spectra carry no times and the run has no "
                    "metadata.\nEnter a CV scan rate on the Parameters tab.")
                return
            # cv_scan_rate is in mV/s; the formula needs V/s. Getting this wrong
            # scales the whole answer by 1000.
            rate, source = float(rate_mv) / 1000.0, "nominal"

        # Film geometry is the one thing the form MAY supply for a loaded run. Segment
        # potentials must never come from it -- they are recorded per-run and taking
        # them from the form mislabeled +0.700 V as +0.400 V. Geometry is different:
        # it is recorded nowhere in older data, so the form is the only place it can
        # come from, and the title says when it did.
        settings = self.win.label_settings()
        live = self.win.settings
        thickness_nm = settings.get("film_thickness_nm") or live.get("film_thickness_nm") or 0.0
        area_cm2 = settings.get("film_area_cm2") or live.get("film_area_cm2") or 0.0
        from_form = not settings.get("film_area_cm2") and bool(area_cm2)
        volume = (area_cm2 * thickness_nm * 1e-7) if (area_cm2 and thickness_nm) else None

        try:
            curves = density_of_states(df[POTENTIAL_COL].to_numpy(float),
                                       df[CURRENT_COL].to_numpy(float),
                                       rate, volume_cm3=volume,
                                       v_min=self.dos_vmin.value(),
                                       v_max=self._seed_dos_vmax(
                                           df[POTENTIAL_COL].to_numpy(float)))
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the tab
            self.canvas.show_message(f"Could not compute a DOS:\n{exc}")
            return
        if not curves:
            self.canvas.show_message("The CV has no complete sweep to use.")
            return

        units = curves[0]["units"]
        # A Gaussian width is what the literature reports for a DOS -- around
        # 55-95 meV for a HOMO -- so fit each direction and put sigma on the legend.
        # See docs/manual.md for the references.
        plotted, dropped = [], 0
        for curve in curves:
            label = curve["direction"]
            # A Gaussian needs a PEAK. Where the sweep stops before the distribution
            # turns over there is only a rising edge, and the Gaussian rails against
            # the window reporting a width that describes nothing. An exponential
            # tail is what an edge supports, so both are tried and each is shown with
            # how well it actually describes the data.
            gauss = fit_gaussian_dos(curve["energy_ev"], curve["dos"])
            tail = fit_exponential_tail(curve["energy_ev"], curve["dos"])
            resolved = gauss["ok"] and not gauss.get("needs_review")

            if resolved:
                label += (f"   sigma = {gauss['sigma_mev']:.0f} +/- "
                          f"{gauss['sigma_sd_mev']:.0f} meV")
            elif tail["ok"]:
                label += (f"   no peak in range; tail E0 = "
                          f"{abs(tail['e0_mev']):.0f} +/- {tail['e0_sd_mev']:.0f} meV"
                          f"  (R2 {tail['r_squared']:.2f})")
                if tail["r_squared"] < 0.9:
                    label += ("\n" + "\n".join(
                        "  ! " + line if i == 0 else "    " + line
                        for i, line in enumerate(textwrap.wrap(
                            "the tail is not straight in log g either (R2 below 0.9), "
                            "so neither a Gaussian nor a single exponential describes "
                            "this window", 46))))
            elif gauss.get("concern"):
                label += "\n" + "\n".join(
                    "  ! " + line if i == 0 else "    " + line
                    for i, line in enumerate(textwrap.wrap(gauss["concern"], 46)))

            plotted.append((curve["energy_ev"], curve["dos"], label))
            # Only draw a model the data actually supports.
            if resolved:
                plotted.append((gauss["energy"], gauss["curve"],
                                f"{curve['direction']} — Gaussian"))
            elif tail["ok"] and tail["r_squared"] >= 0.9:
                plotted.append((tail["energy"], tail["curve"],
                                f"{curve['direction']} — exp tail"))
            dropped += int(np.sum(np.asarray(curve["dos"], float) <= 0))

        # The two directions are each other's control: if the film keeps up with the
        # sweep they measure the same distribution. When they do not, say so loudly --
        # every sigma and E0 below is then describing a transient, not a DOS.
        equilibrium_ok, equilibrium_msg = dos_equilibrium_check(curves)
        if not equilibrium_ok:
            provenance_warning = "NOT AT EQUILIBRIUM: " + equilibrium_msg
        else:
            provenance_warning = ""

        # Log y is the convention: a DOS spans orders of magnitude and the Gaussian is
        # reported over about two of them, which a linear axis flattens away. Points at
        # or below zero cannot be drawn on it -- they are the sweep turnarounds, where
        # the current has not reversed yet -- so the count is stated rather than
        # silently lost.
        note = f"   ({dropped} non-positive point(s) not shown on the log axis)" \
            if dropped else ""
        # Provenance goes UNDER the axis, not into the title: three clauses of it ran
        # off both sides of the canvas.
        provenance = [f"last cycle, {rate * 1000:.1f} mV/s ({source})"]
        if not volume:
            provenance.append("no film volume — dQ/dV")
        if from_form:
            provenance.append("geometry from the Parameters tab")
        if dropped:
            provenance.append(f"{dropped} non-positive point(s) omitted (log axis)")

        # Unwrapped on purpose -- the canvas wraps it to its own width.
        footnote = " · ".join(provenance)
        if provenance_warning:
            footnote += "\n" + provenance_warning
        self.canvas.plot_multi_xy(
            plotted,
            # Two lines: with energy on Y this becomes the VERTICAL label, and on
            # one line it was taller than the axes and clipped at both ends.
            "E = −eV  (eV)\nmore negative = more oxidizing",
            units, logy=True, swap_axes=self.dos_energy_y.isChecked(),
            footnote=footnote, footnote_warn=bool(provenance_warning),
            title="Density of states  [under development]")

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
        label = self._current_label() or "plot"
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
        # way to tell a slow load from a hung one. The requirement was a progress window
        # that becomes the "X segments loaded" box.
        # Release the PREVIOUS run before reading the next. Building the new one
        # alongside the old doubled peak memory, and on the 32-bit SpecEchem32 env
        # that is fatal: the user hit "Unable to allocate 40.6 MiB" on every segment of a
        # second load. The cost is that a cancelled or failed load now leaves nothing
        # loaded instead of the previous run -- which is the right trade when the
        # alternative is not being able to load at all.
        self._release_loaded_run()

        progress = QProgressDialog("Reading run…", "Cancel", 0, len(segs), self)
        progress.setWindowTitle("Loading run")
        # Windows puts a "?" context-help button in the title bar by default, which
        # reads as an unanswered question rather than a control.
        progress.setWindowFlags(progress.windowFlags()
                                & ~Qt.WindowContextHelpButtonHint)
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
            self._release_loaded_run()
            QMessageBox.information(
                self, "Load canceled",
                f"Stopped after {len(results)} of {len(segs)} segment(s).\n\n"
                "Nothing is loaded now — the previous run was released first to "
                "make room. Load again when ready.")
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
        # A NOTE, not a skip: nothing failed to load. Filing it under "Skipped:"
        # made a complete 13-of-13 load look like something had gone wrong.
        notes = []
        if not self.win.loaded_run_settings:
            notes.append("no run metadata — potentials come from the echem files "
                         "where present, and are otherwise unlabeled")
        self.refresh_segments()
        self.win.analysis_tab.refresh_segments()

        msg = f"Loaded {len(results)} segment(s) from:\n{folder}"
        if errors:
            msg += "\n\nSkipped:\n" + "\n".join(errors)
        if notes:
            msg += "\n\nNote:\n" + "\n".join(notes)
        QMessageBox.information(self, "Run loaded", msg)

    def _seed_dos_vmax(self, potential):
        """The DOS upper bound, filled from the sweep unless the user set it.

        Rounded UP to the box's 1 mV: +0.699498 V shown as 0.699 would cut the
        vertex off the curve. A value still equal to the last one filled in is taken
        as untouched and refilled for the new CV; anything else was typed, and kept.
        """
        import math
        current = round(self.dos_vmax.value(), 3)
        if self._dos_vmax_seeded is None or current == self._dos_vmax_seeded:
            top = math.ceil(float(np.nanmax(potential)) * 1000.0) / 1000.0
            self.dos_vmax.blockSignals(True)
            self.dos_vmax.setValue(top)
            self.dos_vmax.blockSignals(False)
            self._dos_vmax_seeded = round(self.dos_vmax.value(), 3)
        return self.dos_vmax.value()

    def _release_loaded_run(self):
        """Drop every reference to the loaded run, so its memory can be reclaimed.

        The plots hold the arrays too -- clearing the dicts alone leaves the figures
        pinning the previous run's data, which on a 32-bit build is most of what runs
        the process out of address space.
        """
        import gc
        self.win.results = {}
        self.win.segments_by_label = {}
        self.win.loaded_run_settings = None
        self.win._potential_cache.clear()
        self.canvas.show_message("No run loaded.")
        self.echem_canvas.show_message("No run loaded.")
        self.win.analysis_tab._fits.clear()
        self.win.analysis_tab._fit_wl.clear()
        self.refresh_segments()
        self.win.analysis_tab.refresh_segments()
        gc.collect()

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
