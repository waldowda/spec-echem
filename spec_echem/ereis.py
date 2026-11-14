"""
ER-EIS -- an impedance sweep after a PITT step's hold, read electrically and
optically.

Not a staircase of its own: a box on the PITT (spec_echem/pitt.py). When
`pitt_eis_enabled` is ticked, each chosen step runs as usual -- its hold, its end
rule, its spectra -- and once the hold has ended normally a frequency sweep runs from
high to low at the same DC potential, with the cell left on and the spectra carrying
on at the slow rate (acquisition.acquire_pitt). The hold then gives the PITT's dQ/dE
and the sweep the impedance, at the same potential on the same film: the low-frequency
capacitance gives the chemical capacitance, independently of dQ/dE, and the higher
frequencies separate the double layer from it. The PITT's return leg sweeps the same
potentials again on the way back, which is the drift and hysteresis check.

Pure logic only: which steps sweep, the frequency list, the time estimate and the
Start checks.

Gamry only for now: the Autolab's FRA32M failed NOVA's own test (2026-10-26) and is
awaiting repair. The loop and the file are not Gamry-specific, so an Autolab driver
can be added once its FRA works.
"""
import math

from spec_echem.pitt import ROLE_DEDOPE, pitt_plan

# How the frequency sweep of a step ended. Recorded per swept step as `sweep_end`.
SWEEP_COMPLETE = "complete"
SWEEP_STOPPED = "stopped"       # Stop pressed: the frequency in hand finished, no more
SWEEP_ERROR = "error"           # an exception ended it (and the run); its points are kept
SWEEP_ABORTED = "aborted"

# Seconds one frequency takes: about EREIS_POINT_BASE_S + EREIS_POINT_CYCLES / f.
# MEASURED on the Reference 600 with ReadZ (2026-10-23, the UDC4 Randles dummy,
# docs/bench-2026-10-26.md section 2): 0.31 s at 10 kHz-100 Hz, 0.46 s at 10 Hz, 3.09 s
# at 1 Hz. A film can need more cycles than a dummy at low frequency, so treat the
# estimate as a floor.
EREIS_POINT_BASE_S = 0.3
EREIS_POINT_CYCLES = 2.8

# The highest frequency each Gamry reaches (docs/bench-2026-10-26.md): 1 MHz on the
# Reference 600, 2 MHz on the Interface 1010E. The lower is the limit we check against.
EREIS_F_MAX_HZ = 1.0e6

_FLOAT_SLACK = 1e-9


def ereis_enabled(settings):
    """Is the PITT to sweep impedance? Only with the PITT itself ticked."""
    return bool(settings.get("pitt_enabled", False)
                and settings.get("pitt_eis_enabled", False))


def ereis_sweep_steps(settings, plan=None):
    """The indices of the PITT steps that get a sweep, as a set (empty when off).

    Every `pitt_eis_every_n`-th potential counted from the start, on the way up and
    on the way back alike, so a return leg sweeps the same potentials as the forward
    one. Never the end dedope: that hold is there to reset the film, not to measure.
    Asks only the impedance box, so a PITT estimate shows the sweeps' time while
    the PITT itself is unticked.
    """
    if not settings.get("pitt_eis_enabled", False):
        return set()
    plan = pitt_plan(settings) if plan is None else plan
    every = max(int(settings.get("pitt_eis_every_n", 1)), 1)
    start = float(settings["pitt_start_v"])
    step_v = abs(float(settings["pitt_step_mv"])) / 1000.0
    out = set()
    for step in plan:
        if step.role == ROLE_DEDOPE:
            continue
        position = int(round(abs(step.potential - start) / step_v)) if step_v else 0
        if position % every == 0:
            out.add(step.index)
    return out


def ereis_frequencies(settings):
    """The sweep's frequencies in Hz, high to low, `pitt_eis_points_per_decade` per
    decade, both ends included. [] when the settings cannot make a sweep."""
    hi = float(settings["pitt_eis_f_high_hz"])
    lo = float(settings["pitt_eis_f_low_hz"])
    ppd = int(settings["pitt_eis_points_per_decade"])
    if not (hi > 0 and lo > 0 and hi >= lo and ppd >= 1):
        return []
    decades = math.log10(hi / lo)
    n = int(round(decades * ppd)) + 1
    if n == 1:
        return [hi]
    return [hi * 10 ** (-decades * k / (n - 1)) for k in range(n)]


def ereis_point_s(f_hz):
    """The estimated time for one frequency, s (see EREIS_POINT_BASE_S)."""
    return EREIS_POINT_BASE_S + EREIS_POINT_CYCLES / float(f_hz)


def ereis_sweep_s(settings):
    """The estimated time for one sweep, s."""
    return sum(ereis_point_s(f) for f in ereis_frequencies(settings))


def ereis_duration_s(settings):
    """The time the sweeps ADD to the PITT, s: an estimate, not a bound -- a film
    may take longer per frequency. 0 when the box is not ticked."""
    return len(ereis_sweep_steps(settings)) * ereis_sweep_s(settings)


def ereis_vac_v(settings):
    """The AC amplitude handed to the instrument, in V."""
    return float(settings["pitt_eis_vac_mv"]) / 1000.0


def ereis_problems(settings):
    """Plain-language reasons the sweeps cannot run as set, or [] when they can (or
    when the box is not ticked)."""
    if not ereis_enabled(settings):
        return []
    out = []
    hi = float(settings["pitt_eis_f_high_hz"])
    lo = float(settings["pitt_eis_f_low_hz"])
    if lo <= 0 or hi <= 0:
        out.append("The impedance frequencies must be larger than 0 Hz.")
    elif lo >= hi:
        out.append(f"The impedance sweep runs from high to low: the highest frequency "
                   f"({hi:g} Hz) must be above the lowest ({lo:g} Hz).")
    elif hi > EREIS_F_MAX_HZ * (1 + _FLOAT_SLACK):
        out.append(f"The highest impedance frequency ({hi:g} Hz) is above what the "
                   f"Reference 600 reaches ({EREIS_F_MAX_HZ:g} Hz).")
    if int(settings["pitt_eis_points_per_decade"]) < 1:
        out.append("The impedance sweep needs at least one point per decade.")
    if float(settings["pitt_eis_vac_mv"]) <= 0:
        out.append("The impedance AC amplitude must be larger than 0 mV.")
    if int(settings.get("pitt_eis_every_n", 1)) < 1:
        out.append("Sweep every Nth potential: N must be 1 or more.")
    return out
