"""
Recommend a usable wavelength window from a test-absorbance spectrum.

The halogen lamp fades toward the spectrum edges, so absorbance there is
noise-dominated. On a NO-SAMPLE (or reference-vs-reference) test, A ≈ 0
everywhere, so the deviation from zero *is* the noise. This finds the central
low-noise band and suggests trimming to it, with an explainable rationale the
user can accept or override.

A no-sample test is the worst case: with a real sample the absorbance signal is
much larger, so the edges become usable further out than a blank suggests. The
suggestion is therefore a conservative floor — widen it with `noise_mult`.

Pure / Qt-free / no vendor SDK — safe to unit-test anywhere.
"""
import numpy as np


def _rolling_std(y, win):
    """Centered rolling standard deviation, same length as y (edges use a
    shorter window). win is in samples; clamped to >= 3."""
    y = np.asarray(y, float)
    n = len(y)
    win = max(3, int(win))
    half = win // 2
    out = np.empty(n)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = np.std(y[lo:hi])
    return out


def recommend_wavelength_range(wavelengths, test_abs, dark=None, ref=None,
                               noise_mult=3.0, win=15, ref_frac=0.05):
    """
    Suggest a usable [wl_start, wl_stop] (nm) from a test-absorbance spectrum.

    Args:
        wavelengths: 1D array of wavelengths (nm), ascending.
        test_abs: 1D absorbance from a no-sample / reference test (A≈0, so any
            deviation from zero is noise).
        dark, ref: optional raw dark/reference spectra for a lamp-brightness
            corroboration (where ref-net drops below ref_frac of its peak).
        noise_mult: the conservative<->liberal knob. Keep wavelengths whose local
            noise <= noise_mult * the plateau noise. Higher keeps more (trims less).
        win: rolling-std window in samples.
        ref_frac: reference-net threshold as a fraction of its peak (corroboration).

    Returns:
        (wl_start, wl_stop, rationale): rationale is a dict of the numbers behind
        the suggestion plus a human-readable 'summary'.
    """
    wl = np.asarray(wavelengths, float)
    a = np.asarray(test_abs, float)
    n = len(wl)
    if n == 0:
        raise ValueError("empty wavelength array")

    sigma = _rolling_std(a, win)
    # Plateau noise = the quiet central region; a low percentile is robust to the
    # noisy edges dominating a plain mean/min.
    positive = sigma[sigma > 0]
    plateau = float(np.percentile(sigma, 10)) if n > 1 else float(sigma[0])
    if plateau <= 0:
        plateau = float(positive.min()) if positive.size else 0.0
    threshold = noise_mult * plateau

    # Grow outward from the quietest point while under threshold — the contiguous
    # low-noise band around the plateau.
    center = int(np.argmin(sigma))
    lo = center
    while lo - 1 >= 0 and sigma[lo - 1] <= threshold:
        lo -= 1
    hi = center
    while hi + 1 < n and sigma[hi + 1] <= threshold:
        hi += 1

    wl_start, wl_stop = float(wl[lo]), float(wl[hi])

    rationale = {
        "plateau_sigma": plateau,
        "noise_mult": float(noise_mult),
        "threshold_sigma": threshold,
        "wl_start": wl_start,
        "wl_stop": wl_stop,
        "sigma_low_edge": float(sigma[0]),
        "sigma_high_edge": float(sigma[-1]),
        "ref_edges": None,
    }

    # Optional lamp-brightness corroboration (the physical cause of the edge noise).
    if dark is not None and ref is not None:
        net = np.asarray(ref, float) - np.asarray(dark, float)
        peak = float(np.max(net)) if net.size else 0.0
        if peak > 0:
            idx = np.where(net >= ref_frac * peak)[0]
            if idx.size:
                rationale["ref_edges"] = (float(wl[idx[0]]), float(wl[idx[-1]]))
        rationale["ref_frac"] = ref_frac
        rationale["ref_peak"] = peak

    summary = (f"Noise floor σ≈{plateau:.4f}. Suggested {wl_start:.0f}–{wl_stop:.0f} nm "
               f"(within {noise_mult:g}× the floor; the current edges reach "
               f"σ≈{sigma[0]:.3f} / {sigma[-1]:.3f}).")
    if rationale["ref_edges"] is not None:
        lo_e, hi_e = rationale["ref_edges"]
        summary += (f" Reference lamp ≥{ref_frac:.0%} of peak over "
                    f"{lo_e:.0f}–{hi_e:.0f} nm — consistent.")
    rationale["summary"] = summary

    return wl_start, wl_stop, rationale
