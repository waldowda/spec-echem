"""
PITT -- an equilibrium potential staircase, read electrically and optically.

Pure logic only: the step plan, when a step ends, and when a spectrum is due. No
instrument code lives here, so each rule is testable without a rig and the drivers
(Autolab first, the Gamry after the 64-bit toolkit) share one definition.

Why a staircase at all: a CV is a rate measurement. On the 20250710 reference run
the two sweeps sit at least 450 mV apart where a film at equilibrium gives ~0 mV, so
its density of states describes a transient. Holding each small step until the
current settles is equilibrium by construction (PITT, potentiostatic intermittent
titration), and the polaron absorbance at the end of each hold is a second,
capacitance-free reading of the same charge.

Why ONE continuous waveform: both drivers switch the cell OFF at the end of every
segment, so a staircase built from separate segments would sit at open circuit
between steps -- each step would start from wherever the film had drifted, and the
leaked charge would be counted in that step's dQ, which is the quantity PITT
measures. The data is still SAVED per step (one HDF5 group each); only the cell is
continuous.
"""
import math
from dataclasses import dataclass

# What a step was, so the analysis never has to infer it from the potential.
ROLE_FORWARD = "forward"    # start -> stop
ROLE_RETURN = "return"      # stop -> start, when "also step back down" is ticked
ROLE_DEDOPE = "dedope"      # the optional hold at pitt_end_dedope_v at the end

# How a step ended. Recorded per step: a step that hit its max hold is NOT at
# equilibrium, and the analysis must be able to say so rather than guess.
END_CUTOFF = "cutoff"       # |I| fell to the cutoff fraction of the step's peak
END_MAX_HOLD = "max_hold"   # the cap ended it first
END_FIXED = "fixed"         # the end dedope: a fixed hold, not ended by the current
END_STOPPED = "stopped"     # Stop pressed: this step cut short, the rest not run
END_ABORTED = "aborted"     # Abort pressed: likewise, immediately
# Stopped and aborted steps KEEP their data. A 30 s segment is cheap to discard; a
# staircase can run for hours, and throwing away every completed step because the
# last one was interrupted would withhold results the scientist paid for.

# How many CONSECUTIVE samples must sit at or below the cutoff before a step ends.
# One is not enough: on the PGSTAT302N 2026-10-05 a 1 MOhm dummy on CR10_1mA read
# ~90 nA low (the range's zero offset), so at +0.1 V the current sat near zero, the
# 'peak' was 12 nA, and a single sample landing within 0.12 nA of zero ended the step
# at 4.4 s on a cell whose current never decays. At the 0.1 s tick, 5 is half a
# second of the current staying down -- a real settle does that; noise grazing zero
# does not.
SETTLE_SAMPLES = 5

# Absorbs binary float error only, as n_doping_cycles does: (0.7 - -0.5) / 0.01 is
# 119.99999999999999, and that ladder must keep its last step.
_FLOAT_SLACK = 1e-9


@dataclass(frozen=True)
class PittStep:
    index: int          # the h5 group number, in run order
    potential: float    # V, the setpoint
    role: str           # ROLE_*


def pitt_plan(settings):
    """Every step the staircase will hold, in run order. -> [PittStep]

    The STOP potential is a ceiling the user set, so the step count rounds DOWN and
    the last forward step never passes it -- the rule n_doping_cycles learned on the
    bench (2026-09-18), where rounding to nearest held a film 50 mV past its end.
    Start may lie above stop; the staircase then runs downward. A return leg, when
    asked for, retraces the forward potentials without repeating the turning point
    and ends back at the start.
    """
    start = float(settings["pitt_start_v"])
    stop = float(settings["pitt_stop_v"])
    step = abs(float(settings["pitt_step_mv"])) / 1000.0
    if step == 0 or start == stop:
        forward = [start]
    else:
        n = int(math.floor(abs(stop - start) / step + _FLOAT_SLACK)) + 1
        sign = 1.0 if stop > start else -1.0
        # Rounded to the microvolt so 0.1 + 0.2 lands on 0.3 in the file and the
        # dropdowns rather than 0.30000000000000004.
        forward = [round(start + sign * k * step, 6) for k in range(n)]

    potentials = [(v, ROLE_FORWARD) for v in forward]
    if settings.get("pitt_return", False) and len(forward) > 1:
        potentials += [(v, ROLE_RETURN) for v in forward[-2::-1]]
    if settings.get("pitt_end_dedope", False):
        potentials.append((float(settings["pitt_end_dedope_v"]), ROLE_DEDOPE))
    return [PittStep(i, v, role) for i, (v, role) in enumerate(potentials)]


