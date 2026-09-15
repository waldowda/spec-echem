"""
Embedded matplotlib canvas, shared by the Instrument preview, the Run cockpit,
and the Results review tab. Static plots only (drawn on demand / post-segment).
"""
import logging
import textwrap

import matplotlib
matplotlib.use("QtAgg")
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

from spec_echem.gamry_data import POTENTIAL_COL, CURRENT_COL

logger = logging.getLogger(__name__)


class MplCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None, xlabel="Wavelength (nm)", ylabel="Intensity (counts)"):
        self.fig = Figure(figsize=(5, 3), tight_layout=True)
        super().__init__(self.fig)
        self.setParent(parent)
        self._xlabel = xlabel
        self._ylabel = ylabel
        self._live_line = None   # persistent Line2D for the incremental live trace
        self.ax = self.fig.add_subplot(111)
        self._decorate()

    def _set_layout(self, mode):
        """Select tight or constrained layout across matplotlib versions.

        set_layout_engine() arrived in matplotlib 3.6. The 32-bit SpecEchem32 env is
        Python 3.7 with an older matplotlib that has only the boolean setters, and
        calling the new API there raised AttributeError from _new_axes -- which every
        plot goes through, so the GUI died at startup on the Gamry rig.
        """
        try:
            if hasattr(self.fig, "set_layout_engine"):
                self.fig.set_layout_engine(mode)
            elif mode == "constrained":
                # Mutually exclusive on the old API, so clear one before the other.
                self.fig.set_tight_layout(False)
                self.fig.set_constrained_layout(True)
            else:
                self.fig.set_constrained_layout(False)
                self.fig.set_tight_layout(True)
        except Exception:  # noqa: BLE001
            # Deliberately broad. Layout is COSMETIC, and this call sits in
            # _new_axes, which every plot goes through -- so an unsupported spelling
            # on some matplotlib version takes the whole GUI down at startup, which
            # is exactly what happened on SpecEchem32. A slightly misaligned label
            # beats an application that will not launch. The fallback branch cannot
            # be tested from the dev machine: on matplotlib >= 3.6 the old setters
            # delegate to the new API, so removing it to simulate an old version
            # breaks the very path being tested.
            logger.debug("layout engine %r unsupported by this matplotlib", mode)

    def _new_axes(self):
        """Fresh axes on a cleared figure — also removes any prior colorbar. Drops
        the live line so update_live_line() rebuilds it (e.g. on a new segment)."""
        self.fig.clear()
        self._live_line = None
        # plot_fit() switches the engine to constrained for its shared-x layout;
        # every other plot wants tight, so restore it here.
        self._set_layout("tight")
        self.ax = self.fig.add_subplot(111)

    def _decorate(self, title=None):
        self.ax.set_xlabel(self._xlabel)
        self.ax.set_ylabel(self._ylabel)
        if title:
            self.ax.set_title(title)
        self.ax.grid(True, alpha=0.3)

    def clear(self):
        self._new_axes()
        self._decorate()
        self.draw_idle()

    def show_spectrum(self, wavelengths, values, title=None, ylabel="Intensity (counts)",
                      mark_max=False):
        """Single intensity/absorbance trace vs wavelength. mark_max annotates the
        peak with its value — used by the raw-counts test to show the detector level."""
        self._ylabel = ylabel
        self._new_axes()
        self.ax.plot(wavelengths, values, lw=1.0, color="#1f77b4")
        if mark_max and len(values):
            i = int(np.argmax(values))
            xmax, ymax = wavelengths[i], values[i]
            self.ax.plot([xmax], [ymax], "o", color="#d62728", ms=5)
            self.ax.annotate(f"max {ymax:.0f} counts @ {xmax:.0f} nm",
                             xy=(xmax, ymax), xytext=(0.98, 0.96),
                             textcoords="axes fraction", ha="right", va="top",
                             fontsize=8, color="#d62728")
        self._decorate(title)
        self.draw_idle()

    def show_cv(self, df, title=None):
        """Cyclic voltammogram: current vs potential (I vs E). Cycles concatenated."""
        self._xlabel, self._ylabel = "Potential (V)", "Current (A)"
        self._new_axes()
        self.ax.plot(df[POTENTIAL_COL].values, df[CURRENT_COL].values, lw=1.0, color="#1f77b4")
        self._decorate(title)
        self.draw_idle()

    def show_chrono(self, df, title=None):
        """Chronoamperometry: current vs corrected time (I vs t)."""
        self._xlabel, self._ylabel = "Time (s)", "Current (A)"
        self._new_axes()
        self.ax.plot(df["Corrected time (s)"].values, df[CURRENT_COL].values,
                     lw=1.0, color="#1f77b4")
        self._decorate(title)
        self.draw_idle()

    def update_live_line(self, x, y, xlabel, ylabel, title=None):
        """Incremental live echem trace mid-run (red = running). Reuses ONE Line2D
        and just updates its data + rescales, instead of clearing and rebuilding the
        whole figure each tick — a much lighter redraw, so it holds the GIL only
        briefly and doesn't jitter the spectra cadence on the worker thread. The
        line resets whenever the axes are cleared (_new_axes → _live_line=None),
        e.g. show_message() at the start of each segment. Generic x/y so the caller
        picks I-vs-E (CV) or I-vs-t (chrono) from the EchemData arrays."""
        if self._live_line is None:
            self._xlabel, self._ylabel = xlabel, ylabel
            self._new_axes()
            (self._live_line,) = self.ax.plot([], [], lw=1.0, color="#d62728")
            self._decorate(title)
        self._live_line.set_data(x, y)
        self.ax.relim()
        self.ax.autoscale_view()
        self.draw_idle()

    def show_linearity(self, times, counts, result, full_scale=65535, title=None):
        """
        Detector response vs integration time: measured points, the fitted linear
        region, the ADC ceiling, and the linearity limit / recommended time.
        `result` is a dict from spec_echem.linearity.analyze_linearity.
        """
        self._xlabel, self._ylabel = "Integration time (ms)", "Counts (peak pixel)"
        self._new_axes()

        times = np.asarray(times, float)
        self.ax.plot(times, counts, "o", ms=4, color="#1f77b4", label="measured", zorder=3)

        # The fitted linear region, extrapolated across the full ramp so the
        # departure from linearity is visible as the gap between line and points.
        fit = result["offset"] + result["slope"] * times
        self.ax.plot(times, fit, "--", lw=1.0, color="#2ca02c", label="linear fit", zorder=2)

        self.ax.axhline(full_scale, ls=":", lw=1.0, color="#888")
        self.ax.annotate("ADC full scale", xy=(times[0], full_scale), xytext=(2, -10),
                         textcoords="offset points", fontsize=7, color="#888",
                         clip_on=True)

        # The fill cap usually decides the working point (the detector stays linear
        # nearly to the clip), so show it — otherwise the recommendation looks arbitrary.
        counts_rec = result.get("counts_recommended")
        if counts_rec is not None and result.get("bound_by") == "fill":
            self.ax.axhline(counts_rec, ls=":", lw=1.0, color="#ff7f0e")
            self.ax.annotate(f"max fill ({counts_rec / full_scale * 100:.0f}% FS)",
                             xy=(times[0], counts_rec), xytext=(2, -10),
                             textcoords="offset points", fontsize=7, color="#ff7f0e",
                             clip_on=True)

        if result.get("t_limit") is not None:
            self.ax.axvline(result["t_limit"], ls="-", lw=1.0, color="#d62728", alpha=0.7)
            self.ax.annotate(f"limit {result['t_limit']:.4g} ms",
                             xy=(result["t_limit"], result["counts_limit"]),
                             xytext=(4, 6), textcoords="offset points",
                             fontsize=8, color="#d62728", clip_on=True)
        t_rec = result.get("t_recommended")
        if t_rec is not None:
            self.ax.axvline(t_rec, ls="-", lw=1.4, color="#ff7f0e", alpha=0.9)
            # Anchored in the axes corner, not at the (t_rec, counts[0]) data point:
            # a recommendation near the left edge used to push right-aligned text off
            # the canvas. Legend is lower-right, so the upper-left corner is free.
            self.ax.annotate(f"recommended {t_rec:.4g} ms",
                             xy=(0.03, 0.97), xycoords="axes fraction",
                             fontsize=8, color="#ff7f0e", ha="left", va="top",
                             clip_on=True)

        self.ax.set_ylim(0, full_scale * 1.08)
        # Lower right: upper-left collides with the ADC full-scale label.
        self.ax.legend(fontsize=7, loc="lower right")
        self._decorate(title)
        self.draw_idle()

    def plot_series(self, x, series, xlabel, ylabel, title=None, styles=None,
                    yerr=None, flags=None, logy=False):
        """Several named y-series against one x, as markers joined by lines.

        NaN is left as NaN on purpose: the analysis tab uses it where a fit failed, so
        the line shows a visible gap rather than joining across a potential that was
        never measured. Silently dropping those points would hide which ones failed.

        `styles` overrides plot kwargs per series name -- the ladder uses it to draw
        dedoping dashed against the same colour as its doping counterpart. `yerr` adds
        error bars per series; NaN entries there simply draw no bar on that point.
        `flags` marks individual points with a hollow ring: the ladder uses it for a
        fit that converged but failed a check, which is PLOTTED rather than dropped so
        the value can be judged, with the ring saying not to trust it blindly.
        """
        import numpy as _np

        self._xlabel, self._ylabel = xlabel, ylabel
        self._new_axes()
        x = _np.asarray(x, dtype=float)
        for name, y in series.items():
            kw = dict(marker="o", ms=4, lw=1.0)
            kw.update((styles or {}).get(name, {}))
            marked = (flags or {}).get(name)
            err = (yerr or {}).get(name)
            if err is None:
                self.ax.plot(x, _np.asarray(y, dtype=float), label=str(name), **kw)
            else:
                # capsize so a point whose interval is smaller than the marker still
                # reads as "measured precisely" rather than "no error bar drawn".
                self.ax.errorbar(x, _np.asarray(y, dtype=float),
                                 yerr=_np.asarray(err, dtype=float),
                                 label=str(name), capsize=3, elinewidth=1.0, **kw)
            if marked is not None and any(marked):
                m = _np.asarray(marked, dtype=bool)
                yy = _np.asarray(y, dtype=float)
                self.ax.plot(_np.asarray(x, dtype=float)[m], yy[m], "o", ms=11,
                             mfc="none", mec="#e07b00", mew=1.6, zorder=6,
                             linestyle="none", label="_nolegend_")
        if logy:
            # symlog, not log: the ratio view can legitimately be near zero, and a
            # hard log axis would drop those points silently.
            self.ax.set_yscale("symlog", linthresh=1e-3)
        if len(series) > 1:
            self.ax.legend(fontsize="small")
        self._decorate(title)
        self.draw_idle()

    def plot_fit(self, t, y, fit_y, xlabel, ylabel, title=None, window=None,
                 note=None, fit_ok=True, caution=None):
        """Data with the fitted curve over it, plus a residual strip.

        The residual panel is the point: an exponential and a stretched exponential
        drawn over the same decay look nearly identical at this size, and the way you
        tell them apart is structure in the residuals. Overlap alone would let a
        visibly wrong model pass.

        `window` shades the excluded region so it is obvious which points the fit
        actually used. fit_y may be None (a failed fit) — the data still plots, which
        is what you need in order to choose a better window.
        """
        self._xlabel, self._ylabel = xlabel, ylabel
        self.fig.clear()
        self._live_line = None
        # tight_layout cannot handle the shared-x gridspec below and silently clips
        # the y-label and the x-label off the canvas; constrained layout handles it.
        self._set_layout("constrained")
        # Residuals go ABOVE the data: that is the convention in the spectroscopy
        # fitting this sits next to (XPS, NMR, IR). Reflectivity and astronomy put
        # them below, which is the other common choice -- not a neutral default.
        gs = self.fig.add_gridspec(2, 1, height_ratios=[1, 3], hspace=0.05)
        self.ax = self.fig.add_subplot(gs[1])
        self.resid_ax = self.fig.add_subplot(gs[0], sharex=self.ax)

        t = np.asarray(t, dtype=float)
        y = np.asarray(y, dtype=float)
        self.ax.plot(t, y, "o", ms=2.5, color="#1f77b4", alpha=0.55, label="data",
                     zorder=2)

        if fit_y is not None:
            fit_y = np.asarray(fit_y, dtype=float)
            # A fit under review is still drawn, dashed and amber so it reads as a
            # caution rather than an endorsement.
            colour = "#d62728" if fit_ok else "#e07b00"
            # The concern goes INTO the legend entry, in amber, with an amber frame
            # round the box. Requested: "I would remove the box and put the NEEDS REVIEW
            # section in the legend in amber... so you would not need that big in
            # your face box." The banner was covering the parameters it sat next to.
            label = note or ("fit" if fit_ok else "fit — needs review")
            if caution:
                label = f"{label}\n{caution}"
            self.ax.plot(t, fit_y, "-" if fit_ok else "--", lw=1.4, color=colour,
                         label=label, zorder=3)
            resid = y - fit_y
            self.resid_ax.plot(t, resid, "o", ms=2.0, color="#1f77b4", alpha=0.6)
            self.resid_ax.axhline(0.0, ls="-", lw=0.8, color=colour, alpha=0.8)

            legend = self.ax.legend(fontsize=7, loc="best")
            if not fit_ok:
                legend.get_frame().set_edgecolor("#e07b00")
                legend.get_frame().set_linewidth(1.4)
                for text in legend.get_texts():
                    text.set_color("#7a4a00")
        else:
            self.resid_ax.text(0.5, 0.5, "no fit", ha="center", va="center",
                               transform=self.resid_ax.transAxes,
                               color="#888", fontsize=8)
            self.resid_ax.set_yticks([])   # no residuals; 0-1 ticks describe nothing
            # No curve, so nothing to hang a legend entry on: a plain centred note.
            message = caution or note or ""
            if message:
                self.ax.annotate(
                    "\n".join(textwrap.fill(line, 44)
                               for line in message.splitlines() or [""]),
                    xy=(0.5, 0.5), xycoords="axes fraction",
                    ha="center", va="center", fontsize=9, clip_on=True,
                    color="#7a4a00" if caution else "#888")

        # Grey out what the fit did not see, so a window that excludes the decay
        # itself is visible at a glance rather than inferred from a bad tau.
        if window is not None and len(t):
            lo, hi = window
            for a in (self.ax, self.resid_ax):
                if lo is not None and lo > t[0]:
                    a.axvspan(t[0], lo, color="#999", alpha=0.13, lw=0, zorder=1)
                if hi is not None and hi < t[-1]:
                    a.axvspan(hi, t[-1], color="#999", alpha=0.13, lw=0, zorder=1)

        self.ax.set_ylabel(ylabel)
        self.ax.set_xlabel(xlabel)
        self.ax.grid(True, alpha=0.3)
        self.resid_ax.set_ylabel("resid.", fontsize=8)
        self.resid_ax.grid(True, alpha=0.3)
        self.resid_ax.tick_params(labelsize=7, labelbottom=False)  # shared x, below
        # suptitle, not a title on either panel: the top panel is the residual
        # strip now, and a title on the main axes would land between the two.
        if title:
            self.fig.suptitle(title, fontsize="medium")
        self.draw_idle()

    def plot_multi_xy(self, curves, xlabel, ylabel, title=None):
        """Several (x, y, label) curves that do NOT share an x axis.

        plot_series takes one x for every series, which is right for a ladder. A CV's
        forward and reverse sweeps sample different potentials, so each needs its own.
        """
        self._xlabel, self._ylabel = xlabel, ylabel
        self._new_axes()
        for x, y, label in curves:
            self.ax.plot(np.asarray(x, dtype=float), np.asarray(y, dtype=float),
                         lw=1.2, label=str(label))
        if len(curves) > 1:
            self.ax.legend(fontsize="small")
        self._decorate(title)
        self.draw_idle()

    def show_message(self, text):
        """Clear the canvas and show a centered note (e.g. 'no echem data yet').

        Long strings — a LinearityError sentence, say — are hard-wrapped so they
        stay inside the axes instead of running off both edges of a small canvas.
        """
        self._new_axes()
        wrapped = "\n".join(textwrap.fill(line, 48)
                            for line in (text.splitlines() or [""]))
        self.ax.text(0.5, 0.5, wrapped, ha="center", va="center",
                     transform=self.ax.transAxes, color="#888", fontsize=9,
                     wrap=True, clip_on=True)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw_idle()

    def show_absorbance(self, absorb_df, title=None, wl_min=None, wl_max=None,
                        mark_wl=None):
        """
        Absorbance vs wavelength for every time point in a segment. Traces are
        colored by elapsed time (viridis), with a colorbar so the time evolution
        is legible. title is shown above the plot (the segment label).
        """
        self._ylabel = "Absorbance"
        self._new_axes()
        wl = absorb_df.index.values
        times = [float(c) for c in absorb_df.columns]
        # matplotlib.colormaps is 3.5+; SpecEchem32's matplotlib predates
        # set_layout_engine (3.6), so it may predate this too. get_cmap is the old
        # spelling and was removed in 3.9, so both are needed. PRE-EXISTING, not
        # introduced with the analysis work -- hardened while fixing the sibling bug.
        try:
            cmap = matplotlib.colormaps["viridis"]
        except AttributeError:
            cmap = matplotlib.cm.get_cmap("viridis")
        norm = Normalize(vmin=min(times), vmax=max(times)) if len(times) > 1 \
            else Normalize(vmin=0.0, vmax=1.0)
        for t, col in zip(times, absorb_df.columns):
            self.ax.plot(wl, absorb_df[col].values, lw=0.8, color=cmap(norm(t)))
        if wl_min is not None and wl_max is not None:
            self.ax.set_xlim(wl_min, wl_max)
            # Rescale y to the data inside the window — otherwise the y-axis stays
            # fixed to the full-spectrum range (dominated by the pi-pi* peak) and a
            # zoom into the weaker polaron region looks squished.
            mask = (wl >= wl_min) & (wl <= wl_max)
            windowed = absorb_df.to_numpy()[mask]
            finite = windowed[np.isfinite(windowed)]
            if finite.size:
                lo, hi = float(finite.min()), float(finite.max())
                pad = (hi - lo) * 0.05 or 0.01   # small margin; guard flat data
                self.ax.set_ylim(lo - pad, hi + pad)
        if mark_wl is not None:
            # Where the kinetics/modulation views are sampling. Without it you have to
            # estimate a wavelength off the x-axis and type it in blind.
            self.ax.axvline(mark_wl, color="#d62728", lw=1.2, alpha=0.85, zorder=5)
            self.ax.annotate(f"{mark_wl:.1f} nm", xy=(mark_wl, 1.0),
                             xycoords=("data", "axes fraction"),
                             xytext=(3, -3), textcoords="offset points",
                             ha="left", va="top", fontsize=8, color="#d62728",
                             clip_on=True)
        self._decorate(title)
        sm = ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        self.fig.colorbar(sm, ax=self.ax, label="Time (s)")
        self.draw_idle()
