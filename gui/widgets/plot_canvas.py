"""
Embedded matplotlib canvas, shared by the Instrument preview, the Run cockpit,
and the Results review tab. Static plots only (drawn on demand / post-segment).
"""
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg


class MplCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None, xlabel="Wavelength (nm)", ylabel="Intensity (counts)"):
        self.fig = Figure(figsize=(5, 3), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self._xlabel = xlabel
        self._ylabel = ylabel
        self._decorate()

    def _decorate(self, title=None):
        self.ax.set_xlabel(self._xlabel)
        self.ax.set_ylabel(self._ylabel)
        if title:
            self.ax.set_title(title)
        self.ax.grid(True, alpha=0.3)

    def clear(self):
        self.ax.clear()
        self._decorate()
        self.draw_idle()

    def show_spectrum(self, wavelengths, values, title=None, ylabel="Intensity (counts)"):
        """Single intensity/absorbance trace vs wavelength."""
        self._ylabel = ylabel
        self.ax.clear()
        self.ax.plot(wavelengths, values, lw=1.0, color="#1f77b4")
        self._decorate(title)
        self.draw_idle()

    def show_dark_ref(self, wavelengths, dark, ref):
        """Overlay dark and reference (100%T) for checking detector range."""
        self._ylabel = "Intensity (counts)"
        self.ax.clear()
        if dark is not None:
            self.ax.plot(wavelengths, dark, lw=1.0, color="#444", label="Dark")
        if ref is not None:
            self.ax.plot(wavelengths, ref, lw=1.0, color="#d62728", label="Reference (100%T)")
        self._decorate("Dark & 100%T")
        self.ax.legend(loc="best", fontsize=8)
        self.draw_idle()

    def show_absorbance(self, absorb_df, title=None, wl_min=None, wl_max=None):
        """
        Absorbance vs wavelength for every time point in a segment.
        absorb_df: DataFrame indexed by wavelength (rows), columns = relative time (s).
        Traces are colored light→dark by time so evolution is visible.
        """
        self._ylabel = "Absorbance"
        self.ax.clear()
        wl = absorb_df.index.values
        n = absorb_df.shape[1]
        cmap = matplotlib.colormaps["viridis"]
        for i, col in enumerate(absorb_df.columns):
            shade = cmap(i / max(n - 1, 1))
            self.ax.plot(wl, absorb_df[col].values, lw=0.8, color=shade)
        if wl_min is not None and wl_max is not None:
            self.ax.set_xlim(wl_min, wl_max)
        self._decorate(title)
        self.draw_idle()
