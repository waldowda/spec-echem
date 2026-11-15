"""Impedance sweeps from a PITT with the impedance box ticked: the model-free
estimators and the Randles fit. No Qt, no hardware; Python 3.7.

The first part of the PITT / ER-EIS analysis design (2026-11-14). Everything here is
validated on the Randles dummy (200 ohm + 3010 ohm || 1 uF) and on synthetic data; the
film models (transmission line, ladder) and the PITT side come later.

Why C'(w) and not -1/(w Z''): with any DC path behind the series resistance (a
charge-transfer resistance, a leak), -1/(w Z'') diverges at low frequency -- 3150 uF
at 1 Hz on the 1 uF dummy. C'(w) = Im[1/(Z - Rs)]/w is the capacitance of what lies
behind Rs: for R || C it equals C at every frequency, and for any RC network it falls
with frequency towards the total capacitance, so at the lowest frequency it is a
LOWER BOUND unless the plateau is reached.

The software raises concerns; it never withholds a number. Every function returns
its values whatever it concludes, with the reasons beside them.
"""
import math

import numpy as np

# The instrument's accuracy found on the Randles dummy (2026-10-23, Reference 600):
# |Z| within ~0.5%, phase within ~0.1 deg. Used for the uncertainty of C'.
Z_MOD_REL_U = 0.005
Z_PHASE_U_DEG = 0.1

# d ln C' / d ln f over the lowest three points: within this, the plateau is reached
# (C' within ~5% of its limit).
PLATEAU_SLOPE_TOL = 0.1
# Phase of Z at the highest frequency beyond this: the high-frequency limit was not
# reached, and Re Z there is an upper bound on Rs.
HF_PHASE_LIMIT_DEG = 10.0
# Phase of Z - Rs at the lowest frequency inside this: C' rests on a small angle.
LF_RESISTIVE_PHASE_DEG = 5.0
# A fitted parameter with a relative sd above this is "not determined by this sweep".
UNDETERMINED_REL_SD = 0.5
# A fit whose rms misfit (|dZ|/|Z|) exceeds this "misses the data".
MISFIT_REL_RMS = 0.02


def z_randles(f, rs, rct, cdl):
    """Rs + (Rct || Cdl), complex ohms."""
    w = 2.0 * np.pi * np.asarray(f, dtype=float)
    return rs + rct / (1.0 + 1j * w * rct * cdl)


# name -> (function, parameter names). The film models join this table later.
MODELS = {
    "randles": (z_randles, ("rs", "rct", "cdl")),
}


def sweep_arrays(frame):
    """(f, Z) from one step's impedance frame (data.read_impedance_h5): the MEASURED
    frequency `zfreq` (it differs from `frequency_set` by up to 0.9%), only rows the
    instrument marked ok, finite values only."""
    f = np.asarray(frame["zfreq"], dtype=float)
    z = (np.asarray(frame["zreal"], dtype=float)
         + 1j * np.asarray(frame["zimag"], dtype=float))
    keep = np.isfinite(f) & (f > 0) & np.isfinite(z.real) & np.isfinite(z.imag)
    if "ok" in frame:
        keep &= np.asarray(frame["ok"]).astype(bool)
    return f[keep], z[keep]


def series_resistance(f, z):
    """(Rs, phase in degrees) from the highest frequency. A phase beyond
    HF_PHASE_LIMIT_DEG means the high-frequency limit was not reached, and Rs is an
    upper bound."""
    k = int(np.argmax(f))
    return float(z[k].real), float(np.degrees(np.angle(z[k])))


def capacitance_spectrum(f, z, rs):
    """(C', C_series) in farads at each frequency: C' = Im[1/(Z - Rs)]/w, the
    capacitance behind Rs; C_series = -1/(w Z''), for reference only (it diverges
    with any DC path)."""
    w = 2.0 * np.pi * np.asarray(f, dtype=float)
    z = np.asarray(z, dtype=complex)
    with np.errstate(divide="ignore", invalid="ignore"):
        c_parallel = (1.0 / (z - rs)).imag / w
        c_series = -1.0 / (w * z.imag)
    return c_parallel, c_series


