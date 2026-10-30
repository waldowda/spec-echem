"""
GITT -- galvanostatic intermittent titration, read electrically and optically.

The galvanostatic sibling of the PITT (spec_echem/pitt.py): the same continuous
waveform, one loop (acquisition.acquire_pitt) and one HDF5 group per step, with the
control swapped -- set the CURRENT, measure the potential. Pure logic only, as in
pitt.py: the plan, when a step ends, the limits, and when a spectrum is due.

One STEP is one titration: a current pulse of `gitt_pulse_s`, then a relaxation at
0 A with the cell left ON (a galvanostat at zero current, not open circuit), ended
after `gitt_relax_s` or -- opt-in -- once dE/dt has fallen below a threshold. Keeping
pulse and relaxation in one step lets the instrument time the pulse where it can
(the Gamry runs both in one curve on its own clock) and means the display hold at a
step's end always lands at 0 A, never mid-pulse.

Why a potential window is not optional: a galvanostat drives E wherever it must to
pass the current. A film that runs out of capacity goes to compliance, and that is
electrolyte breakdown or a damaged film. Every sample is checked against the window
in Python and the run ends there; the Gamry also stops the pulse in the instrument.
"""
import math
from collections import deque
from dataclasses import dataclass

from spec_echem.pitt import END_FIXED, END_MAX_HOLD, SETTLE_SAMPLES

ROLE_REST = "rest"            # the optional 0 A hold before the first pulse: E0
ROLE_TITRATION = "titration"  # one pulse + its relaxation

END_RELAXED = "relaxed"       # dE/dt fell below the threshold (the opt-in rule)
END_E_LIMIT = "e_limit"       # the potential left the window: the RUN stops here
# END_FIXED (pitt.py) is a relaxation that ran its set time with the rule off;
# END_MAX_HOLD one that ran it with the rule on and never relaxed.

# Relaxation samples this soon after the pulse ends are left out of the dE/dt fit:
# the IR drop is a step, and on the Gamry the loop's clock leads the instrument's
# pulse end by the ~80 ms a curve takes to start.
RELAX_SKIP_S = 1.0

# The Autolab's current at a 0 A setpoint, as a fraction of the range's full scale.
# MEASURED on the PGSTAT302N: -16 nA on CR11_100uA (2026-10-26, 10 kOhm) and +1.6 uA
# on CR09_10mA (2026-09-11). Both are 1.6e-4 of full scale. Not yet measured on a
# Gamry, so no Gamry concern is raised from it.
AUTOLAB_ZERO_OFFSET_FRACTION = 1.6e-4

# Absorbs binary float error, as pitt._FLOAT_SLACK does.
_FLOAT_SLACK = 1e-9


@dataclass(frozen=True)
class GittStep:
    index: int          # the h5 group number, in run order
    current: float      # A during the pulse; + oxidises (p-dopes)
    pulse_s: float      # s; 0 for the rest step
    role: str           # ROLE_*

    @property
    def setpoint(self):
        return self.current

    def fields(self):
        """This step's columns in the step table."""
        return {"current_set_A": self.current, "pulse_s": self.pulse_s}


def gitt_current_a(settings):
    return float(settings["gitt_current_ua"]) * 1e-6


def gitt_plan(settings):
    """Every step the titration will run, in run order. -> [GittStep]"""
    i = gitt_current_a(settings)
    pulse = float(settings["gitt_pulse_s"])
    steps = []
    if float(settings.get("gitt_initial_rest_s", 0.0)) > 0:
        steps.append((0.0, 0.0, ROLE_REST))
    steps += [(i, pulse, ROLE_TITRATION)] * int(settings["gitt_n_pulses"])
    return [GittStep(k, c, p, r) for k, (c, p, r) in enumerate(steps)]


def gitt_settle_enabled(settings):
    """Does dE/dt end a relaxation? Off (the default), each relaxes for its set time."""
    return bool(settings.get("gitt_relax_settle_enabled", False))


def gitt_planned_charge_c(settings):
    """|Q| the pulses will pass, in coulombs: |I| x pulse x N. Exact by design --
    the galvanostat sets it -- which is why the cap is checked before Start."""
    return (abs(gitt_current_a(settings)) * float(settings["gitt_pulse_s"])
            * int(settings["gitt_n_pulses"]))


