# Coordinating echem with spectra — how it works, and where it breaks

Written 2026-09-04, after reading the NOVA 2.1 manual (`docs/NOVA User manual.pdf`) against the
timing measured on the UW rig on 2026-09-03/04.

**The requirement (Dean, standing since the project began): the spectroscopy must start when the
electrochemistry starts, within about 1–40 ms, and by hardware.** Everything below is measured
against that number.

Findings are marked MEASURED or INFERRED.

---

## 1. What actually has to be true

Two separate things, often conflated:

1. **A shared t=0.** Both instruments must agree on when the experiment began.
2. **Accurate per-sample times relative to it.** Each spectrum must know its own offset from that
   origin.

Requirement 2 is already solid and was never the problem. Spectrum times come from
`AVS_GetScopeData()[0]` — the *spectrometer's own device clock*, in 10 µs ticks — and
`Corrected time (s)` is each timestamp minus spectrum 0's. Host scheduling cannot corrupt them.
An even sampling grid is a convenience, not a requirement.

**Requirement 1 is the whole problem**, and it is a property of how the edge relates to the
waveform, not of Python's cadence.

---

## 2. How the two rigs differ — the structural reason

**Gamry (PLU) — tight by construction.** `ToolkitPotentiostat._run_segment`:

```python
pstat.set_cell(True)
pstat.set_digital_out(0x1, 0x1)   # edge -> the armed Avantes fires
curve.run(True)                    # the waveform starts
```

Three consecutive Python calls. The edge and the start of the waveform are separated by **one
Python statement** — sub-millisecond, and whatever jitter exists is common to both.

**Autolab (UW) — loose by construction.** `AutolabPotentiostat.fire()`:

```python
_set_cell(self._inst, True)
self._proc.Measure()               # returns in ~0.31 s; the PROCEDURE now runs
...sleep to autolab_pulse_delay_s...
pulse P1.A                         # edge -> the armed Avantes fires
```

The waveform does not start when `Measure()` returns. The procedure first runs its own preamble,
and only then reaches the staircase:

```
[1] Autolab control      set CurrentRange = CR10_1mA
[2] Set potential        conditioning potential (0.0 V in the stock template)
[3] Set cell             ON
[4] Wait                 5.0 s
[5] Optimize current range   ~0.2-0.4 s, variable
[6] CV staircase         <- the electrochemistry actually starts HERE
```

MEASURED — the staircase begins at `CalcTime[0]` ≈ **5.86–6.08 s** after `Measure()`. So Python is
guessing, on a wall clock, at an instant that lives ~6 s inside the instrument's own program.
**That gap is the entire problem.** It is not Python being slow; it is the edge and the waveform
being separated by seconds of instrument-side preamble whose duration varies.

---

## 3. The error budget (MEASURED, 2026-09-03/04)

| term | size | fixable from Python? |
|---|---|---|
| edge → spectrometer integrating | **~0.5 ms** | already excellent (the cable) |
| spectrum → its own recorded time | device clock | already exact |
| systematic lag, CV template | 0.800 s | yes — corrected per template |
| systematic lag, CA template | 0.977 s | yes — corrected per template |
| **run-to-run jitter in that lag** | **115–199 ms** | **no** |