def capacitance_u(f, z, rs, rel_mod=Z_MOD_REL_U, phase_deg=Z_PHASE_U_DEG):
    """Relative 1-sd uncertainty of C' from the instrument's |Z| and phase accuracy.

    Linear propagation through C' = Im[1/W]/w, W = Z - Rs: an error Z(e + j*d) in Z
    (e relative in |Z|, d in radians of phase) moves 1/W by -Z(e + j*d)/W^2, so
    dC'/C' = e*Im[-Z/W^2]/Im[1/W] + d*Im[-jZ/W^2]/Im[1/W], the two in quadrature.
    Large where the phase of W is small: C' then rests on a small angle.
    (A closed form in |Z|/|W| and tan(phase) overstated it 2.5x at high frequency
    against a Monte Carlo, 2026-11-14.)"""
    z = np.asarray(z, dtype=complex)
    w2 = (z - rs) ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        base = (1.0 / (z - rs)).imag
        g_mod = (-z / w2).imag / base
        g_phase = (-1j * z / w2).imag / base
    return np.hypot(rel_mod * g_mod, np.radians(phase_deg) * g_phase)


def low_frequency_capacitance(f, z, rs, n_low=3, slope_tol=PLATEAU_SLOPE_TOL):
    """C' at the lowest frequency, and whether its plateau was reached.

    -> dict: c_lf (F), f_lf (Hz), slope (d ln C'/d ln f over the lowest n_low
    points), reached, c_lf_u (relative 1 sd), phase_deg (of Z - Rs there), concerns.
    Not reached: c_lf is a LOWER BOUND, and says so.
    """
    f = np.asarray(f, dtype=float)
    z = np.asarray(z, dtype=complex)
    order = np.argsort(f)
    cp, _cs = capacitance_spectrum(f, z, rs)
    low = order[:n_low]
    k = order[0]
    good = np.isfinite(cp[low]) & (cp[low] > 0)
    slope = (float(np.polyfit(np.log(f[low][good]), np.log(cp[low][good]), 1)[0])
             if good.sum() >= 2 else float("nan"))
    reached = bool(math.isfinite(slope) and abs(slope) <= slope_tol)
    phase = float(np.degrees(np.angle(z[k] - rs)))
    u = float(capacitance_u(f[k:k + 1], z[k:k + 1], rs)[0])
    concerns = []
    if not reached:
        concerns.append(
            "low-frequency plateau not reached (slope %s over the lowest %d "
            "frequencies): C' = %.4g F at %.3g Hz is a lower bound"
            % ("undefined" if not math.isfinite(slope) else "%.2f" % slope,
               n_low, float(cp[k]), float(f[k])))
    if abs(phase) < LF_RESISTIVE_PHASE_DEG:
        concerns.append(
            "resistive at %.3g Hz (phase of Z - Rs %.2f deg): C' rests on a small "
            "angle, +/- %.0f%%" % (float(f[k]), phase, 100 * u))
    return {"c_lf": float(cp[k]), "f_lf": float(f[k]), "slope": slope,
            "reached": reached, "c_lf_u": u, "phase_deg": phase,
            "concerns": concerns}


def high_frequency_capacitance(f, z, rs):
    """A model-free Cdl: the flattest three-point stretch of C' on the high-frequency
    side (smallest |d ln C'/d ln f|). -> dict cdl (F), slope, f (Hz, its centre)."""
    f = np.asarray(f, dtype=float)
    order = np.argsort(f)[::-1]
    cp, _cs = capacitance_spectrum(f, z, rs)
    ff, cc = f[order], cp[order]
    best = None
    for k in range(len(ff) - 2):
        seg = slice(k, k + 3)
        if not np.all(np.isfinite(cc[seg]) & (cc[seg] > 0)):
            continue
        s = abs(float(np.polyfit(np.log(ff[seg]), np.log(cc[seg]), 1)[0]))
        if best is None or s < best[0]:
            best = (s, float(np.exp(np.mean(np.log(cc[seg])))), float(ff[k + 1]))
    if best is None:
        return {"cdl": float("nan"), "slope": float("nan"), "f": float("nan")}
    return {"cdl": best[1], "slope": best[0], "f": best[2]}


def _randles_guess(f, z):
    rs, _phase = series_resistance(f, z)
    rs = max(0.98 * rs, 1e-3)
    rdc = float(z[int(np.argmin(f))].real)
    cp, _cs = capacitance_spectrum(f, z, rs)
    finite = cp[np.isfinite(cp) & (cp > 0)]
    cdl = float(np.median(finite)) if finite.size else 1e-6
    return [rs, max(rdc - rs, 1.0), cdl]


_GUESSES = {"randles": _randles_guess}


