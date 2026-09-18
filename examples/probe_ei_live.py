"""
probe_ei_live.py — HOW do you read a live current out of this Autolab?

20260909_test11 recorded 0.000125 V and -4.18 nA for every sample of every chrono
segment, identical across holds at 0.0 V, 0.1 V and 0.2 V, while the instrument's own
display showed real current. So `Ei.Current` and `Ei.Potential` are latched values
that something must refresh, and reading them in a loop returns the same stale pair
forever. Ei mode cannot work until the live-read path is known.

Three candidates exist in the API report and nothing distinguishes them by reflection:

    1  Ei.Current / Ei.Potential                     -- what test11 used (stale)
    2  Ei.Sampler.Sample() first, THEN Ei.Current    -- Sample() may be the refresh
    3  a sampler's own GetSignal("WE(1).Current")    -- Instrument.CreateSampler()
                                                        and Instrument.GetSignal()
                                                        both exist

The dummy resistor settles it in one run and leaves no room for interpretation. At a
known potential across a known resistor the true current is Ohm's law:

    0.100 V / 10 kOhm = 10 uA

A strategy that returns ~1e-5 A is live. One that returns ~-4e-9 A is the stale
latch. Those differ by three orders of magnitude, so no judgement is required -- and
each strategy is then re-read at a SECOND potential, because a value that is correct
once but frozen afterwards is exactly the failure being chased.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

Phase 0 (reflection) needs no cell. Phase 1 does: it holds 0.1 V then 0.2 V for a
couple of seconds each and switches off in a finally block.

Usage:
    python probe_ei_live.py              # reflection only, nothing energized
    python probe_ei_live.py --energize   # the real answer; dummy resistor in
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Off by default, and a flag rather than an edit, so this file is never accidentally
# left in an energizing state for whoever runs it next:
#     python probe_ei_live.py --energize
ENERGIZE_CELL = "--energize" in sys.argv
DUMMY_OHMS = 10_000.0        # what is actually wired
TEST_POTENTIALS = (0.100, 0.200)   # two, so a frozen-after-first value is caught
SETTLE_S = 1.0               # let the hold settle before reading
CURRENT_SIGNAL = "WE(1).Current"
POTENTIAL_SIGNAL = "WE(1).Potential"


# --- the candidate strategies ----------------------------------------------
# Each returns (potential, current) or None. Kept as small independent callables so
# a failure in one says nothing about the others.

def strat_bare(inst, state):
    """1: read the properties directly. What test11 did."""
    ei = inst.Ei
    return float(ei.Potential), float(ei.Current)


def strat_sample_then_read(inst, state):
    """2: ask the sampler to take a sample, then read the same properties."""
    inst.Ei.Sampler.Sample()
    ei = inst.Ei
    return float(ei.Potential), float(ei.Current)


def strat_sampler_getsignal(inst, state):
    """3: a sampler of our own, read by signal name."""
    sampler = state.get("sampler")
    if sampler is None:
        sampler = inst.CreateSampler()
        state["sampler"] = sampler
    safe(lambda: sampler.Sample())
    def one(name):
        sig = sampler.GetSignal(name)
        for attr in ("Value", "ValueAsObject", "SignalValue"):
            v = safe(lambda a=attr: float(getattr(sig, a)), None)
            if v is not None:
                return v
        return float(sig)
    return one(POTENTIAL_SIGNAL), one(CURRENT_SIGNAL)


def strat_instrument_getsignal(inst, state):
    """3b: the Instrument's own GetSignal, no sampler object."""
    def one(name):
        sig = inst.GetSignal(name)
        for attr in ("Value", "ValueAsObject", "SignalValue"):
            v = safe(lambda a=attr: float(getattr(sig, a)), None)
            if v is not None:
                return v
        return float(sig)
    return one(POTENTIAL_SIGNAL), one(CURRENT_SIGNAL)


STRATEGIES = [
    ("Ei.Current (bare)", strat_bare),
    ("Sampler.Sample() then Ei.Current", strat_sample_then_read),
    ("CreateSampler().GetSignal()", strat_sampler_getsignal),
    ("Instrument.GetSignal()", strat_instrument_getsignal),
]


