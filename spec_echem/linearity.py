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


def analyze_linearity(times, counts, tolerance_pct=2.0,
                      full_scale=FULL_SCALE_COUNTS, min_fit_points=MIN_FIT_POINTS):
    """
    Fit the linear region and find where the response departs from it.

    Seeds a line (with intercept — the detector's dark offset) on the lowest
    `min_fit_points` integration times, then grows the fit upward point by point
    while each new point stays within `tolerance_pct` of the line, refitting as it
    goes. The limit is the first point that falls more than `tolerance_pct` BELOW
    the fitted line (compression is always downward) or that saturates.

    Returns a dict:
        slope, offset        fitted counts = offset + slope * t
        t_limit              last integration time still linear (None if never departed)
        counts_limit         counts there
        t_recommended        0.95 * t_limit  (5% below the limit of linearity)
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

    if limit_idx is None:
        # Never departed: the whole tested range is linear.
        t_limit = float(times[-1])
        return {
            "slope": float(slope), "offset": float(offset),
            "t_limit": None, "counts_limit": float(counts[-1]),
            "t_recommended": round(0.95 * t_limit, 6),
            "saturated": saturated, "limit_found": False, "n_fit": n_fit,
            "summary": (
                f"Linear across the whole tested range (up to {t_limit:.4g} ms, "
                f"{counts[-1]:.0f} counts) — no departure > {tolerance_pct:.1f}% found. "
                f"Increase Stop to find the actual limit. "
                f"Recommended for now: {0.95 * t_limit:.4g} ms."),
        }

    # The last point still linear is the one before the departure.
    t_limit = float(times[limit_idx - 1])
    counts_limit = float(counts[limit_idx - 1])
    t_recommended = round(0.95 * t_limit, 6)
    why = ("saturated" if counts[limit_idx] >= sat_level
           else f"fell {abs((counts[limit_idx] - (offset + slope * times[limit_idx])) / (offset + slope * times[limit_idx])) * 100:.1f}% below the fit")

    return {
        "slope": float(slope), "offset": float(offset),
        "t_limit": t_limit, "counts_limit": counts_limit,
        "t_recommended": t_recommended,
        "saturated": saturated, "limit_found": True, "n_fit": n_fit,
        "summary": (
            f"Linear to {t_limit:.4g} ms ({counts_limit:.0f} counts); at "
            f"{times[limit_idx]:.4g} ms the response {why}. "
            f"Fit: counts = {offset:.0f} + {slope:.0f}·t (over {n_fit} points). "
            f"Recommended: {t_recommended:.4g} ms (5% below the limit)."),
    }


def find_saturation_time(spec, start, max_time=10000.0,
                         full_scale=FULL_SCALE_COUNTS, max_steps=20):
    """
    Bracket saturation by doubling the integration time from `start`.

    Returns the first integration time whose spectrum saturates — a sensible
    upper bound (Stop) for the ramp. Raises if saturation is never reached by
    `max_time`, or if `start` already saturates.
    """
    sat_level = full_scale * SATURATION_FRAC
    t = float(start)
    if t <= 0:
        raise LinearityError("Start integration time must be > 0.")

    for _ in range(max_steps):
        spec.set_integration_time(t)
        result = spec.measure()
        if result is None:
            raise LinearityError("Measurement aborted.")
        _, spectrum = result
        if float(np.asarray(spectrum, float).max()) >= sat_level:
            return t
        if t >= max_time:
            break
        t = min(t * 2.0, max_time)

    raise LinearityError(
        f"No saturation up to {t:.4g} ms. The light may be too dim — remove "
        "attenuation, or set Stop by hand.")