def fit_impedance(f, z, model="randles", p0=None):
    """Complex non-linear least squares of one model to one sweep.

    Residuals (Z_fit - Z)/|Z|, real and imaginary parts, in log-parameters so every
    parameter stays positive and the scales (ohms to microfarads) do not matter;
    seven starts, the best kept. The covariance is scaled by the reduced chi-square.

    -> dict: model, status ("ok" | "needs_review" | "did_not_converge"), params and
    sd (name -> value, 1 sd), rel_rms, aicc, n_points, concerns, curve_z. Never
    raises; parameters are returned whatever the concerns.
    """
    from scipy.optimize import least_squares

    f = np.asarray(f, dtype=float)
    z = np.asarray(z, dtype=complex)
    func, names = MODELS[model]
    keep = np.isfinite(f) & (f > 0) & np.isfinite(z.real) & np.isfinite(z.imag)
    f, z = f[keep], z[keep]
    n, k = 2 * f.size, len(names)
    if n <= k + 1:
        return {"model": model, "status": "did_not_converge", "concerns": [],
                "reason": "%d frequencies for %d parameters" % (f.size, k)}
    p0 = np.asarray(_GUESSES[model](f, z) if p0 is None else p0, dtype=float)
    weight = np.abs(z)

    def residual(log_p):
        d = (func(f, *np.exp(log_p)) - z) / weight
        return np.concatenate([d.real, d.imag])

    rng = np.random.RandomState(0)
    starts = [np.log(p0)] + [np.log(p0) + rng.normal(0.0, 1.0, k) for _ in range(6)]
    best = None
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        for start in starts:
            try:
                r = least_squares(residual, start, method="trf", bounds=(-34, 34),
                                  max_nfev=4000)
            except Exception:  # noqa: BLE001 -- one bad start is not a failed fit
                continue
            if np.all(np.isfinite(r.x)) and (best is None or r.cost < best.cost):
                best = r
    if best is None:
        return {"model": model, "status": "did_not_converge", "concerns": [],
                "reason": "no start converged"}

    ssr = 2.0 * best.cost
    jtj = best.jac.T @ best.jac
    # An undetermined parameter makes J'J singular; a tiny ridge keeps the inverse
    # finite, so determined parameters keep their sd and the others get a huge one.
    ridge = 1e-12 * np.trace(jtj) / k
    cov = np.linalg.inv(jtj + ridge * np.eye(k)) * (ssr / (n - k))
    rel_sd = np.sqrt(np.abs(np.diag(cov)))          # sd of ln p = relative sd
    p = np.exp(best.x)
    concerns = []
    for name, rsd, log_p in zip(names, rel_sd, best.x):
        if abs(log_p) > 33.5:
            concerns.append("%s is at its bound: not determined by this sweep" % name)
        elif not math.isfinite(rsd) or rsd > UNDETERMINED_REL_SD:
            concerns.append("%s is uncertain by %s: not determined by this sweep"
                            % (name, "%.0f%%" % (100 * rsd) if rsd < 10
                               else "more than 1000%"))
    rel_rms = float(math.sqrt(ssr / n))
    if rel_rms > MISFIT_REL_RMS:
        concerns.append("the model misses the data by %.1f%% rms" % (100 * rel_rms))
    aicc = n * math.log(max(ssr, 1e-300) / n) + 2 * k + 2.0 * k * (k + 1) / max(n - k - 1, 1)
    return {"model": model, "status": "needs_review" if concerns else "ok",
            "params": dict(zip(names, (float(v) for v in p))),
            "sd": dict(zip(names, (float(v) for v in p * rel_sd))),
            "rel_rms": rel_rms, "aicc": float(aicc), "n_points": int(f.size),
            "concerns": concerns, "curve_z": func(f, *p)}


def analyse_sweep(frame):
    """Everything model-free about one step's sweep, plus the Randles fit.

    -> dict: f, z, rs, rs_phase_deg, c_parallel, c_series, c_u, low (see
    low_frequency_capacitance), high (see high_frequency_capacitance), randles (see
    fit_impedance), concerns (sweep-level). None when the frame has no usable rows.
    """
    f, z = sweep_arrays(frame)
    if f.size < 3:
        return None
    rs, phase = series_resistance(f, z)
    concerns = []
    n_bad = int(len(frame) - f.size)
    if n_bad:
        concerns.append("%d frequenc%s without a value" % (n_bad, "y" if n_bad == 1 else "ies"))
    if abs(phase) > HF_PHASE_LIMIT_DEG:
        concerns.append("high-frequency limit not reached (phase %.1f deg at %.3g Hz): "
                        "Rs = %.4g ohm is an upper bound" % (phase, float(f.max()), rs))
    cp, cs = capacitance_spectrum(f, z, rs)
    return {"f": f, "z": z, "rs": rs, "rs_phase_deg": phase,
            "c_parallel": cp, "c_series": cs, "c_u": capacitance_u(f, z, rs),
            "low": low_frequency_capacitance(f, z, rs),
            "high": high_frequency_capacitance(f, z, rs),
            "randles": fit_impedance(f, z, "randles"),
            "concerns": concerns}
