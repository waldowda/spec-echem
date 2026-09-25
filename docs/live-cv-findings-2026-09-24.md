# Live-CV wedge — what the bench established, 2026-09-24

Written for a session continuing this work **away from the rig**. Everything below was
measured on an Autolab PGSTAT302N with a **10 kOhm dummy resistor**, 100 mV/s, 10 mV
steps. No film was involved at any point.

This supersedes the diagnosis in [`bench-live-cv-handoff.md`](bench-live-cv-handoff.md),
which was the plan going in. That plan's central hypothesis turned out to be wrong; the
document is still worth reading for how the question was framed, not for its answer.

**Nothing further needs the instrument** until the two fixes in §3 exist and want
confirming. Both are code-only.

---

## 1. What was disproved

**The straddle is not the cause.** `spec_echem/potentiostat.py::_read_ei_pair` guards
against a latch refresh landing between the potential read and the current read. It does
not fix the glitch:

- `20260924_test1` (GUI): **nine** glitches on screen, **zero** dropped samples in the
  log — the guard never fired on any of them.
- `20260924_test2` (GUI): glitches again, zero drops again.
- `bench_ei_pair_source.py` (bare CV, no spectrometer): mid-sweep staleness median 0.063
  steps, p95 0.246. Essentially no straddles, and **the wedge does not reproduce at all
  without the GUI's acquisition loop**.

Keep the guard — it costs one property read and it does cover `Ei` mode, where "nothing
else refreshes the latch" is still an untested assumption. But it is not the fix.

## 2. What the wedge actually is

A **lag**, not a mismatch across two reads.

`20260924_test2` dumped the live stream the plot draws (241 samples). Seven mid-sweep
points were displaced from the resistor line by:

    +30.55, +31.20, +31.35, +32.12, +32.42, +32.13, and -30.68 mV

Every one is **about three staircase steps**, and the sign follows the sweep direction —
negative while descending, positive while climbing. At 100 mV/s, 31 mV is **0.31 s of
sweep**: the potential runs roughly three polls behind the current, persistently.

A straddle is one refresh between two reads and is worth at most ONE step (10 mV). And
because the stale potential is *stable*, `before == after` passes every time, so the
guard is structurally blind to it.

Supporting measurements:

- **`Ei.Potential` is not quantized** — median 1.58 mV from the nearest 10 mV multiple.
  It is a measured value with its own noise, so bit-identical consecutive reads genuinely
  mean the latch did not refresh. It does not refresh on **43.5%** of 100 ms polls.
- **The recorded data is clean.** `CV.txt` (from `.Signals`) had zero points off the line
  out of 480, worst residual 3.9e-7 A, fit R = 9870.8 ohm. Only the latch stream glitches.
- **`.Signals` fills DURING the run**: 0 points at 1.2 s climbing to 1040 at 118 s, 106
  distinct intermediate counts. The 2026-09-03 reading that it "materialises at
  completion" came from an aborted run — it never filled *because it was aborted*, which
  is exactly the ambiguity that evidence was flagged as carrying.

**A second, separate bug.** Samples 0–3 of `20260924_test2` read `E = 0.0000 V` and
`I = 0.0000e+00 A` — exactly zero, four times, from t = 1.114 s. `pump()` samples before
the latch has ever been loaded, so the plot draws a point at the origin. The recorder's
own `CalcTime[0]` (1.250 s there) marks when the staircase actually starts.

## 3. The two fixes, neither started

1. **Draw the live CV trace from `.Signals` in procedure mode.** This is the wedge fix.
   `gui/tabs/run_tab.py:377` calls `pot.live_data()` on a timer; in procedure mode that
   should read the recorder's arrays instead of `_live_samples`. The recorder is already
   what `CV.txt` uses and is provably clean, so the lag cannot exist by construction.
   Keep `pump()` sampling regardless — the overload flags are only readable while the run
   is going, which is its first and most important job.

   In `Ei` mode the latch stays: there `_live_samples` **is** the segment's saved data,
   and a lag cannot make a wedge because the potential is held constant per segment.

2. **Do not plot a sample before the latch has content.** The origin-point fix. An
   exactly-zero `(E, I)` pair is trivially detectable, and the first recorded `CalcTime`
   says when the staircase really began.

## 4. Traps worth not re-learning

- **`FHCyclicVoltammetry2` puts step at `[3]` and stop at `[5]`** — swapped relative to
  the order the NOVA manual prints them (`autolab-run-api.md` §1).
  `examples/bench_live_cv.py` had the manual's order and therefore wrote `step = 0.0`.
  **A zero-step staircase records 0 points, stops early, and otherwise looks like a
  perfectly clean successful run** — the same failure shape as the Gamry's GC'd signal
  object. The as-loaded defaults are the tell: `[3]` is 0.00244 (a step), `[5]` is 0.0.
  Two bench runs were invalid before this was caught. **The driver was never affected** —
  its `CV_IDX_*` constants were already correct and it verifies them against `IdNames`.
