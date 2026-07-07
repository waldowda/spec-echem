"""
Inspect the ACTUAL per-spectrum timing of a run from its saved spectra file(s).

The 8-column spectra output stores a per-spectrum timestamp ("Corrected time (s)",
column 8), repeated across the 1265 wavelength rows of each spectrum. This tool pulls
one timestamp per spectrum and shows the interval to the previous spectrum vs spectrum
number — so you can SEE where timing jitter happens (e.g. compare a live-plot run
against a plot-off run) instead of trusting only the summary stats in the log.

No hardware, no repo imports — just reads the saved .txt with pandas.

Usage:
    python examples/plot_spectra_timing.py RUN_OR_FILE [RUN_OR_FILE ...]

Each argument may be a spectra .txt OR a run folder (in which case every
`*spectra*.txt` inside it — CV / doping / dedoping / pre-dedoping — is used). So
    python examples/plot_spectra_timing.py RUN_live RUN_off
compares two whole runs.

For each file it prints a per-spectrum table + summary and writes `<file>_timing.csv`.
Given 2+ files it also saves one comparison plot (`spectra_timing.png`,
interval-vs-spectrum overlaid) next to the first — the direct way to compare
live-plot ON vs OFF.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # file output only; no display needed
import matplotlib.pyplot as plt

CORR_COL = "Corrected time (s)"


def per_spectrum_times(path):
    """Return (spectrum_number, corrected_time[s]) — one entry per spectrum.

    Each spectrum repeats its Corrected time across its wavelength rows, so the
    ordered unique values are exactly the per-spectrum timestamps. Using the time
    column (not 'Spectrum number'/'Index') keeps this valid for CV *and* chrono
    files, whose 6th column header differs."""
    df = pd.read_csv(path, sep="\t")
    if CORR_COL not in df.columns:
        raise ValueError(f"{path}: no '{CORR_COL}' column; got {list(df.columns)}")
    # Drop the NaNs first: the golden format leaves one row per spectrum (the last
    # wavelength) without a timestamp, so dropna() then ordered-unique gives exactly
    # the per-spectrum times.
    times = np.asarray(pd.unique(df[CORR_COL].dropna()), dtype=float)
    return np.arange(len(times)), times


def report(path):
    nums, times = per_spectrum_times(path)
    intervals = np.diff(times) * 1000.0   # ms
    print(f"\n== {path} ==")
    print(f"{len(times)} spectra; corrected time {times[0]:.3f} -> {times[-1]:.3f} s")
    if len(intervals):
        print(f"interval (ms): mean {intervals.mean():.1f}  min {intervals.min():.1f}  "
              f"max {intervals.max():.1f}  jitter(sd) {intervals.std():.1f}")
    print(f"{'spec#':>6} {'time(s)':>10} {'interval(ms)':>13}")
    for i, (n, t) in enumerate(zip(nums, times)):
        iv = "" if i == 0 else f"{intervals[i - 1]:.1f}"
        print(f"{int(n):>6} {t:>10.3f} {iv:>13}")

    # Viewable table alongside the data
    csv = Path(path).with_name(Path(path).stem + "_timing.csv")
    out = pd.DataFrame({
        "spectrum": nums,
        "corrected_time_s": times,
        "interval_ms": np.concatenate([[np.nan], intervals]) if len(intervals) else [np.nan],
    })
    out.to_csv(csv, index=False)
    print(f"wrote table: {csv}")
    return nums, times


def resolve(args):
    """Expand each argument to spectra file(s): a file stays as-is; a run folder
    yields every *spectra*.txt inside it (CV/doping/dedoping/pre-dedoping)."""
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            found = sorted(p.glob("*spectra*.txt"))
            if not found:
                print(f"(no spectra .txt files in {p})")
            files.extend(found)
        else:
            files.append(p)
    return files


def main(args):
    paths = resolve(args)
    if not paths:
        print("No spectra files found. Pass a spectra .txt or a run folder.")
        return
    have_plot = False
    plt.figure(figsize=(9, 4))
    for p in paths:
        nums, times = report(p)
        if len(times) > 1:
            plt.plot(nums[1:], np.diff(times) * 1000.0, marker=".", ms=4,
                     label=f"{Path(p).parent.name}/{Path(p).name}")
            have_plot = True
    if have_plot:
        target = None  # a horizontal line at the median helps the eye
        plt.axhline(np.median([np.diff(per_spectrum_times(p)[1]).mean() * 1000
                               for p in paths if len(per_spectrum_times(p)[1]) > 1]),
                    color="#888", ls="--", lw=0.8, label="mean")
        plt.xlabel("Spectrum number")
        plt.ylabel("Interval to previous spectrum (ms)")
        plt.title("Per-spectrum interval (spikes = timing jitter)")
        plt.grid(True, alpha=0.3)
        plt.legend(fontsize=8)
        out = Path(paths[0]).with_name("spectra_timing.png")
        plt.tight_layout()
        plt.savefig(out, dpi=150)
        print(f"\nsaved comparison plot: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
