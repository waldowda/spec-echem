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

    def show_dark_ref(self, wavelengths, dark, ref):
        """Overlay dark and reference (100%T) for checking detector range."""
        self._ylabel = "Intensity (counts)"
        self._new_axes()
        if dark is not None:
            self.ax.plot(wavelengths, dark, lw=1.0, color="#444", label="Dark")
        if ref is not None:
            self.ax.plot(wavelengths, ref, lw=1.0, color="#d62728", label="Reference (100%T)")
        self._decorate("Dark & 100%T")
        self.ax.legend(loc="best", fontsize=8)
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
