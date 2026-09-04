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

## 6. The resolution ceiling changes the ranking

**MEASURED constraint (Dean, 2026-09-04): spectra on this rig will not go faster than ~100–200 ms,
and the useful science window is 100 ms to 10 s.** (The Gamry rig may reach ~25 ms if S/N allows.)

That reverses the earlier reasoning. At a 100 ms ceiling:

- Python-driven `Ei` sampling at ~20–30 ms per point is **5–10× faster than needed** — the firmware
  recorder's millisecond capability cannot be used, because nothing optical matches it.
- The procedure's ~0.6 s late start is **6 lost points** at the front of the useful window.
- The ±150 ms trigger jitter is **1–1.5 spectra** of alignment uncertainty.

So the procedure's advantage is worthless here and its costs are expensive.

**Post-hoc correction, floated earlier, does not work and is withdrawn.** `CalcTime[0]` is on the
Autolab's clock from procedure start; the pulse time is on the host clock. The unknown is precisely
the offset between them, so differencing reproduces the ±150 ms rather than removing it. There is no
event common to both records to anchor against — which is what a hardware edge *is*.

---

## 7. Proposed architecture (agreed in principle 2026-09-04; not yet built)

**Python owns potential application, t=0 and the start of spectra. The procedure owns only the
staircase waveform.**

### CA — doping, dedoping, pre-dedoping: Python-driven via `Ei`

```python
ei.Setpoint = V
ei.CellOnOff = On     # the sample's t=0
spec.measure()        # spectrum 0, free-run — no edge needed
```

Three consecutive statements. **This makes the trigger problem disappear for these segments**: no
DIO, no procedure preamble, no jitter, nothing lost at the front of the window. It restores the
original notebook architecture, where `timeStamp[0]` *is* potential application and every later
spectrum inherits a correct offset from the Avantes device clock.

`pump()` already accumulates `(t, E, I)` per spectrum into `_live_samples` and `live_data()` already
builds an `EchemData` from them — so making that the *recorded* trace rather than a live-plot
convenience is a small change.

### CV: keep the procedure, but strip its preamble

A staircase hand-rolled in Python is not worth attempting, and CV timing is the looser case (±150 ms
across a 4–40 s sweep is ~1.5 spectra). But `[2] Set potential` and `[3] Set cell` should be
**deleted from the `.nox`** so Python applies the potential and switches the cell on itself, exactly
as for CA. The procedure then only sweeps.

Consequence: the spectra cover the conditioning period *and* the sweep, with an exact t=0. The echem
trace still begins at the staircase, so the first ~0.6–0.8 s has spectra but no current — acceptable
for a CV, where nothing interesting happens at constant potential.

### On the 5 s wait — a scientific choice, not cleanup

`FHWait` exists to equilibrate the film at the initial potential. That may be wanted. What is wrong
today is that it happens **by accident and unobserved**: the template holds for 5 s after its own
`Set cell`, and `fire()` energizes even earlier, at whatever `Ei.Setpoint` was left from the previous
segment. Three ways to fix it:

1. **Drop it** (`FHWait[0]` → ~0). Sweep begins essentially at cell-on. No equilibration.
2. **Keep it deliberately** and record it as metadata: held at X V for Y s. Honest, but the film's
   history is still unobserved.
3. **Make it a real segment** — a short Python-driven CA hold at the initial potential, with spectra,
   followed by the CV. Fully observed, and it falls out of the CA design above for free.

Option 3 is the one this architecture makes cheap, and it is the only one where the equilibration is
data rather than a gap.

### Three open questions before building

1. **Cost of one `Ei` scalar read.** The ~10 ms figure is INFERRED from the in-run/free-run spectrum
   difference, never isolated. Ten lines on the dummy settles it and sets Python's sampling floor.
2. **Current ranging.** NOVA §9.2 governs ranging *for a measurement command*. With no measurement
   command running, Python may have to manage `Ei.CurrentRange` itself — real work for a film
   spanning decades, and clipping if wrong.
3. **Will a staircase run with the cell already on and no `Set cell` in the procedure**, and does it
   sweep from the applied potential or re-step to its own initial value? Not answerable from the
   manual; one run settles it.

Also unknown: whether `Ei.Current` (an instantaneous scalar) is noisier than the `FHLevel` recorder's
sampled value at these currents. One comparison on the dummy.

### What this does not solve

Cutoffs are measurement-command properties, so a Python-driven hold has none. `Ei.CurrentOverload`
is readable and `pump()` can check it, but that has to be written deliberately rather than assumed.

---

## 8. Where the counter-Pulse route still matters

If CV timing ever needs to beat ±150 ms, §4a is the mechanism — and it remains the only route to a
genuinely instrument-timed edge. It is not needed for CA under this architecture, which is the
larger half of the problem.