- **The GUI sets `FHWait` to 0; the bench scripts do not.** The template's own wait is
  ~5 s, so the staircase starts ~5 s later in a bench run than in a GUI run. A
  "sweep starts at" constant taken from a GUI log will be wrong in a bench script, and
  startup artifacts will be mistaken for mid-sweep wedges. That happened once here.
- **Do not judge a glitch by a multiple of the noise.** One step of stale potential on
  this dummy is 10 mV / 9873 ohm = **1.01 uA**, against a median residual of 0.354 uA —
  only 2.9x. A threshold of "10x the median residual" sits *above* a genuine one-step
  wedge and finds nothing. Scale the threshold to the physics: fractions of a
  step-equivalent.
- **A resistor is the right test object for this.** The true CV is a straight line, so a
  mismatched pair is unambiguous *and* its size converts straight back into staleness:
  `E_implied = (I - b) * R`, and `E_implied - E` is how far behind the potential was.

## 5. How to re-examine any of this without the rig

`SPECECHEM_LIVE_DUMP=1` in the environment makes the Autolab driver write
`{folder}/{label}_live_samples.csv` — the stream the live plot actually draws, which is
otherwise never persisted and dies with the run. Off by default; it adds a file beside
the data and changes no existing format.

The evidence from this afternoon is committed under `examples/`:
`bench_live_cv_report.txt`, `bench_ei_pair_source_report.txt`, and
`bench_ei_pair_source_samples.csv` (373 raw samples).

## 6. Loose ends

- **Cadence stall, new and unexplained.** `20260924_test2` logged spectra cadence
  `max 1139.0 ms, jitter(sd) 67.5 ms`, against `20260924_test1`'s `max 140.5, sd 6.2` —
  minutes apart on the same rig. It is **not** the cause of the wedges: the gaps at all
  seven glitch points were a normal 102–141 ms.
- **Still open from the original handoff, and still needing the rig:** a film run on
  `CR10_1mA`; `examples/bench_ei_sampling.py` phase A; the ULS2048L linearity items; and
  whether the CV's `FHPreCurrentRangingCV` picks too sensitive a range.

## 7. Confirmed on the rig, 2026-09-25

Both fixes (`7b9da23`) ran on the instrument for the first time in the GUI run
`20260925_test1`. The run used the 10 kOhm dummy with 100 mV/s, 10 mV steps and
−0.5 to +0.7 V for a 3-cycle CV, followed by pre-dedoping and two doping/dedoping pairs.
The chrono segments used `Ei` mode on `CR09_10mA`, each a 30 s hold at 0.1 s.

- **Fix 1: the wedge is gone.** The user watched the live trace and saw no wedge on any
  cycle. The trace drew normally and the log has no "could not read the recorder" warning,
  so `.Signals` is readable mid-run on this instrument.
- **Fix 2: no origin point.** The CV logged `2 sample(s) before the latch loaded`, where
  about 4 was expected; it was 4 on 2026-09-24, and the count depends on poll phase. There
  was no `live sample(s) dropped` line, so the straddle guard counted 0.
- **The chrono path did not regress.** `prededoping(0)`, `steps(0/1)` and `dedoping(0/1)`
  each have 301 data rows. That matches `20260916_test1` under the same hold and
  `delta_time`, and no file starts with an all-zero row.
- **The cadence stall did not recur.** Across all six segments the maximum was 140.1 ms,
  with jitter (sd) of 8.6 ms at most, against 1139 ms on `20260924_test2`. The cause is
  still unknown.

**The echem data from this run is not clean, but the fault was the cell connection, not
the code.** The reference clip was making intermittent contact, and the user reseated the
clips during `steps(0)`.

- **CV cycles 1–2:** the fit gives a slope of R = 9.9 kOhm with residual sd 0.17 uA, but an
  intercept of **+40 uA**, which a resistor cannot have. Treat it as a floating reference
  shifting the measured potential.
- **CV cycle 3:** the data degrades from index 587 to the end, reaching a max residual of
  20 uA.
- **After reseating:** `steps(1)` gives 28.87 uA at +0.300 V, which is 10.4 kOhm with no
  offset.
- **`dedoping(1)`:** logged a POTENTIAL OVERLOAD at t = 20.5 s, meaning the clip let go
  again.

The live trace draws from the same `.Signals` arrays as `CV.txt`, so the bad cycle-3 data
is real measured data being plotted faithfully, not a display artifact. Use this run for
the plotting result only. Do not take resistances or offsets from it.
