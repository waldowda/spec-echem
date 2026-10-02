"""
Igor Text (.itx) export: named waves, correct axis scaling, and a Display.

Requested: *"it would be good to get data out in a nice format such as the graphs...
export an Igorpro file I can open in Igor with all the data and graph info setup to
print, since Igor has such amazing graphing and formatting capabilities."*

So this writes the numbers behind a figure as Igor waves plus a minimal `Display`,
and stops there. It does NOT try to reproduce the matplotlib styling: Igor's
formatting is the reason for exporting to it, and generated `ModifyGraph` calls
would be guesses at conventions the user already has, to be fought rather than used.

`.itx` and not `.pxp`: `.itx` is plain text and documented, carries both the waves
AND `X` command lines that Igor executes on load, and can therefore be tested here
without Igor on the machine. `.pxp` is an undocumented binary container.
"""
import re

import numpy as np

# Igor 6 caps object names at 31 characters and allows only letters, digits and
# underscore, starting with a letter. Igor 7+ relaxes this, but a file that loads
# everywhere is worth more than a few characters of fidelity.
MAX_NAME = 31

# Igor takes 16-bit colour components. Blue for data and red for the fit, matching
# the matplotlib figure these numbers came from.
RGB_FIT = "65535,0,0"

# Matched to the matplotlib figure these numbers come from, so the Igor graph and
# the saved PNG are recognisably the same plot: markers at 2.5 pt, the fit line at
# 1.4 pt, and a full box -- matplotlib draws all four spines, Igor draws two.
MARKER_SIZE = 2.5
FIT_WIDTH = 1.4
RGB_SERIES = ("0,0,65535", "0,39168,0", "65535,32768,0", "39168,0,39168")


def wave_name(text, used=None):
    """A legal, unique Igor wave name from arbitrary column text.

    'mean tau (s), 95% CI' -> 'mean_tau_s_95_CI'. Collisions get _2, _3, ... rather
    than silently overwriting: two columns reduced to the same name would otherwise
    leave one of them simply missing from the file.
    """
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", str(text)).strip("_")
    if not cleaned:
        cleaned = "wave"
    if not cleaned[0].isalpha():
        cleaned = "w_" + cleaned
    cleaned = cleaned[:MAX_NAME]
    if used is None:
        return cleaned
    candidate, n = cleaned, 1
    while candidate.lower() in used:
        n += 1
        suffix = f"_{n}"
        candidate = cleaned[:MAX_NAME - len(suffix)] + suffix
    used.add(candidate.lower())
    return candidate


# A 2-D absorbance block is ~760k numbers. repr() spends 17 characters on each,
# which is 13 MB of text for digits that are not there: the stored absorbance is
# float32, so about 7 significant figures is everything it knows.
MATRIX_FORMAT = "%.7g"


