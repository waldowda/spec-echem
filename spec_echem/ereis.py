"""
ER-EIS -- impedance at each step of a potential staircase, read electrically and
optically.

The third staircase beside the PITT (spec_echem/pitt.py) and the GITT
(spec_echem/gitt.py): the same continuous waveform, the same loop
(acquisition.acquire_pitt with kind="ereis") and one HDF5 group per step. Pure logic
only: the plan, the frequency list, the duration estimate and the Start checks.

One STEP is: hold at E for `ereis_hold_s` (a fixed hold, with spectra, so the film
settles at that potential), then a frequency sweep from high to low at the same DC
potential, with the cell left on and the spectra carrying on at the slow rate. The
low-frequency capacitance gives the chemical capacitance, and with it the density of
states, independently of the PITT's dQ/dE on the same film.

Gamry only for now: the Autolab's FRA32M failed NOVA's own test (2026-10-26) and is
awaiting repair. The loop and the file are not Gamry-specific, so an Autolab driver
can be added once its FRA works.
"""
import math

from spec_echem.pitt import ROLE_FORWARD, PittStep, StepEnd

ROLE_REPEAT = "repeat"          # the first step again at the end: the drift check

# How the frequency sweep of a step ended. Recorded per step as `sweep_end`.
SWEEP_COMPLETE = "complete"
SWEEP_STOPPED = "stopped"       # Stop pressed: the frequency in hand finished, no more
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


def ereis_plan(settings):
    """Every step the staircase will hold and sweep, in run order. -> [PittStep]

    The stop potential is a ceiling, as in the PITT (pitt.pitt_plan): the step count
    rounds DOWN. Start may lie above stop; the staircase then runs downward. With
    `ereis_repeat_first`, the first potential is held and swept again at the end, so
    drift over a long run shows as two different spectra at one potential.
    """
    start = float(settings["ereis_start_v"])
    stop = float(settings["ereis_stop_v"])
    step = abs(float(settings["ereis_step_mv"])) / 1000.0
    if step == 0 or start == stop:
        forward = [start]
    else:
        n = int(math.floor(abs(stop - start) / step + _FLOAT_SLACK)) + 1
        sign = 1.0 if stop > start else -1.0
        forward = [round(start + sign * k * step, 6) for k in range(n)]
    potentials = [(v, ROLE_FORWARD) for v in forward]
    if settings.get("ereis_repeat_first", False):
        potentials.append((forward[0], ROLE_REPEAT))
    return [PittStep(i, v, role) for i, (v, role) in enumerate(potentials)]


def ereis_frequencies(settings):
    """The sweep's frequencies in Hz, high to low, `ereis_points_per_decade` per
    decade, both ends included. [] when the settings cannot make a sweep."""
    hi = float(settings["ereis_f_high_hz"])
    lo = float(settings["ereis_f_low_hz"])
    ppd = int(settings["ereis_points_per_decade"])
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
    """The estimated length of the whole staircase, s: every step's hold plus its
    sweep. An estimate, not a bound -- a film may take longer per frequency."""
    n = len(ereis_plan(settings))
    return n * (float(settings["ereis_hold_s"]) + ereis_sweep_s(settings))


def ereis_vac_v(settings):
    """The AC amplitude handed to the instrument, in V."""
    return float(settings["ereis_vac_mv"]) / 1000.0


def ereis_problems(settings):
    """Plain-language reasons the ER-EIS cannot run as set, or [] when it can."""
    out = []
    if float(settings["ereis_step_mv"]) <= 0:
        out.append("The ER-EIS step must be larger than 0 mV.")
    if float(settings["ereis_hold_s"]) <= 0:
        out.append("The ER-EIS hold before each sweep must be longer than 0 s.")
    hi = float(settings["ereis_f_high_hz"])
    lo = float(settings["ereis_f_low_hz"])
    if lo <= 0 or hi <= 0:
        out.append("The ER-EIS frequencies must be larger than 0 Hz.")
    elif lo >= hi:
        out.append(f"The ER-EIS sweep runs from high to low: the highest frequency "
                   f"({hi:g} Hz) must be above the lowest ({lo:g} Hz).")
    elif hi > EREIS_F_MAX_HZ * (1 + _FLOAT_SLACK):
        out.append(f"The ER-EIS highest frequency ({hi:g} Hz) is above what the "
                   f"Reference 600 reaches ({EREIS_F_MAX_HZ:g} Hz).")
    if int(settings["ereis_points_per_decade"]) < 1:
        out.append("The ER-EIS sweep needs at least one point per decade.")
    if float(settings["ereis_vac_mv"]) <= 0:
        out.append("The ER-EIS AC amplitude must be larger than 0 mV.")
    if float(settings["ereis_slow_interval_s"]) < float(settings["chrono_delta_time"]):
        out.append("The ER-EIS slow spectrum interval is shorter than the time between "
                   "spectra, so it would not slow anything down.")
    return out


def ereis_step_end_for(step, settings, resolution_a=None):
    """The hold before a sweep: a fixed time, never ended by the current."""
    hold = float(settings["ereis_hold_s"])
    return StepEnd(cutoff_pct=0.0, min_hold_s=hold, max_hold_s=hold, fixed=True)