def pitt_step_potential(settings, index):
    """The setpoint of step `index`, or None when there is no such step."""
    plan = pitt_plan(settings)
    return plan[index].potential if 0 <= index < len(plan) else None


def pitt_duration_bounds(settings):
    """(shortest, longest) the staircase can take, in seconds.

    Every step holds at least its minimum and at most its maximum, so the truth lies
    between. The end dedope is a fixed hold, not cut short by the current.
    """
    plan = pitt_plan(settings)
    holds = [s for s in plan if s.role != ROLE_DEDOPE]
    dedope = sum(float(settings["pitt_end_dedope_time_s"])
                 for s in plan if s.role == ROLE_DEDOPE)
    lo = len(holds) * float(settings["pitt_min_hold_s"]) + dedope
    hi = len(holds) * float(settings["pitt_max_hold_s"]) + dedope
    return lo, hi


def pitt_problems(settings):
    """Plain-language reasons the PITT cannot run as set, or [] when it can.

    These are the settings that make the run impossible or meaningless. Anything the
    scientist might reasonably choose -- a high ceiling, a long hold -- is theirs.
    """
    out = []
    if float(settings["pitt_step_mv"]) <= 0:
        out.append("The PITT step must be larger than 0 mV.")
    cutoff = float(settings["pitt_cutoff_pct"])
    if not 0 < cutoff < 100:
        out.append(f"The PITT cutoff must be between 0 and 100% of the step's peak "
                   f"current; it is {cutoff:g}%.")
    lo, hi = float(settings["pitt_min_hold_s"]), float(settings["pitt_max_hold_s"])
    if lo < 0 or hi <= 0:
        out.append("The PITT hold times must be positive.")
    elif lo > hi:
        out.append(f"The PITT minimum hold ({lo:g} s) is longer than its maximum "
                   f"({hi:g} s).")
    if float(settings["pitt_slow_interval_s"]) < float(settings["chrono_delta_time"]):
        out.append("The PITT slow spectrum interval is shorter than the time between "
                   "spectra, so it would not slow anything down.")
    if settings.get("pitt_end_dedope", False) \
            and float(settings["pitt_end_dedope_time_s"]) <= 0:
        out.append("The end-of-PITT dedope needs a hold time longer than 0 s.")
    return out


class StepEnd:
    """Decides when ONE step is over, from the current as it is sampled.

    Ends at the cutoff -- |I| at or below `cutoff_pct` of the largest |I| seen in this
    step, on SETTLE_SAMPLES consecutive samples -- once the minimum hold has passed,
    or at the maximum hold, whichever comes first. The minimum exists because a fast-settling step could otherwise end at its
    second sample, before the transient or a single spectrum had been taken.

    The peak is the largest SAMPLED current. On a cell whose transient is faster than
    the sampling -- the UDC4's Randles side settles in ~0.2 ms against ~50 ms samples
    -- the peak is just the DC floor, the cutoff is never met, and the max hold ends
    every step. That is the right answer: nothing resolvable relaxed.
    """

    def __init__(self, cutoff_pct, min_hold_s, max_hold_s,
                 settle_samples=SETTLE_SAMPLES, resolution_a=None):
        self.fraction = float(cutoff_pct) / 100.0
        self.min_hold = float(min_hold_s)
        self.max_hold = float(max_hold_s)
        self.settle_samples = int(settle_samples)
        # One count of the current range, when the driver knows it. A cutoff smaller
        # than this cannot be expressed by any reading: on the PGSTAT302N CR10_1mA
        # reads in whole counts of 3.05 nA, so a 1% cutoff of a one-count peak was met
        # by readings of exactly zero (2026-10-05, test 3). Such a step cannot be
        # judged settled, and runs to its max hold with `unresolved` set.
        self.resolution = float(resolution_a) if resolution_a else 0.0
        self.peak = 0.0
        self.reason = None
        self._below = 0          # consecutive samples at or below the cutoff

    @property
    def unresolved(self):
        """The cutoff current is below one count of the range: settling cannot be
        judged from these readings. Meaningful once the step has ended."""
        return self.resolution > 0 and self.fraction * self.peak < self.resolution

    def feed(self, t_in_step, current):
        """One sample. Returns END_CUTOFF / END_MAX_HOLD once over, else None."""
        if self.reason is not None:
            return self.reason
        magnitude = abs(float(current))
        if math.isfinite(magnitude):
            self.peak = max(self.peak, magnitude)
        if math.isfinite(magnitude) and magnitude <= self.fraction * self.peak:
            self._below += 1
        elif math.isfinite(magnitude):
            self._below = 0      # one sample back above it starts the count again
        if t_in_step >= self.max_hold:
            self.reason = END_MAX_HOLD
        elif (t_in_step >= self.min_hold and self._below >= self.settle_samples
              and not self.unresolved):
            self.reason = END_CUTOFF
        return self.reason