def _format(value):
    """A number Igor will parse. NaN is written as NaN, which Igor understands and
    plots as a gap -- the same thing the fits mean by it."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "NaN"
    return repr(float(value))


def _escape(text):
    """Igor string literal contents. An unescaped quote ends the string early and
    the whole file fails to load."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def write_itx(path, waves, display=None, title=None, notes=(),
              xlabel=None, ylabel=None, resid=None, prefix_stem=""):
    """Write `waves` to an Igor Text file.

    waves   -- ordered {name: 1-D sequence}. Names are sanitised; the caller's
               order is kept, because the first numeric wave is the x axis.
    display -- (x_name, [y_names]) to emit a Display command, or None for data only.
    notes   -- lines recorded in the file as Igor comments, for provenance.
    """
    used = set()
    named = []
    for raw, values in waves.items():
        name = wave_name(raw, used)
        named.append((name, np.asarray(values, dtype=float), str(raw)))

    lines = ["IGOR"]
    for line in notes:
        lines.append(f"X // {line}")
    for name, values, raw in named:
        lines.append(f"WAVES/D/N=({values.size})\t{name}")
        lines.append("BEGIN")
        lines.extend("\t" + _format(v) for v in values)
        lines.append("END")
        # The original column header, kept where Igor shows it: the sanitised name
        # loses punctuation and units, and those are what make a column readable.
        if raw != name:
            lines.append(f'X Note {name}, "{raw}"')

    if display:
        x_raw, y_raws = display
        by_raw = {raw: name for name, _v, raw in named}
        x = by_raw.get(x_raw)
        ys = [by_raw[y] for y in y_raws if y in by_raw]
        if x and ys:
            lines.append("X Display " + ",".join(ys) + f" vs {x}")
            # Per TRACE, not the whole graph. A bare ModifyGraph sets every trace at
            # once, which drew the fit as red markers on top of the data it was
            # meant to be a line through (seen in Igor, 2026-10-02).
            #
            # This is the one styling liberty taken, and only because points-for-data
            # and a line-for-fit is not a convention anyone has to be asked about.
            # Everything else is left to Igor on purpose.
            for i, name in enumerate(ys):
                is_fit = name.lower().endswith("_fit") or name.lower() == "fit"
                mode = 0 if is_fit else 3
                # A COLOUR per trace. Without one Igor draws them all the same and
                # the fit is indistinguishable from the data it runs through --
                # seen in Igor 2026-10-02. Blue data, red fit, matching the figure
                # these numbers came from so the two are recognisably the same plot.
                rgb = RGB_FIT if is_fit else RGB_SERIES[i % len(RGB_SERIES)]
                parts = [f"mode({name})={mode}", f"rgb({name})=({rgb})"]
                if is_fit:
                    parts.append(f"lsize({name})={FIT_WIDTH}")
                else:
                    parts += [f"marker({name})=19", f"msize({name})={MARKER_SIZE}"]
                lines.append("X ModifyGraph " + ",".join(parts))
            # Axis labels are DATA, not decoration: a bare number axis makes the
            # reader guess at seconds versus nanometres.
            lines.append("X ModifyGraph mirror(bottom)=1,mirror(left)=1")
            if xlabel:
                lines.append(f'X Label bottom "{_escape(xlabel)}"')
            if ylabel:
                lines.append(f'X Label left "{_escape(ylabel)}"')
            if resid:
                lines.append(f"X AppendToGraph/L={RESID_AXIS} {resid} vs {x}")
                lines.append(f"X ModifyGraph axisEnab(left)="
                             f"{{{DATA_SPAN[0]},{DATA_SPAN[1]}}}")
                lines.append(f"X ModifyGraph axisEnab({RESID_AXIS})="
                             f"{{{RESID_SPAN[0]},{RESID_SPAN[1]}}},"
                             # {0,kwFraction}: at the LEFT EDGE of the plot area.
                             # A bare 0 means x=0 in DATA units, which put the axis
                             # and its label on top of the trace (Igor, 2026-10-02).
                             f"freePos({RESID_AXIS})={{0,kwFraction}}")
                lines.append(f"X ModifyGraph mode({resid})=3,marker({resid})=19,"
                             f"msize({resid})={MARKER_SIZE},"
                             f"rgb({resid})=({RGB_SERIES[0]})")
                lines.append(f"X ModifyGraph mirror({RESID_AXIS})=1")
                lines.append(f'X Label {RESID_AXIS} "resid."')
                lines.append(f"X ModifyGraph nticks({RESID_AXIS})=3")
            # Named from the DATA, not from the wave names: Igor's automatic legend
            # would read "Doping6_fit_biexp_y", which is the file's business and not
            # the reader's. \\s(trace) draws that trace's own symbol.
            entries = []
            for name in ys:
                plain = name[len(prefix_stem) + 1:] if prefix_stem else name
                entries.append(f"\\\\s({name}) "
                               + LEGEND_LABELS.get(plain.lower(), plain))
            if entries:
                lines.append('X Legend/C/N=leg/A=RB "' + "\\r".join(entries) + '"')
            if title:
                # The WINDOW's title, not a TextBox in the plot area. A TextBox
                # anchored middle-top is drawn INSIDE the axes and landed on the
                # data (Igor, 2026-10-02); the window title cannot collide with
                # anything, and Igor's own default there is just the wave names.
                lines.append(f'X DoWindow/T kwTopWin, "{_escape(title)}"')

    text = "\n".join(lines) + "\n"
    # \r\n: Igor on Windows is the common case and tolerates it on macOS, where a
    # bare \n file loads but shows as one long line in some text editors.
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write(text)
    return [name for name, _v, _raw in named]


