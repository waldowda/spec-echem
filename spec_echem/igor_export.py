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
              xlabel=None, ylabel=None):
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
            for name in ys:
                is_fit = name.lower().endswith("_fit") or name.lower() == "fit"
                lines.append(f"X ModifyGraph mode({name})={0 if is_fit else 3}"
                             + ("" if is_fit else f",marker({name})=19,msize({name})=2"))
            # Axis labels are DATA, not decoration: a bare number axis makes the
            # reader guess at seconds versus nanometres.
            if xlabel:
                lines.append(f'X Label bottom "{_escape(xlabel)}"')
            if ylabel:
                lines.append(f'X Label left "{_escape(ylabel)}"')
            if title:
                lines.append(
                    f'X TextBox/C/N=title/F=0/A=MT "{_escape(title)}"')

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
    if prefix:
        stem = wave_name(prefix)[:MAX_PREFIX].rstrip("_")
        waves = {f"{stem}_{c}": frame[c].to_numpy() for c in numeric}
        x, ys = f"{stem}_{x}", [f"{stem}_{y}" for y in ys]
    else:
        waves = {c: frame[c].to_numpy() for c in numeric}
    return write_itx(path, waves, display=(x, ys), title=title, notes=notes,
                     xlabel=xlabel, ylabel=ylabel)
