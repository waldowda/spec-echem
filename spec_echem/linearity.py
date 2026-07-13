"""
Detector linearity check: find the integration time where the detector stops
responding linearly, and recommend a safe working value below it.

For a fixed light source the detector response is linear in integration time:

    counts = offset + k * t

where `offset` is the dark level. As counts approach the ADC full scale the
response compresses (rolls off) before hard-clipping, so the useful upper limit
sits *below* saturation. This module ramps the integration time, tracks a single
detector pixel, fits the linear region, and reports where the response departs
from that line.

Run with the reference solution in place and the lamp on: the check characterises
the detector under the light level you will actually measure at.

Pure / Qt-free / no vendor SDK — safe to unit-test anywhere.
"""
import numpy as np

FULL_SCALE_COUNTS = 65535   # 16-bit ADC
SATURATION_FRAC = 0.99      # counts at/above this fraction of full scale = saturated
MIN_FIT_POINTS = 4          # lowest-t points assumed linear, used to seed the fit
MAX_FILL_FRAC = 0.85        # keep the working peak at/below this fraction of full scale


class LinearityError(Exception):
    """The ramp cannot be interpreted (no signal, saturated at the start, ...)."""


def measure_linearity_series(spec, times, full_scale=FULL_SCALE_COUNTS,
                             peak_pixel=None, on_progress=None):
    """
    Ramp the integration time and record the response.

    The brightest pixel of the FIRST (lowest-t) spectrum is chosen and then
    tracked for the whole ramp, so the curve describes one detector element
    rather than a wandering argmax.

    Stops early once the spectrum saturates — there is nothing to learn past the
    clip. Restoring the caller's integration time / scan averages is the caller's
    job.

    Returns (times, counts, peak_pixel) — `times` truncated to what was measured.
    """
    times = [float(t) for t in times]
    if not times:
        raise LinearityError("No integration times to measure.")

    sat_level = full_scale * SATURATION_FRAC
    used, counts = [], []

    for i, t in enumerate(times):
        spec.set_integration_time(t)
        result = spec.measure()
        if result is None:
            break
        _, spectrum = result
        spectrum = np.asarray(spectrum, float)

        if peak_pixel is None:
            peak_pixel = int(np.argmax(spectrum))

        used.append(t)
        counts.append(float(spectrum[peak_pixel]))
        if on_progress is not None:
            on_progress(i + 1, len(times))

        # Saturated anywhere in the spectrum: past here the curve is just the clip.
        if float(spectrum.max()) >= sat_level:
            break

    return np.array(used), np.array(counts), peak_pixel