# Room for a prefix without the generic part of the name being truncated away.
MAX_PREFIX = 18


# Columns that belong in the FILE but not on the graph. A residual shares the x
# axis and nothing else: plotted beside the data it is a flat line at zero that
# squashes everything, which is why the matplotlib version gives it its own panel.
NOT_DISPLAYED = ("residual",)

# The residual gets its OWN panel above the data, as it does in the figure these
# numbers come from -- that is the convention in the spectroscopy this sits beside
# (XPS, NMR, IR). On the same axes it is a flat line at zero that squashes
# everything; left out entirely it is the one thing that says whether the fit is
# any good. These fractions leave a gap between the two panels.
RESID_AXIS = "resid"

# What the generic frame columns are called on a graph. "y" is what the column is
# named in the file; "data" is what it IS.
LEGEND_LABELS = {"y": "data", "fit": "fit"}
DATA_SPAN = (0.0, 0.72)
RESID_SPAN = (0.80, 1.0)


def frame_to_itx(path, frame, title=None, notes=(), prefix=None,
                 xlabel=None, ylabel=None):
    """Write a tidy DataFrame: first column is x, the rest are plotted against it.

    This is the shape MplCanvas.last_data() returns, which is the same data the CSV
    export writes -- so the Igor file and the CSV cannot disagree about what the
    figure showed.

    `prefix` is prepended to every wave name, because Igor waves are GLOBAL to an
    experiment: without it, exporting two segments gives both of them waves called
    x, y and fit, and loading the second overwrites the first. Comparing segments
    in Igor is the obvious reason to export more than one, so the names have to
    survive being in the same experiment.
    """
    columns = list(frame.columns)
    if not columns:
        raise ValueError("nothing to export: the frame has no columns")
    numeric = [c for c in columns
               if np.issubdtype(np.asarray(frame[c]).dtype, np.number)]
    if not numeric:
        raise ValueError("nothing to export: no numeric columns")
    x = numeric[0]
    ys = [c for c in numeric[1:] if str(c).lower() not in NOT_DISPLAYED]
    resid_col = next((c for c in numeric if str(c).lower() == "residual"), None)
    stem = ""
    if prefix:
        stem = wave_name(prefix)[:MAX_PREFIX].rstrip("_")
        waves = {f"{stem}_{c}": frame[c].to_numpy() for c in numeric}
        x, ys = f"{stem}_{x}", [f"{stem}_{y}" for y in ys]
        resid = f"{stem}_{resid_col}" if resid_col is not None else None
    else:
        waves = {c: frame[c].to_numpy() for c in numeric}
        resid = resid_col
    return write_itx(path, waves, display=(x, ys), title=title, notes=notes,
                     xlabel=xlabel, ylabel=ylabel, resid=resid, prefix_stem=stem)


# A cap on how many spectra get exported. None = every one, which is the default and
# what the figure draws. The old default of 25 thinned a 721-spectrum CV into a sparse
# fan -- a DIFFERENT plot from the dense band on screen, which is exactly how it looked
# in Igor (2026-10-02). The cap stays only as an escape hatch.
SPECTRA_TRACES = None


