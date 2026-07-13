"""
Embedded matplotlib canvas, shared by the Instrument preview, the Run cockpit,
and the Results review tab. Static plots only (drawn on demand / post-segment).
"""
import matplotlib
matplotlib.use("QtAgg")
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

from spec_echem.gamry_data import POTENTIAL_COL, CURRENT_COL


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

    def _new_axes(self):
        """Fresh axes on a cleared figure — also removes any prior colorbar. Drops
        the live line so update_live_line() rebuilds it (e.g. on a new segment)."""
        self.fig.clear()
        self._live_line = None
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
        picks I-vs-E (CV) or I-vs-t (chrono) from the acq_data fields."""
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
                         textcoords="offset points", fontsize=7, color="#888")

        if result.get("t_limit") is not None:
            self.ax.axvline(result["t_limit"], ls="-", lw=1.0, color="#d62728", alpha=0.7)
            self.ax.annotate(f"limit {result['t_limit']:.4g} ms",
                             xy=(result["t_limit"], result["counts_limit"]),
                             xytext=(4, 6), textcoords="offset points",
                             fontsize=8, color="#d62728")
        t_rec = result.get("t_recommended")
        if t_rec is not None:
            self.ax.axvline(t_rec, ls="-", lw=1.4, color="#ff7f0e", alpha=0.9)
            self.ax.annotate(f"recommended {t_rec:.4g} ms", xy=(t_rec, counts[0]),
                             xytext=(-4, 4), textcoords="offset points",
                             fontsize=8, color="#ff7f0e", ha="right")

        self.ax.set_ylim(0, full_scale * 1.08)
        # Lower right: upper-left collides with the ADC full-scale label.
        self.ax.legend(fontsize=7, loc="lower right")
        self._decorate(title)
        self.draw_idle()

    def show_message(self, text):
        """Clear the canvas and show a centered note (e.g. 'no echem data yet')."""
        self._new_axes()
        self.ax.text(0.5, 0.5, text, ha="center", va="center",
                     transform=self.ax.transAxes, color="#888", fontsize=9)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw_idle()

    def show_absorbance(self, absorb_df, title=None, wl_min=None, wl_max=None):
        """
        Absorbance vs wavelength for every time point in a segment. Traces are
        colored by elapsed time (viridis), with a colorbar so the time evolution
        is legible. title is shown above the plot (the segment label).
        """
        self._ylabel = "Absorbance"
        self._new_axes()
        wl = absorb_df.index.values
        times = [float(c) for c in absorb_df.columns]
        cmap = matplotlib.colormaps["viridis"]
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
        self._decorate(title)
        sm = ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        self.fig.colorbar(sm, ax=self.ax, label="Time (s)")
        self.draw_idle()
