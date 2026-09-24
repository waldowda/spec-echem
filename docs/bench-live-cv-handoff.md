# Bench handoff — live-CV straddle (UW Autolab rig)

For a Claude session running **on the Windows instrument PC**. Read this first, then work
through the prompts in order. Everything here is on `gui-dev` — `git pull` before starting.

**Rig:** Metrohm Autolab PGSTAT302N + Avantes AvaSpec-ULS2048L.

**Cell safety, non-negotiable:**

- Phases marked *no cell* energize nothing. Run them first, every time.
- The energized phases want a **10 kΩ dummy resistor**, not a film. W + WS on one leg,
  RE + CE on the other.
- For the film run at the end: current range **`CR10_1mA`**, dedope at **−0.5 V**, and
  **never past +0.7 V** — the test film does not survive +0.8 V.
- The user decides when a cell is energized. Do not set `ENERGIZE_CELL = True` on your own.

---

## What this is about

The live CV trace on the Run tab sometimes draws a point beside the line — a wedge: along
the trace, off it, back. The saved `CV.txt` is clean, so it is display-only.

**Diagnosis:** `Ei.Potential` and `Ei.Current` are two separate reads of one latch. The
running `.nox` refreshes that latch on its own schedule, so a refresh landing between the
two reads pairs sample N's potential with sample N+1's current. The potential is read
first, so it is the older half; on a negative-going sweep the point lands to the right.

**The fix, already committed** (`spec_echem/potentiostat.py::_read_ei_pair`): read the
potential again after the current, and re-take the sample if it moved. Also, `pump()` now
uses `sample_ei()`'s return value — a failed refresh drops the sample instead of recording
the previous reading under a fresh timestamp.

**It is unit-tested against a fake, and unverified on hardware.** That is what this trip
is for.

---

## Prompt A — the bench script (no cell, then dummy)

> Run `examples/bench_live_cv.py` with `ENERGIZE_CELL = False` and show me the output.

It connects, loads the standard CV procedure, prints the parameter map and the `.Signals`
count before anything has run. If it fails here, the problem is setup, not the experiment:
check `pythonnet`, the SDK paths at the top of `examples/autolab_common.py`, and that NOVA
is closed.

Then, once the user has the dummy connected and sets `ENERGIZE_CELL = True`:

> Run it again and summarise both sections.

**Section A — straddle rate.** Report the percentage, the biggest potential move across a
pair, and how it compares with the 10 mV step size.

- **Non-zero** → the mechanism is confirmed on hardware. Record the rate.
- **Zero** → it does *not* clear the mechanism. The user's screenshot showed it happening;
  one scan rate on a resistor is one set of conditions. Say so rather than concluding the
  guard is unnecessary.

**Section B — does `.Signals` fill during the run?**

- **Count climbs** → a live plot could be drawn from the recorder instead of the latch.
  Worth designing; do not build it on this trip.
- **Flat during, full after** → the array materialises at completion. Close the TODO bullet
  "Consider not building the live CV trace from the latch at all."
- **Read raises mid-run** → same conclusion, for a different reason: the array is
  unavailable while measuring.

---

## Prompt B — the acceptance test (the one that matters)

> Start a normal CV in the GUI with the live echem plot on, and watch for the wedge.

The script measures the mechanism; only this shows whether the fix works. Ask the user to
watch the trace — they know what the glitch looks like, having reported it.

Also check the run log for the new line. A segment that dropped samples reports
`N live sample(s) dropped` in its health summary. A handful is the guard working. Hundreds
means something else is wrong, most likely `Sample()` failing rather than straddling, and
the warning text distinguishes the two.

---

## Prompt C — record what was found

> Update `TODO.md` under "Live CV plot shows points the recorded data does not" and commit
> the transcript.

- Tick **"Close the straddle window"** and **"Still use `sample_ei`'s return value"** if
  the GUI check passed, noting the measured straddle rate beside them.
- Answer or close the **`.Signals` as a live source** bullet with section B's result.
- Commit `examples/bench_live_cv_report.txt` — the transcript is the evidence, and it is
  how the finding travels back through git.

Write what was MEASURED, with the conditions (scan rate, step size, dummy or film). Do not
write conclusions the run does not support.

---

## While the rig is open — other items needing this hardware

Only if there is time; none of these block the above.

- **A film run on `CR10_1mA`** — the highest-value open item. Every film on 2026-09-11 used
  `CR09_10mA`, whose **+1.6 µA zero offset** is 10–100% of the settled currents, so those
  steady-state numbers are not quantitatively trustworthy. Peaks were fine.
- **`examples/bench_ei_sampling.py`** — written, never run. Phase A needs no cell and times
  a single `Ei` read, setting the floor for any grid faster than 100 ms.
- **ULS2048L linearity** — this is the floor-limited detector (1.05 ms against 0.009 ms on
  the other one), so two open items can only be exercised here: whether
  `LIN_STOP_FLOOR_SPANS = 8.0` has any physics behind it, and that `Find saturation`
  advises something achievable when already at the floor.
- **Is the CV's auto-ranging picking too sensitive a range?** `FHPreCurrentRangingCV`
  probably probes at the initial potential, where a film draws almost nothing. If so the
  fix is a NOVA edit (fixed range in the CV template), not code.