def gitt_duration_bounds(settings):
    """(shortest, longest) the titration can take, in seconds."""
    n = int(settings["gitt_n_pulses"])
    pulse = float(settings["gitt_pulse_s"])
    relax = float(settings["gitt_relax_s"])
    rest = max(float(settings.get("gitt_initial_rest_s", 0.0)), 0.0)
    shortest = relax
    if gitt_settle_enabled(settings):
        shortest = min(relax, RELAX_SKIP_S + float(settings["gitt_relax_window_s"]))
    return rest + n * (pulse + shortest), rest + n * (pulse + relax)


def gitt_problems(settings):
    """Plain-language reasons the GITT cannot run as set, or [] when it can."""
    out = []
    i = gitt_current_a(settings)
    if not math.isfinite(i) or i == 0:
        out.append("The GITT pulse current must not be 0.")
    if float(settings["gitt_pulse_s"]) <= 0:
        out.append("The GITT pulse must last longer than 0 s.")
    if float(settings["gitt_relax_s"]) <= 0:
        out.append("The GITT relaxation must last longer than 0 s.")
    if int(settings["gitt_n_pulses"]) < 1:
        out.append("The GITT needs at least one pulse.")
    if float(settings.get("gitt_initial_rest_s", 0.0)) < 0:
        out.append("The GITT rest before the first pulse cannot be negative.")
    lo, hi = float(settings["gitt_e_min_v"]), float(settings["gitt_e_max_v"])
    if lo >= hi:
        out.append(f"The GITT potential window is empty: the lower limit ({lo:+.3f} V) "
                   f"must be below the upper ({hi:+.3f} V).")
    cap = float(settings["gitt_max_charge_mc"]) * 1e-3
    q = gitt_planned_charge_c(settings)
    if cap <= 0:
        out.append("The GITT charge cap must be larger than 0 mC.")
    elif q > cap * (1 + _FLOAT_SLACK):
        out.append(f"The GITT would pass {q * 1e3:.3g} mC, more than the "
                   f"{cap * 1e3:.3g} mC cap. Check the current's units, or raise the "
                   f"cap if this film can take it.")
    if gitt_settle_enabled(settings):
        if float(settings["gitt_relax_dvdt_uv_s"]) <= 0:
            out.append("The GITT dE/dt threshold must be larger than 0 µV/s.")
        window = float(settings["gitt_relax_window_s"])
        if window <= 0:
            out.append("The GITT dE/dt window must be longer than 0 s.")
        elif RELAX_SKIP_S + window > float(settings["gitt_relax_s"]):
            out.append(f"The GITT dE/dt window ({window:g} s, plus {RELAX_SKIP_S:g} s "
                       f"after the pulse) does not fit in the relaxation "
                       f"({float(settings['gitt_relax_s']):g} s).")
    if float(settings["gitt_slow_interval_s"]) < float(settings["chrono_delta_time"]):
        out.append("The GITT slow spectrum interval is shorter than the time between "
                   "spectra, so it would not slow anything down.")
    return out


def gitt_range_problem(current_a, full_scale_a):
    """A reason the pulse cannot be applied on this current range, or None."""
    if full_scale_a and abs(current_a) > full_scale_a:
        return (f"The GITT pulse ({abs(current_a):.3g} A) is larger than the current "
                f"range's full scale ({full_scale_a:.3g} A).")
    return None


def autolab_offset_concern(current_a, full_scale_a, pulse_s, relax_s):
    """A warning when the Autolab's 0 A offset is not small next to the pulse, or None.

    Two ways it matters: next to the pulse current itself, and as charge -- the
    offset flows through every relaxation, so a long relaxation after a short pulse
    can undo a real fraction of what the pulse put in. The scientist decides; this
    only says so.
    """
    i = abs(current_a)
    if not full_scale_a or not i or not pulse_s:
        return None             # nothing to compare with (gitt_problems says why)
    offset = AUTOLAB_ZERO_OFFSET_FRACTION * full_scale_a
    drain = offset * relax_s / (i * pulse_s)
    if i >= 20 * offset and drain <= 0.05:
        return None
    return (f"The Autolab reads about {offset:.2g} A at a 0 A setpoint on this range "
            f"(measured: 1.6e-4 of full scale). That is {offset / i:.1%} of the pulse, "
            f"and over each relaxation it moves {drain:.1%} of the pulse's charge. The "
            f"relaxed potentials will carry that bias.")