def spectrum_due(t_in_step, t_last, delta_time, fast_s, slow_interval_s):
    """Is a spectrum due now? Full rate for `fast_s` after a step starts, then one
    every `slow_interval_s` through the rest of the hold.

    The transient at the start of a step is what needs the cadence; the long tail of
    a hold changes slowly, and at full rate a 30-minute staircase is ~18,000 spectra
    in one run. `t_last` is the step-relative time of the previous spectrum, or None
    for the step's first.
    """
    if t_last is None:
        return True
    interval = delta_time if t_in_step < fast_s else slow_interval_s
    # A hair of slack so a loop that wakes exactly on the interval does not skip it
    # to the next tick on a float rounding.
    return t_in_step - t_last >= interval - 1e-9


@dataclass
class PittRecord:
    """Everything one staircase collected, continuous, each sample tagged by step.

    Tagging at collection time is what lets the data be SAVED per step without
    matching two clocks: a spectrum belongs to the step that was set when it was
    taken. The spectrometer and the potentiostat keep separate clocks, and nothing
    here pretends otherwise.
    """
    spectra: list = None            # 1-D count arrays
    timestamps: list = None         # spectrometer clock, s
    spectrum_step: list = None      # step index of each spectrum
    echem_time: list = None         # potentiostat clock, s
    echem_t_in_step: list = None    # s since this sample's step was set (loop clock)
    echem_potential: list = None    # V, measured
    echem_current: list = None      # A
    echem_step: list = None         # step index of each sample
    steps: list = None              # one dict per step that RAN; see close_step()
    completed: bool = False         # every planned step ran to its own end

    def __post_init__(self):
        for name in ("spectra", "timestamps", "spectrum_step", "echem_time",
                     "echem_t_in_step", "echem_potential", "echem_current",
                     "echem_step", "steps"):
            if getattr(self, name) is None:
                setattr(self, name, [])

    def close_step(self, step, t_start, t_end, reason, peak, unresolved=False):
        # When the first current sample landed after the setpoint changed. dQ is
        # integrated from the samples, so the charge before this is NOT in it --
        # the analysis can extrapolate back to 0, or at least say how much it missed.
        firsts = [t for t, k in zip(self.echem_t_in_step, self.echem_step)
                  if k == step.index]
        self.steps.append({
            "first_sample_s": float(firsts[0]) if firsts else float("nan"),
            "index": step.index, "potential_set": step.potential, "role": step.role,
            "hold_s": float(t_end - t_start), "end_reason": reason,
            "peak_current_A": float(peak),
            # True when the cutoff was below one count of the current range, so the
            # step could not be judged settled whatever the cell did.
            "cutoff_unresolved": bool(unresolved),
            "n_spectra": sum(1 for k in self.spectrum_step if k == step.index),
            "n_echem": sum(1 for k in self.echem_step if k == step.index),
        })


def step_end_for(step, settings, resolution_a=None):
    """The StepEnd rule for one step. The end dedope is a fixed hold: neither
    minimum nor maximum, just its time, and the current never ends it early."""
    if step.role == ROLE_DEDOPE:
        hold = float(settings["pitt_end_dedope_time_s"])
        return StepEnd(cutoff_pct=0.0, min_hold_s=hold, max_hold_s=hold)
    return StepEnd(settings["pitt_cutoff_pct"], settings["pitt_min_hold_s"],
                   settings["pitt_max_hold_s"], resolution_a=resolution_a)