def analyze_linearity(times, counts, tolerance_pct=2.0, max_fill_frac=MAX_FILL_FRAC,
                      full_scale=FULL_SCALE_COUNTS, min_fit_points=MIN_FIT_POINTS):
    """
    Fit the linear region and find where the response departs from it.

    Seeds a line (with intercept — the detector's dark offset) on the lowest
    `min_fit_points` integration times, then grows the fit upward point by point
    while each new point stays within `tolerance_pct` of the line, refitting as it
    goes. The limit is the first point that falls more than `tolerance_pct` BELOW
    the fitted line (compression is always downward) or that saturates.

    TWO constraints set the recommendation, and the tighter one wins:

      1. 5% below the limit of linearity, and
      2. peak counts no higher than `max_fill_frac` of ADC full scale.

    The second is not redundant. On real hardware the detector can stay linear to
    within ~1% right up until it hard-clips, so the deviation test alone puts the
    "limit" at ~98% of full scale and leaves no headroom for lamp drift. The
    fill cap is what keeps the working point off the ceiling.

    Returns a dict:
        slope, offset        fitted counts = offset + slope * t
        t_limit              last integration time still linear (None if never departed)
        counts_limit         counts there
        t_recommended        the tighter of the two constraints above
        counts_recommended   predicted peak counts there
        bound_by             "linearity" or "fill" — which constraint decided
        t_fill               integration time that reaches max_fill_frac of full scale
        saturated            whether the ramp reached the ADC clip
        limit_found          whether a departure from linearity was actually seen
        n_fit                points included in the final linear fit
        summary              human-readable rationale
    """
    times = np.asarray(times, float)
    counts = np.asarray(counts, float)
    if len(times) != len(counts):
        raise LinearityError("times and counts must be the same length.")
    if len(times) == 0:
        raise LinearityError("No measurements to analyze.")

    order = np.argsort(times)
    times, counts = times[order], counts[order]

    sat_level = full_scale * SATURATION_FRAC
    saturated = bool(counts.max() >= sat_level)

    # Check this BEFORE the point-count guard: a ramp that saturates immediately
    # stops after one point, and "increase Steps" would be the wrong advice.
    if counts[0] >= sat_level:
        raise LinearityError(
            f"Already saturated at the lowest integration time ({times[0]:.4g} ms, "
            f"{counts[0]:.0f} counts). Lower Start, or attenuate the light "
            "(neutral-density filter).")

    if len(times) < min_fit_points + 1:
        raise LinearityError(
            f"Need at least {min_fit_points + 1} points to fit and test; got {len(times)}. "
            "Increase Steps, or widen Start/Stop.")

    span = float(counts.max() - counts.min())
    if span < 0.01 * full_scale:
        raise LinearityError(
            f"No response to integration time (counts vary by only {span:.0f}). "
            "Is the lamp on and the reference in place?")

    # Seed the fit on the lowest-t points, which are safely in the linear region.
    n_fit = min_fit_points
    slope, offset = np.polyfit(times[:n_fit], counts[:n_fit], 1)

    tol = tolerance_pct / 100.0
    limit_idx = None
    for i in range(n_fit, len(times)):
        predicted = offset + slope * times[i]
        # Only downward departure means compression; upward is noise.
        deviation = (counts[i] - predicted) / predicted if predicted > 0 else 0.0
        if deviation < -tol or counts[i] >= sat_level:
            limit_idx = i
            break
        # Still linear — absorb it and refit, tightening the line as we climb.
        n_fit = i + 1
        slope, offset = np.polyfit(times[:n_fit], counts[:n_fit], 1)

    # --- The two constraints on a safe working integration time ---
    # (1) 5% below the limit of linearity. If no departure was seen, the highest
    #     time we actually tested is the best evidence we have.
    limit_found = limit_idx is not None
    t_last_linear = float(times[limit_idx - 1]) if limit_found else float(times[-1])
    t_from_linearity = 0.95 * t_last_linear

    # (2) Keep the peak below max_fill_frac of full scale. Extrapolated from the fit,
    #     which is the *linear* response — exactly what we want the working point on.
    fill_counts = max_fill_frac * full_scale
    t_fill = (fill_counts - offset) / slope if slope > 0 else float("inf")

    if t_fill < t_from_linearity:
        t_recommended, bound_by = t_fill, "fill"
    else:
        t_recommended, bound_by = t_from_linearity, "linearity"

    if t_recommended <= 0:
        raise LinearityError(
            "Cannot recommend an integration time — the fitted response is degenerate.")

    counts_recommended = offset + slope * t_recommended
    t_recommended = round(t_recommended, 6)

    if bound_by == "fill":
        why_rec = (f"held to {max_fill_frac * 100:.0f}% of full scale "
                   f"({counts_recommended:.0f} counts) — the detector stays linear "
                   f"nearly to the clip, so fill is the binding constraint, not linearity")
    else:
        why_rec = (f"5% below the limit of linearity ({counts_recommended:.0f} counts, "
                   f"{counts_recommended / full_scale * 100:.0f}% of full scale)")

    common = {
        "slope": float(slope), "offset": float(offset),
        "t_recommended": t_recommended,
        "counts_recommended": float(counts_recommended),
        "bound_by": bound_by,
        "t_fill": float(t_fill),
        "saturated": saturated, "n_fit": n_fit,
    }

    if not limit_found:
        common.update({
            "t_limit": None, "counts_limit": float(counts[-1]), "limit_found": False,
            "summary": (
                f"Linear across the whole tested range (up to {t_last_linear:.4g} ms, "
                f"{counts[-1]:.0f} counts) — no departure > {tolerance_pct:.1f}% found; "
                f"increase Stop to find the true limit. "
                f"Fit: counts = {offset:.0f} + {slope:.0f}·t (over {n_fit} points). "
                f"Recommended: {t_recommended:.4g} ms — {why_rec}."),
        })
        return common

    counts_limit = float(counts[limit_idx - 1])
    predicted_at_limit = offset + slope * times[limit_idx]
    why_limit = ("saturated" if counts[limit_idx] >= sat_level else
                 f"fell {abs((counts[limit_idx] - predicted_at_limit) / predicted_at_limit) * 100:.1f}% "
                 "below the fit")
    common.update({
        "t_limit": t_last_linear, "counts_limit": counts_limit, "limit_found": True,
        "summary": (
            f"Linear to {t_last_linear:.4g} ms ({counts_limit:.0f} counts, "
            f"{counts_limit / full_scale * 100:.0f}% of full scale); at "
            f"{times[limit_idx]:.4g} ms the response {why_limit}. "
            f"Fit: counts = {offset:.0f} + {slope:.0f}·t (over {n_fit} points). "
            f"Recommended: {t_recommended:.4g} ms — {why_rec}."),
    })
    return common


def find_saturation_time(spec, start, max_time=10000.0, full_scale=FULL_SCALE_COUNTS,
                         max_steps=20, bisect_steps=6):
    """
    Find where the detector saturates: double the integration time until it clips,
    then bisect the bracket to pin the threshold.

    Doubling alone overshoots badly — it can only ever report a power-of-two
    multiple of `start`, which says little about where saturation actually is
    (e.g. it reports 0.176 ms when the true threshold is 0.111 ms). The bisection
    is what makes the answer meaningful.

    Returns a dict: t_sat (lowest time seen to saturate), t_below (highest time
    seen NOT to saturate), counts_below (peak counts there).
    """
    sat_level = full_scale * SATURATION_FRAC

    def peak_at(t):
        spec.set_integration_time(t)
        result = spec.measure()
        if result is None:
            raise LinearityError("Measurement aborted.")
        _, spectrum = result
        return float(np.asarray(spectrum, float).max())

    t = float(start)
    if t <= 0:
        raise LinearityError("Start integration time must be > 0.")

    peak = peak_at(t)
    if peak >= sat_level:
        raise LinearityError(
            f"Already saturated at Start ({t:.4g} ms, {peak:.0f} counts). "
            "Lower Start, or attenuate the light.")

    # Double until it clips, keeping the last unsaturated time as the lower bracket.
    lo, counts_lo, hi = t, peak, None
    for _ in range(max_steps):
        if t >= max_time:
            break
        t = min(t * 2.0, max_time)
        peak = peak_at(t)
        if peak >= sat_level:
            hi = t
            break
        lo, counts_lo = t, peak

    if hi is None:
        raise LinearityError(
            f"No saturation up to {t:.4g} ms. The light may be too dim — remove "
            "attenuation, or set Stop by hand.")

    # Bisect the [lo, hi] bracket to pin the threshold.
    for _ in range(bisect_steps):
        mid = 0.5 * (lo + hi)
        peak = peak_at(mid)
        if peak >= sat_level:
            hi = mid
        else:
            lo, counts_lo = mid, peak

    return {"t_sat": float(hi), "t_below": float(lo), "counts_below": float(counts_lo)}
