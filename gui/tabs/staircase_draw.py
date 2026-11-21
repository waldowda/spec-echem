"""Every-step views shared by the Titration (PITT) and GITT tabs (2026-11-20).

One step at a time hides how the steps differ; these draw them all on one plot,
coloured in run order (early dark, late light), with staircase_views supplying
the data. Each function draws through MplCanvas.plot_multi_xy, so Export replays it.
"""
import numpy as np
from matplotlib import cm

from spec_echem import staircase_views as sv


def step_colors(n):
    """n colours in run order. A colormap, not the 10-colour cycle, so step 11 is
    not step 1's colour again and the order reads at a glance."""
    if n <= 0:
        return []
    return [cm.viridis(x) for x in np.linspace(0.0, 0.9, n)]


MAX_LABELS = 5
ORDER_NOTE = "colour: dark = early, light = late"


def _labels(labels):
    """At most MAX_LABELS in the legend, always the first and the last; the rest are
    hidden from it ("_" prefix) but still drawn. Twelve entries covered the bleach
    band on the first film's spectra (2026-11-20)."""
    n = len(labels)
    if n <= MAX_LABELS:
        return list(labels)
    keep = set(np.round(np.linspace(0, n - 1, MAX_LABELS)).astype(int))
    return [lab if k in keep else "_" + lab for k, lab in enumerate(labels)]


def _note(*parts):
    return "; ".join(p for p in parts + (ORDER_NOTE,) if p)


def draw_timeline(canvas, traces, y_of, ylabel, label_of, title, logy=False,
                  footnote=""):
    """Every step on the run's clock, one colour per step."""
    if not traces:
        canvas.show_message("No step has echem.")
        return
    colors = step_colors(len(traces))
    labels = _labels([label_of(tr) for tr in traces])
    curves = []
    for tr, color, lab in zip(traces, colors, labels):
        y = y_of(tr)
        x = tr["t_run"]
        if logy:
            keep = np.abs(y) > 0
            x, y = x[keep], np.abs(y[keep])
        curves.append((x, y, lab, {"color": color, "lw": 1.0}))
    canvas.plot_multi_xy(curves, "Run time (s)", ylabel, title=title, logy=logy,
                         footnote=_note(footnote))


def draw_overlay(canvas, traces, y_of, ylabel, label_of, title, logy=False,
                 logx=False, footnote=""):
    """Every step from its own start, overlaid, one colour per step."""
    if not traces:
        canvas.show_message("No step has echem.")
        return
    colors = step_colors(len(traces))
    labels = _labels([label_of(tr) for tr in traces])
    curves = []
    for tr, color, lab in zip(traces, colors, labels):
        x, y = tr["t"], y_of(tr)
        keep = np.isfinite(x) & np.isfinite(y)
        if logy:
            keep &= np.abs(y) > 0
        if logx:
            keep &= x > 0
        x, y = x[keep], (np.abs(y[keep]) if logy else y[keep])
        curves.append((x, y, lab, {"color": color, "lw": 1.0}))
    canvas.plot_multi_xy(curves, "Time in step (s)", ylabel, title=title, logy=logy,
                         footnote=_note(footnote))


def draw_end_spectra(canvas, path, label_of_index, title):
    """The change in absorbance at each step's end, from the first spectrum of the
    first step, one colour per step."""
    wl, ref, ends = sv.step_end_spectra(path)
    if ref is None or not ends:
        canvas.show_message("This run has no spectra to compare.")
        return
    colors = step_colors(len(ends))
    labels = _labels([label_of_index(k) for k, _role, _a in ends])
    curves = [(wl, a - ref, lab, {"color": c, "lw": 1.0})
              for (_k, _role, a), c, lab in zip(ends, colors, labels)]
    biggest = max(ends, key=lambda e: float(np.nanmax(np.abs(e[2] - ref))))
    fall, rise = sv.band_wavelengths(wl, biggest[2] - ref)
    parts = ["ΔA = A at the step's end - the first spectrum of the first step"]
    if fall or rise:
        parts.append("largest fall %s, largest rise %s (most-changed step)" % (
            "%.0f nm" % fall if fall else "none", "%.0f nm" % rise if rise else "none"))
    canvas.plot_multi_xy(curves, "Wavelength (nm)", "ΔA", title=title,
                         footnote=_note(*parts))


def band_points(path, x_of_index):
    """{name: (xs, ys)} for the fall and rise bands, at each step's end; and the
    two wavelengths. `x_of_index(k)` gives a step's x (E, or charge), or None to
    leave the step out."""
    wl, ref, ends = sv.step_end_spectra(path)
    if ref is None or not ends:
        return {}, (None, None)
    # The bands of the most-changed step: the last one may be a dedope back to
    # where the run began.
    biggest = max(ends, key=lambda e: float(np.nanmax(np.abs(e[2] - ref))))
    fall, rise = sv.band_wavelengths(wl, biggest[2] - ref)
    out = {}
    for name, w in (("fall", fall), ("rise", rise)):
        if w is None:
            continue
        xs, ys = [], []
        for k, _role, a in ends:
            x = x_of_index(k)
            if x is None:
                continue
            xs.append(x)
            ys.append(sv.absorbance_at(wl, a, w) - sv.absorbance_at(wl, ref, w))
        out[name] = (xs, ys)
    return out, (fall, rise)


def draw_bands(canvas, path, x_of_index, xlabel, title):
    pts, (fall, rise) = band_points(path, x_of_index)
    if not pts:
        canvas.show_message("No absorbance band changed in this run.")
        return
    styles = {"fall": {"color": "#1f77b4", "marker": "o", "linestyle": "-"},
              "rise": {"color": "#d62728", "marker": "s", "linestyle": "-"}}
    names = {"fall": "%.0f nm (largest fall)" % fall if fall else "",
             "rise": "%.0f nm (largest rise)" % rise if rise else ""}
    curves = [(xs, ys, names[k], styles[k]) for k, (xs, ys) in pts.items()]
    canvas.plot_multi_xy(curves, xlabel, "ΔA at the step's end", title=title,
                         footnote="bands found from the most-changed step; ΔA from "
                                  "the first spectrum of the first step")