The bias is handled: `_wait_window()` reads the procedure's own `FHWait` live and adds a
per-template measured setup lag. **The jitter is not, and cannot be.** Removing
`FHPreCurrentRangingCV` (Sung-Joo's CV, lag 0.602 s) left the spread at ~150 ms, so the scatter is
in the host→instrument start path, not in ranging.

**Current relative-timing uncertainty is roughly ±150 ms against a requirement of 1–40 ms — off by
about 4×, and larger than one 100 ms spectrum interval.** Which spectrum lines up with which point
on the CV is uncertain by 1–2 spectra, run to run.

---

## 4. What the NOVA manual provides (read 2026-09-04)

### 4a. Counter → Pulse — the mechanism that fits the requirement

**NOVA §9.4, p.577 and §9.4.2, p.580.** Counters attach to a *measurement command*; when a counter
condition is met, an action fires. One action is **Pulse**: "a user-defined TTL pulse is generated
at the DIO connector," with properties **DIO connector (P1/P2), Port (A/B/C), pulse value, end
value, duration in µs**.

The sentence that matters:

> *"Since the counters are intrinsically linked to the measured data, the events triggered by the
> counters are directly correlated to the data points."*

INFERRED (from the manual, not yet tested here): a counter on the staircase firing at the first
data point, action Pulse on P1.A, puts the edge **inside the instrument's own measurement loop** —
the exact analogue of DIGOUT0 inside a `.GSequence`. Python leaves the timing path entirely, the
6 s preamble stops mattering, and the ±150 ms jitter should collapse to sub-ms.

**This also explains every failed search.** It is a property *of* a measurement command, not a
command in the list — so `Commands.IdNames` can never show it, and the driver's
`autolab_trigger_in_procedure` guard cannot detect it. That guard needs rethinking if this route is
taken: the flag would have to be trusted, or the counter detected some other way.

### 4b. Wait for DIO — the reverse arrangement

**NOVA §7.2.4.2, p.228.** The `Wait` command (already `FHWait` in every template) has four modes,
and mode 2 is **Wait for DIO**: block until a bit pattern appears on P1/P2, port A/B/C, with an
optional timeout and per-pin masking (1/0/X).

INFERRED: one external edge could release the Autolab *and* fire the Avantes simultaneously. Both
instruments then start on the same electrical event, so whatever jitter exists in producing that
edge is common-mode and cancels in the relative timing. Needs an edge source and wiring; probably
unnecessary if 4a works, but it is the fallback and it does not depend on counters.

---

## 5. A separate problem the same investigation exposed

**The cell is energized for ~6 s before anything is recorded, at a potential nobody chose.**

MEASURED (Dean, watching the Autolab front panel against the Win11 screen): the current range shows
mA, then switches to µA exactly when Python starts plotting. That is command [1] setting
`CR10_1mA`, then `Optimize current range` at [5] auto-ranging just before the staircase.

The template turns the cell on at [3], *before* the 5 s wait at [4]. Worse, `fire()` calls
`_set_cell(True)` before `Measure()` even runs — so the cell is live from before the procedure
starts, at whatever `Ei.Setpoint` was left from the previous segment, until [2] overwrites it.

On a 10 kΩ dummy this is nothing. **On an OMIEC film it is 5–6 seconds of unrecorded polarization
before every segment** — an uncontrolled conditioning step in the prehistory of every doping cycle,
invisible in both the echem file and the spectra.

Both knobs are writable: `FHSetSetpointPotential[0]` is the conditioning potential and `FHWait[0]`
its duration (the driver already reads the latter live). **This should be fixed before any real
sample**, independently of the trigger work.

On ranging itself — no action needed. **NOVA §9.2, p.572:** automatic current ranging stays active
during the measurement and changes range on overload/underload, requiring *five consecutive*
detections before it switches. So a film spanning decades will range rather than clip. Each change
is a small discontinuity in the trace; `Highest/Lowest current range` bound the hunting if a film's
range is known.

---

## 6. Options, and what each buys

| option | relative timing | cost | status |
|---|---|---|---|
| **Counter → Pulse (4a)** | sub-ms, INFERRED | NOVA config per template | **recommended, untested** |
| Wait for DIO (4b) | sub-ms, INFERRED | config + wiring + edge source | fallback |
| Shrink the preamble | still ±150 ms | trivial | does *not* fix jitter — worth doing for §5, not for timing |
| Post-hoc correction | ±150 ms controlled, but *known* to ~ms | small code change | meets "know within 40 ms", not "start within" |
| Status quo | ±150 ms | none | below requirement |

**Post-hoc deserves a mention even though it does not meet the stated requirement.** The echem
trace already records when the waveform truly began, so the real offset is recoverable after the
fact even when it cannot be commanded beforehand. If the science needs to *know* the alignment
rather than *impose* it, that is cheap and available now. Worth deciding which the experiment
actually requires.

---

## 7. Recommendation

1. **Fix the conditioning hold first** (§5). It is independent of timing, it affects real samples,
   and it is two writable parameters.
2. **Try the counter Pulse** (§4a) on a copy of the stock CV in NOVA, then re-measure the skew with
   `bench_autolab_coacquire.py`. That is the one change that can reach 1–40 ms.
3. **Decide whether "know" or "impose"** is the real requirement (§6). It changes how much the
   remaining jitter matters.
4. Leave `autolab_trigger_in_procedure` alone until 4a is proven — its guard cannot see a counter,
   so it would refuse a procedure that works.