class GittStepEnd:
    """Decides when ONE titration step is over, from the potential as it is sampled.

    Over when the relaxation has run `relax_s`, or -- with `settle` -- once dE/dt,
    a straight-line fit over the last `window_s` of the relaxation, has stayed at or
    below `dvdt_v_s` for SETTLE_SAMPLES consecutive samples. At ANY time, a potential
    outside [e_min, e_max] ends it END_E_LIMIT, which ends the run.
    """

    def __init__(self, pulse_s, relax_s, e_min, e_max, settle=False,
                 dvdt_v_s=None, window_s=None, settle_samples=SETTLE_SAMPLES):
        self.pulse_s = float(pulse_s)
        self.relax_s = float(relax_s)
        self.e_min, self.e_max = float(e_min), float(e_max)
        self.settle = bool(settle)
        self.dvdt = float(dvdt_v_s) if dvdt_v_s is not None else 0.0
        self.window = float(window_s) if window_s is not None else 0.0
        self.settle_samples = int(settle_samples)
        self.peak = 0.0             # the largest |I| sampled: the pulse, as measured
        self.e_last = float("nan")
        self.slope = float("nan")   # the last dE/dt fitted, V/s
        self.reason = None
        self._below = 0
        self._window = deque()      # (t, E) in the relaxation, for the fit

    unresolved = None               # no current cutoff here to be unresolved

    def _fit_slope(self):
        n = len(self._window)
        if n < 3:
            return float("nan")
        mt = sum(t for t, _ in self._window) / n
        mv = sum(v for _, v in self._window) / n
        sxx = sum((t - mt) ** 2 for t, _ in self._window)
        if sxx <= 0:
            return float("nan")
        return sum((t - mt) * (v - mv) for t, v in self._window) / sxx

    def feed(self, t_in_step, current, potential=None):
        """One sample. Returns END_* once over, else None."""
        if self.reason is not None:
            return self.reason
        i = abs(float(current))
        if math.isfinite(i):
            self.peak = max(self.peak, i)
        v = float("nan") if potential is None else float(potential)
        if math.isfinite(v):
            self.e_last = v
            if v < self.e_min or v > self.e_max:
                self.reason = END_E_LIMIT
                return self.reason
        t_relax = t_in_step - self.pulse_s
        if self.settle and math.isfinite(v) and t_relax >= RELAX_SKIP_S:
            self._window.append((t_in_step, v))
            while self._window and self._window[0][0] < t_in_step - self.window:
                self._window.popleft()
            if t_relax >= RELAX_SKIP_S + self.window:
                self.slope = self._fit_slope()
                if math.isfinite(self.slope) and abs(self.slope) <= self.dvdt:
                    self._below += 1
                else:
                    self._below = 0
        if t_relax >= self.relax_s:
            self.reason = END_MAX_HOLD if self.settle else END_FIXED
        elif self._below >= self.settle_samples:
            self.reason = END_RELAXED
        return self.reason

    def row_fields(self):
        """This rule's columns in the step table."""
        return {"e_last_V": self.e_last, "relax_dvdt_V_s": self.slope}


def gitt_step_end_for(step, settings, resolution_a=None):
    """The GittStepEnd rule for one step. The rest step is a relaxation with no
    pulse, of its own length; the rule-on/off choice applies to it too."""
    relax = (float(settings["gitt_initial_rest_s"]) if step.role == ROLE_REST
             else float(settings["gitt_relax_s"]))
    settle = gitt_settle_enabled(settings)
    if settle and relax < RELAX_SKIP_S + float(settings["gitt_relax_window_s"]):
        settle = False      # a rest too short to fit a window simply runs its time
    return GittStepEnd(step.pulse_s, relax, settings["gitt_e_min_v"],
                       settings["gitt_e_max_v"], settle=settle,
                       dvdt_v_s=float(settings["gitt_relax_dvdt_uv_s"]) * 1e-6,
                       window_s=settings["gitt_relax_window_s"])


def gitt_spectrum_due(step, t_in_step, t_last, delta_time, fast_s, slow_interval_s):
    """Is a spectrum due now? Full rate for `fast_s` after the pulse starts AND after
    it ends -- both are transients -- one every `slow_interval_s` otherwise. The first
    spectrum after the pulse ends is always taken, so the switch is never skipped."""
    if t_last is None:
        return True
    pulse = step.pulse_s
    if pulse > 0 and t_last < pulse <= t_in_step:
        return True
    phase_start = pulse if (pulse > 0 and t_in_step >= pulse) else 0.0
    interval = delta_time if t_in_step - phase_start < fast_s else slow_interval_s
    return t_in_step - t_last >= interval - 0.5 * delta_time