def _ramp(i, n):
    """Dark blue -> green -> yellow across the series, so TIME reads as colour the
    way it does in the figure these numbers come from."""
    f = 0.0 if n < 2 else i / (n - 1.0)
    if f < 0.5:
        g = 2 * f
        return f"{int(4000 * (1 - g))},{int(26000 * g)},{int(30000 + 25000 * (1 - g))}"
    g = 2 * (f - 0.5)
    return f"{int(60000 * g)},{int(26000 + 30000 * g)},{int(20000 * (1 - g))}"


def spectra_to_itx(path, frame, name, title=None, notes=(),
                   xlabel=None, ylabel=None, traces=SPECTRA_TRACES):
    """A wavelength x time block as ONE 2-D wave, displayed column by column.

    EVERY spectrum goes in the file. An earlier version wrote 25 evenly spaced waves;
    that is a different plot from the one being exported -- the figure draws all 721
    spectra of a CV as a dense band, and the thinned version came out as a sparse fan
    (2026-10-02).

    A 2-D wave rather than one wave per time because 721 separate `WAVES` blocks is
    unreadable in Igor's data browser, and because the block can then be put on a graph
    with one short command per column instead of one 14 KB `Display` line, which would
    be past Igor's command-length limit.

    `traces` caps the count if a block is ever big enough to need it (the times kept are
    evenly spaced and always include the first and last). None means all.
    """
    values = np.asarray(frame.values, dtype=float)
    wl = np.asarray(frame.index.values, dtype=float)
    times = np.asarray(frame.columns.values, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"expected a 2-D block; got {values.shape}")

    if traces is None or int(traces) >= times.size:
        picks = np.arange(times.size)
    else:
        count = max(2, int(traces))
        picks = np.unique(np.linspace(0, times.size - 1, count).round().astype(int))
    total = times.size
    values = values[:, picks]
    times = times[picks]

    stem = wave_name(name)[:MAX_PREFIX].rstrip("_")
    wl_wave, t_wave, mat = f"{stem}_wl", f"{stem}_t", f"{stem}_a"

    lines = ["IGOR"]
    for line in notes:
        lines.append(f"X // {line}")
    lines.append(f"X // all {picks.size} spectra" if picks.size == total
                 else f"X // {picks.size} of {total} spectra, evenly spaced")
    lines.append(f"X // t = {times[0]:g} to {times[-1]:g} s, "
                 f"colour runs dark blue -> green -> yellow with time")

    lines.append(f"WAVES/D/N=({wl.size})\t{wl_wave}")
    lines.append("BEGIN")
    lines.extend("\t" + _format(v) for v in wl)
    lines.append("END")

    # The times get a wave of their own: with one 2-D wave there is nowhere else to put
    # them, and without them the columns are anonymous.
    lines.append(f"WAVES/D/N=({times.size})\t{t_wave}")
    lines.append("BEGIN")
    lines.extend("\t" + _format(v) for v in times)
    lines.append("END")

    lines.append(f"WAVES/D/N=({wl.size},{times.size})\t{mat}")
    lines.append("BEGIN")
    for row in values:
        lines.append("\t" + "\t".join(
            "NaN" if not np.isfinite(v) else MATRIX_FORMAT % v for v in row))
    lines.append("END")

    # One command per column. Igor names the first trace after the wave and the rest
    # `wave#1`, `wave#2`, ..., which is what the colour commands below address.
    lines.append(f"X Display {mat}[][0] vs {wl_wave}")
    for j in range(1, times.size):
        lines.append(f"X AppendToGraph {mat}[][{j}] vs {wl_wave}")
    for j in range(times.size):
        trace = mat if j == 0 else f"{mat}#{j}"
        lines.append(f"X ModifyGraph rgb({trace})=({_ramp(j, times.size)})")
    lines.append("X ModifyGraph mirror(bottom)=1,mirror(left)=1")
    if xlabel:
        lines.append(f'X Label bottom "{_escape(xlabel)}"')
    if ylabel:
        lines.append(f'X Label left "{_escape(ylabel)}"')
    if title:
        lines.append(f'X DoWindow/T kwTopWin, "{_escape(title)}"')

    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return [wl_wave, t_wave, mat]