def phase0(inst):
    rule("0 — what the sampler surface looks like (no cell)")
    for path, obj in (("Instrument.Ei.Sampler", safe(lambda: inst.Ei.Sampler)),
                      ("Instrument.CreateSampler()", safe(lambda: inst.CreateSampler()))):
        if obj is None:
            say(f"  {path}: not available")
            continue
        say(f"  {path}: {type(obj).__name__}")
        names = safe(lambda o=obj: list(o.GetSignals), None)
        say(f"     GetSignals: {names}")
    say("")
    say(f"  Instrument.GetSignals: {safe(lambda: list(inst.GetSignals), None)}")


def phase1(inst):
    """Hold at each potential and ask every strategy what it sees."""
    from EcoChemie.Autolab.Sdk import EI
    ei = inst.Ei
    safe(lambda: setattr(ei, "Mode", EI.EIMode.Potentiostatic))
    safe(lambda: setattr(ei, "CurrentRange", EI.EICurrentRange.CR10_1mA))

    results = {name: [] for name, _ in STRATEGIES}
    state = {}
    try:
        for v in TEST_POTENTIALS:
            expected = v / DUMMY_OHMS
            rule(f"1 — held at {v:+.3f} V   (expect {expected * 1e6:.1f} uA "
                 f"across {DUMMY_OHMS / 1000:.0f} kOhm)")
            ei.Setpoint = float(v)
            ac.switch_cell(inst, True)
            time.sleep(SETTLE_S)
            for name, fn in STRATEGIES:
                got = safe(lambda f=fn: f(inst, state), None)
                if got is None:
                    say(f"    {name:34} raised / unavailable")
                    results[name].append(None)
                    continue
                E, I = got
                err = abs(I - expected) / expected * 100.0 if expected else float("nan")
                verdict = "LIVE" if err < 25 else "stale/wrong"
                say(f"    {name:34} E={E:+.6f} V  I={I:+.4e} A  "
                    f"({err:6.1f}% off)  {verdict}")
                results[name].append((E, I))
            ac.switch_cell(inst, False)
            time.sleep(0.2)
    finally:
        ac.cell_off_quietly(inst)

    rule("VERDICT")
    winners = []
    for name, _ in STRATEGIES:
        vals = results[name]
        if any(v is None for v in vals) or len(vals) < 2:
            say(f"  {name:34} incomplete")
            continue
        currents = [I for _E, I in vals]
        expected = [v / DUMMY_OHMS for v in TEST_POTENTIALS]
        ok = all(abs(c - e) / e < 0.25 for c, e in zip(currents, expected))
        moved = abs(currents[1] - currents[0]) > abs(expected[0]) * 0.25
        if ok and moved:
            say(f"  {name:34} LIVE — correct at both potentials, and it MOVED")
            winners.append(name)
        elif moved:
            say(f"  {name:34} moves, but the magnitude is wrong "
                f"(range or scaling?)")
        else:
            say(f"  {name:34} FROZEN — did not change between potentials")
    say("")
    if winners:
        say(f"  Use: {winners[0]}")
        say("  Wire that into AutolabPotentiostat.pump() and Ei mode works.")
    else:
        say("  None of these is a live read. The remaining candidate is that a")
        say("  sampler must be told WHICH signals to collect before Sample() —")
        say("  check Sampler.Contains/Reset and the SDK manual's Sampler section.")

    # The overload flags ride on the same object, and the procedure path trusts them.
    rule("SIDE QUESTION — do the overload flags update?")
    say("  pump() calls Ei.CurrentOverload every spectrum in BOTH modes to catch a")
    say("  meaningless segment. If Ei's properties are latched, that check may never")
    say("  have been able to fire — worth knowing independently of Ei mode.")
    say(f"    Ei.CurrentOverload   = {safe(lambda: bool(inst.Ei.CurrentOverload))}")
    say(f"    Ei.PotentialOverload = {safe(lambda: bool(inst.Ei.PotentialOverload))}")


def main():
    rule("HOW DO YOU READ A LIVE CURRENT OUT OF THIS AUTOLAB?")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    if ENERGIZE_CELL:
        say(f"*** The cell WILL be switched on at {TEST_POTENTIALS} V. ***")
        say(f"*** {DUMMY_OHMS / 1000:.0f} kOhm dummy only — never a sample. ***")
    inst = ac.connect()
    if inst is None:
        return 1
    try:
        phase0(inst)
        if ENERGIZE_CELL:
            phase1(inst)
        else:
            rule("1 — skipped")
            say("  Set ENERGIZE_CELL = True with the dummy in. Ohm's law is what")
            say("  decides this, and it needs current to actually flow.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "probe_ei_live_report.txt"))
    sys.exit(code)
