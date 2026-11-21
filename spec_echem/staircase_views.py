"""
Whole-run views of a PITT or GITT h5: every step on one plot.

Requested 2026-11-20: one step at a time hides how the steps differ, so these put
them side by side. Pure data, no Qt; the PITT and GITT tabs draw them.

  - `step_traces`: each step's echem on its own clock (from its start) and on the
    run's clock (`time_potentiostat`, continuous across steps), so a timeline and
    an overlay are both one call;
  - `step_end_spectra`: the absorbance at each step's END, and the reference the
    changes are taken from (the first spectrum of the first step);
  - `band_wavelengths`: where the run's absorbance fell most and rose most, so the
    band traces follow the sample's own bands without a wavelength typed in.
"""
import numpy as np

# The ends of a detector's range are noise; the bands are looked for inside this
# fraction of the measured span, trimmed from each end.
EDGE_TRIM_FRACTION = 0.05
SMOOTH_POINTS = 9


def _steps_json(f):
    import json
    for key in ("pitt_steps_json", "gitt_steps_json"):
        if key in f.attrs:
            return json.loads(f.attrs[key])
    return []


def step_traces(path):
    """[{index, role, setpoint, t, t_run, e, i}] per step group, in run order.

    `setpoint` is the step's potential (PITT) or current (GITT); `t` is the step's
    own clock; `t_run` the run's, from `time_potentiostat` when the step has it and
    else the steps laid end to end.
    """
    import h5py
    out, run_end = [], 0.0
    with h5py.File(path, "r") as f:
        for row in _steps_json(f):
            key = str(int(row["index"]))
            if key not in f or "echem" not in f[key]:
                continue
            e = f[key]["echem"]
            t = np.asarray(e["time"], dtype=float)
            if "time_potentiostat" in e:
                t_run = np.asarray(e["time_potentiostat"], dtype=float)
            else:
                t_run = t + run_end
            if t_run.size:
                run_end = float(t_run[-1])
            setpoint = row.get("potential_set")
            if setpoint is None:
                setpoint = row.get("current_set_A")
            out.append(dict(index=int(row["index"]), role=row.get("role"),
                            setpoint=setpoint, t=t, t_run=t_run,
                            e=np.asarray(e["potential"], dtype=float),
                            i=np.asarray(e["current"], dtype=float)))
    return out


def step_end_spectra(path):
    """(wavelengths, reference, [(index, role, absorbance at the step's end)]).

    The reference is the first spectrum of the first step: the film before the
    titration moved it. None when the file has no spectra.
    """
    import h5py
    ends, reference = [], None
    with h5py.File(path, "r") as f:
        if "wavelength" not in f:
            return None, None, []
        wl = np.asarray(f["wavelength"], dtype=float)
        for row in _steps_json(f):
            key = str(int(row["index"]))
            if key not in f or "absorbance_vs_time" not in f[key]:
                continue
            a = np.asarray(f[key]["absorbance_vs_time"], dtype=float)
            if a.ndim != 2 or a.shape[1] == 0:
                continue
            if reference is None:
                reference = a[:, 0]
            ends.append((int(row["index"]), row.get("role"), a[:, -1]))
    return wl, reference, ends


def _smooth(y, n=SMOOTH_POINTS):
    if y.size < n:
        return y
    kernel = np.ones(n) / n
    return np.convolve(np.nan_to_num(y), kernel, mode="same")


def band_wavelengths(wavelengths, change):
    """(wavelength of the largest fall, of the largest rise) in `change`, or None
    for one that does not exist. The detector's edges are left out."""
    wl = np.asarray(wavelengths, dtype=float)
    d = _smooth(np.asarray(change, dtype=float))
    if wl.size < 3:
        return None, None
    trim = int(EDGE_TRIM_FRACTION * wl.size)
    inner = slice(trim, wl.size - trim) if wl.size - 2 * trim >= 3 else slice(None)
    w, d = wl[inner], d[inner]
    k_min, k_max = int(np.argmin(d)), int(np.argmax(d))
    fall = float(w[k_min]) if d[k_min] < 0 else None
    rise = float(w[k_max]) if d[k_max] > 0 else None
    return fall, rise


def absorbance_at(wavelengths, spectrum, wavelength):
    wl = np.asarray(wavelengths, dtype=float)
    return float(np.asarray(spectrum, dtype=float)[int(np.argmin(np.abs(wl - wavelength)))])
