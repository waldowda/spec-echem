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
        self.ax = self.fig.add_subplot(111)
        self._decorate()

    def _new_axes(self):
        """Fresh axes on a cleared figure — also removes any prior colorbar."""
        self.fig.clear()
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

    def show_live_echem(self, x, y, xlabel, ylabel, title=None):
        """Live echem trace mid-run (red = running). Generic x/y so the caller
        picks I-vs-E (CV) or I-vs-t (chrono) from the acq_data fields."""
        self._xlabel, self._ylabel = xlabel, ylabel
        self._new_axes()
        self.ax.plot(x, y, lw=1.0, color="#d62728")
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
        self._decorate(title)
        sm = ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        self.fig.colorbar(sm, ax=self.ax, label="Time (s)")
        self.draw_idle()
