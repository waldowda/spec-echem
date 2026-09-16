"""
probe_overload.py -- what does the Autolab's overload flag actually mean?

Every CV in every film run on 2026-09-11 flagged CURRENT OVERLOAD, and not one
recorded sweep clips. That leaves three questions unanswered, and the driver acts on
the answer to all three:

  1. WHAT DOES A CLIPPED TRACE LOOK LIKE?  If a clipped current sits visibly pinned at
     the range ceiling, a flagged segment can be checked against its own data. If it
     looks like plausible electrochemistry, it cannot.

  2. DOES THE FLAG LATCH, OR SELF-CLEAR?  pump() reads it once per spectrum and reports
     the FIRST time it sees it set. If the flag latches, one transient spike at t=0
     marks the whole segment -- and every later segment too, if it survives a range
     change -- which would explain a CV flagging while its own data is clean. If it
     self-clears, a flag means the overload is happening NOW.

  3. IS THE CV FLAG WORTH ACTING ON?  Follows from 1 and 2.

Method: hold a potential across a 10 kOhm dummy at a range that CANNOT carry the
resulting current, then at one that comfortably can, and watch the flag and the
recorded current through both. 0.1 V across 10 kOhm is 10 uA:

    CR13_1uA    full scale   1 uA   -- demands 10x over scale. OVERLOAD expected.
    CR11_100uA  full scale 100 uA   -- 10% of scale. The control.

Reading the flag requires a Sampler.Sample() FIRST. Ei.Current, Ei.Potential and the
overload flags are a LATCH, not live properties -- which is why this check had never
once fired, in either mode, before 2026-09-09.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

Usage:
    python probe_overload.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

HOLD_V = 0.100              # 10 uA across the 10 kOhm dummy
HOLD_S = 4.0                # long enough to see a flag persist or decay
INTERVAL_S = 0.100          # the cadence pump() runs at
OVER_RANGE = "CR13_1uA"     # 1 uA full scale -- 10x under what the cell will draw
GOOD_RANGE = "CR11_100uA"   # 100 uA full scale -- the control
EXPECTED_A = 1.0e-5         # 0.1 V / 10 kOhm


def set_mode_and_range(inst, range_name):
    """Potentiostatic, fixed range, setpoint -- written while the cell is still OPEN."""
    from EcoChemie.Autolab.Sdk import EI
    inst.Ei.Mode = EI.EIMode.Potentiostatic
    inst.Ei.CurrentRange = getattr(EI.EICurrentRange, range_name)
    inst.Ei.Setpoint = float(HOLD_V)


def read_point(inst):
    """One (potential, current, current_overload, potential_overload).

    Sample() FIRST, always: the flags read the same latch the scalars do.
    """
    try:
        inst.Ei.Sampler.Sample()
    except Exception:  # noqa: BLE001 -- a stale read is not a reason to stop
        pass
    return (float(inst.Ei.Potential), float(inst.Ei.Current),
            bool(inst.Ei.CurrentOverload), bool(inst.Ei.PotentialOverload))


def hold(inst, label, range_name, seconds=HOLD_S, cell_on=True):
    """Hold HOLD_V at one range, sampling throughout. Returns the rows."""
    say("")
    say("  %s  (%s, cell %s)" % (label, range_name, "ON" if cell_on else "OFF"))
    set_mode_and_range(inst, range_name)
    if cell_on:
        ac.switch_cell(inst, True)
    rows, t0 = [], time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        t = time.perf_counter() - t0
        pot, cur, cur_ov, pot_ov = read_point(inst)
        rows.append((t, pot, cur, cur_ov, pot_ov))
        slept = time.perf_counter() - t0 - t
        time.sleep(max(0.0, INTERVAL_S - slept))
    if cell_on:
        ac.switch_cell(inst, False)

    flagged = sum(1 for r in rows if r[3] or r[4])
    currents = [r[2] for r in rows]
    say("    samples          %d" % len(rows))
    say("    current          first %.4g A   last %.4g A   max |I| %.4g A"
        % (currents[0], currents[-1], max(abs(c) for c in currents)))
    say("    expected         %.4g A  (0.1 V / 10 kOhm)" % EXPECTED_A)
    say("    overload flagged %d of %d samples" % (flagged, len(rows)))
    say("    first sample     t=%.3fs  E=%.5f V  I=%.4g A  CUR_OV=%s  POT_OV=%s"
        % (rows[0][0], rows[0][1], rows[0][2], rows[0][3], rows[0][4]))
    say("    last sample      t=%.3fs  E=%.5f V  I=%.4g A  CUR_OV=%s  POT_OV=%s"
        % (rows[-1][0], rows[-1][1], rows[-1][2], rows[-1][3], rows[-1][4]))
    return rows


def main():
    rule("probe_overload -- 10 kOhm dummy, 0.1 V, two current ranges")
    say("Reading a flag needs Sampler.Sample() first; the flags are a latch.")

    inst = ac.connect()
    if inst is None:
        return
    try:
        ac.cell_off_quietly(inst)

        rule("0. baseline -- cell OFF on the fine range, nothing driven")
        hold(inst, "baseline", OVER_RANGE, seconds=1.0, cell_on=False)

        rule("1. the control -- a range that CAN carry 10 uA")
        good = hold(inst, "control", GOOD_RANGE)

        rule("2. the overload -- a range 10x too fine for 10 uA")
        over = hold(inst, "overload", OVER_RANGE)

        rule("3. does the flag LATCH? cell off, back to the good range")
        # No reconnect and no new session: exactly what the driver does between
        # segments, which is the case that matters.
        after = hold(inst, "after", GOOD_RANGE, seconds=2.0, cell_on=False)

        rule("4. and driven again on the good range?")
        recover = hold(inst, "recovery", GOOD_RANGE)

        rule("VERDICT")
        good_flagged = sum(1 for r in good if r[3] or r[4])
        over_flagged = sum(1 for r in over if r[3] or r[4])
        after_flagged = sum(1 for r in after if r[3] or r[4])
        recover_flagged = sum(1 for r in recover if r[3] or r[4])

        say("  control flagged   %d/%d" % (good_flagged, len(good)))
        say("  overload flagged  %d/%d" % (over_flagged, len(over)))
        say("  after, cell off   %d/%d" % (after_flagged, len(after)))
        say("  recovery, driven  %d/%d" % (recover_flagged, len(recover)))
        say("")
        if over_flagged == 0:
            say("  The flag did NOT fire even 10x over scale. Then it does not mean")
            say("  'this range is too small', and the CV flags need another cause.")
        elif after_flagged or recover_flagged:
            say("  LATCHING: the flag outlived the overload itself. Then ONE transient")
            say("  at t=0 marks a whole segment -- and the next segment too -- which")
            say("  would explain a CV flagging while its recorded sweep is clean.")
            say("  pump() must clear or re-arm it per segment, or every segment after")
            say("  the first reports an overload it never had.")
        else:
            say("  SELF-CLEARING: the flag tracks the present sample. A flag means the")
            say("  overload is happening NOW, pump() reporting it once per segment is")
            say("  right, and a CV that flags has a real excursion somewhere in it.")

        ceiling = max(abs(r[2]) for r in over)
        say("")
        say("  Clipped current reached |I| <= %.4g A against a %.4g A cell current."
            % (ceiling, EXPECTED_A))
        if ceiling < EXPECTED_A * 0.5:
            say("  So a clipped trace IS recognisable from its own data: the current")
            say("  sits pinned near the range ceiling, not merely wrong.")
        else:
            say("  The recorded current is NOT obviously pinned, so a clipped segment")
            say("  cannot be recognised from the data alone -- the flag is the only")
            say("  evidence, which makes the latch question decisive.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
        ac.write_transcript(os.path.join(HERE, "probe_overload_report.txt"))


if __name__ == "__main__":
    main()
