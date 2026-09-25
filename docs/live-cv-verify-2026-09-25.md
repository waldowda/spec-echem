# Verify the live-CV fixes — UW Autolab rig, 2026-09-25

For a Claude session **on the Windows instrument PC**. `git pull` first; the fixes are on
`gui-dev` at `7b9da23`.

Read [`live-cv-findings-2026-09-24.md`](live-cv-findings-2026-09-24.md) for what yesterday
established. This note is only about confirming the two fixes written afterwards, neither
of which has ever run on an instrument.

**Cell safety:** the checks below want the **10 kΩ dummy resistor**, not a film. If a film
goes in for the separate run at the end: current range **`CR10_1mA`**, dedope at
**−0.5 V**, **never past +0.7 V**. The user decides when a cell is energized.

---

## What changed, and what could go wrong

**Fix 1 — the live CV is drawn from the recorder.** In procedure mode `live_data()` now
reads the procedure's own arrays (`.Signals`) instead of the latch stream `pump()`
collects. The latch lags by ~3 staircase steps; the recorder does not, and is what
`CV.txt` already uses.

*The risk is that the live plot goes blank rather than wrong.* It deliberately does not
fall back to the latch, so if the arrays cannot be read mid-run on this instrument, the
Run tab will sit at "waiting for data…" all segment. That is the failure to watch for, and
the run log will carry one warning naming it.

**Fix 2 — no origin point.** Samples read before the latch has ever loaded — exactly
(0.0000 V, 0.0000e+00 A) — are dropped. Only leading zeros; a zero later in a segment is
kept as data.

*The risk is over-dropping.* In `Ei` mode those samples are saved data, so if the count is
large, chrono segments are losing real rows.

---

## Check 1 — the wedge is gone (the acceptance test)

> Run a normal CV in the GUI on the dummy with the live echem plot on. Watch the trace.

100 mV/s, 10 mV steps — the conditions the wedge appeared under. Ask the user to watch:
they reported it and know its shape (along the line, off it, back).

- **No wedge, trace draws normally** → fix 1 works. This is the result we want.
- **Trace stays blank / "waiting for data…"** → the recorder cannot be read mid-run here.
  Check the run log for "could not read the recorder for the live plot". Report the
  exception named there; do not patch around it at the bench.
- **Wedge still present** → the diagnosis is still incomplete. Capture the run with
  `SPECECHEM_LIVE_DUMP=1` and compare the dumped latch stream against `CV.txt`.

Also worth noting: the live trace should now match `CV.txt` point for point, because both
come from the same arrays.

## Check 2 — no point at the origin

Same run. At the start of each segment the trace should begin on the sweep, not at (0, 0).

Then read the run log's per-segment health lines:

- `N sample(s) before the latch loaded` — expect a small number, around 4 on a CV.
- `N live sample(s) dropped` — expect 0. This is the straddle guard, which measured zero
  yesterday; a sudden non-zero count means something new.

## Check 3 — the chrono path did not regress

> Run a doping/dedoping sequence on the dummy and check `steps(N).txt` and
> `dedoping(N).txt` row counts against previous runs.

`Ei` mode is deliberately untouched by fix 1, but fix 2 touches the path that writes its
data. A file materially shorter than an equivalent earlier run means leading samples are
being dropped that should not be. Compare like with like — same hold duration and
`delta_time`.

## Check 4 — the cadence stall, if it reappears

`20260924_test2` logged spectra cadence `max 1139.0 ms, jitter(sd) 67.5 ms` against
test1's `max 140.5, sd 6.2`, minutes apart on the same rig, and it is unexplained. It was
**not** the cause of the wedges. If it shows up again, record the run folder and the
cadence line; if the runs today are all clean, note that too — "did not recur" is a
result.

---

## Recording what was found

> Update `docs/live-cv-findings-2026-09-24.md` with a short "confirmed on the rig" section,
> tick the two items in `TODO.md` under the live-CV heading, and commit.

Write what was MEASURED, with conditions. If a check could not be run, say so rather than
leaving it ambiguous.

## Separately, if there is rig time

Unchanged from yesterday's list, none of it blocked by the above:

- **A film run on `CR10_1mA`** — the highest-value open item. `CR09_10mA` carries a
  **+1.6 µA zero offset**, which is 10–100% of the settled currents from 2026-09-11, so
  those steady-state numbers are not quantitatively trustworthy.
- **`examples/bench_ei_sampling.py`** phase A — written, never run, needs no cell.
- **ULS2048L linearity** — `LIN_STOP_FLOOR_SPANS = 8.0` has no physics behind it, and
  `Find saturation` should be checked for advising something achievable at the floor.
- **`FHPreCurrentRangingCV`** — does the CV's auto-ranging pick too sensitive a range?
  If so the fix is a NOVA template edit, not code.
