# Autolab run-API — probe results & driver handoff (2026-08-31)

Cross-session handoff for writing the **`AutolabPotentiostat`** backend in
`spec_echem/potentiostat.py` (the analogue of `ToolkitPotentiostat`). Companion to
[`metrohm-rig-status.md`](metrohm-rig-status.md).

Everything below was learned by running **`examples/query_autolab_run.py`** against the rig on
2026-08-31. The raw listing is committed at **`examples/autolab_api_report.txt`** (a passing
10 kΩ dummy-resistor CV run).

Rig: **Metrohm Autolab PGSTAT302N** (earlier notes said PGSTAT10 — corrected; serial/modules still
to re-confirm at the bench), Autolab **SDK 2.1**, **64-bit** `SpecEchem` conda env (Python 3.13),
`pythonnet` 3.x. No 32/64-bit split — one process drives everything.

---

## 0. The design decision this all serves

**Use NOVA's standard CV and chronoamperometry procedures, parameterized from Python. Do not
generate waveforms in Python.** (Dean, 2026-08-30.)

This is the same shape as the Gamry driver, which is worth seeing explicitly. `ToolkitPotentiostat`
does not hand-roll anything either — `_build_signal` calls toolkitpy's own constructors,
`signal_r_up_dn_new` for CV and `signal_d_step_new` for a hold, and feeds them numbers out of
settings. The vendor supplies the standard waveform; we supply the parameters. The Autolab
equivalent of that is `LoadProcedure` on a standard `.nox` plus parameter writes.

**Why it matters technically, not just tidily:** the staircase and its sampling are *firmware*
timed. A Python loop that sets a potential, waits, and reads a current inherits OS scheduling jitter
on both the potential axis and the time axis. Direct `Ei` control is trivial for a constant-potential
hold and poor for a sweep, and even for the hold it is a real downgrade in the current trace. It is
the fallback, not the plan.

**Two consequences that shape everything else here:**

1. **The parameter index map *is* the interface.** Since a `CommandParameter` has no name property,
   an index is the only handle on a potential or a scan rate. That is why so much bench effort goes
   into establishing and *verifying* those indices, and why an unconfirmed index is a `None` stub in
   the driver rather than a plausible guess — a silently wrong index runs the wrong experiment on a
   real sample and produces data that looks fine.
2. **The trigger can live inside the `.nox`.** NOVA's own spectro-EC procedures on this rig already
   pulse P1.A, which is exactly the analogue of DIGOUT0 living inside a `.GSequence`. The driver
   currently pulses from Python during the procedure's wait window, which is equivalent and keeps
   the timing in code; either is legitimate, and the choice is now a preference rather than a
   constraint.

---

## 1. What is now proven (with hardware)

### Connect / load / run
- `Instrument()` → `inst.AutolabConnection.EmbeddedExeFileToStart = ADX` →
  `inst.set_HardwareSetupFile(HDW)` → `inst.Connect()`; check `inst.AutolabConnection.IsConnected`.
- **`proc = inst.LoadProcedure(path)` RETURNS the `Procedure` object.** There is **no
  `inst.Procedure`** property. Keep the return value.
- `proc.Measure()` is **NON-BLOCKING** — returns in ~0.3 s with `proc.IsMeasuring == True`.
  Driver runs `Measure()` then polls `proc.IsMeasuring` until `False`. **No dedicated thread
  needed** (simpler than the Gamry). `MeasureAsync` does **not** exist on this SDK.
- Abort / pause: `proc.Abort()`, `proc.Hold()`, `proc.Continue()`, `proc.Skip()`.
- Liveness: `inst.AutolabConnection.IsConnected` (untested against a yanked USB — worth one
  deliberate pull mid-run, the Gamry equivalent caught a truncated file).

### Parameters (Q2 — YES, decisive)
- `proc.Commands` is a `ProcedureCommandList`: `.Names` (`String[]`), `.IdNames` (`String[]`),
  `.Item` indexer, `GetEnumerator`, `Contains` / `ContainsId`. Address a command by IdName:
  `proc.Commands["FHCyclicVoltammetry2"]`.
- A `Command` has `.CommandParameters` (a `CommandParameterList`) and `.Signals`
  (a `CommandParameterSignalList`). The `Command` object itself has **no name property** — names
  come from the parent list's `.Names[i]` / `.IdNames[i]`.
- A `CommandParameter` has **no name property either** (all name-reflection returned `None`) —
  parameters are addressed **by index** within the command. `prm.ValueAsObject` reads and
  **writes** (verified: write a Double, read it back, matches to 1e-9). Non-numeric params are
  typed enums (`CommandParameterMode`, `CommandParameterOnOff`, …).

Standard CV (`Standard Nova Procedures\Cyclic voltammetry.nox`) command list:

| i | `.Names[i]` | `.IdNames[i]` |
|---|---|---|
| 0 | Cyclic voltammetry | ExtendedSequence |
| 1 | Autolab control | FHGetSetValues |
| 2 | Set potential | FHSetSetpointPotential |
| 3 | Set cell | FHSwitchCell |
| 4 | Wait time (s) | FHWait |
| 5 | Optimize current range | FHPreCurrentRangingCV |
| 6 | CV staircase | FHCyclicVoltammetry2 |
| 7 | i vs E | PlotsIvsE |
| 8 | Set cell | FHSwitchCell |

**CV staircase (`FHCyclicVoltammetry2`) `.CommandParameters` — index map:**

| idx | type | seen | meaning | confidence |
|---|---|---|---|---|
| 0 | Double | 0.0 | **start potential** (V) | high |
| 1 | Double | 1.0 | **upper vertex potential** (V) | high |
| 2 | Double | −1.0 | **lower vertex potential** (V) | high |
| 3 | Double | 0.00244 | **step potential** (V) | **confirmed** — `SetpointApplied` increments by exactly this; `[3] / Δt` = scan rate |
| 4 | Int | 2 | **number of (stop) crossings** — 2 = one full cycle | **confirmed** — the only `Int` of the seven params, the only count-type param in the manual; `Scan` stayed 1 across one 0→+1→−1→0 cycle |
| 5 | Double | 0.0 | **stop potential** (V) — where the sweep terminates | **high** — value 0.0 matches the sweep ending at 0 V, and rules out step (step is nonzero, at [3]) |
| 6 | Double | 0.1 | **scan rate** — stored **V/s** in the SDK (NOVA UI shows mV/s: 0.1 V/s = 100 mV/s) | **confirmed** — step / Δt = 0.09998 |

All seven are now identified. The NOVA manual's "CV potentiostatic" entry lists the staircase
block as (in order, no indices given): start, upper vertex, lower vertex, **stop pot**, number of
crossings, **step pot**, scan rate (mV/s). The SDK's `CommandParameters` order has **step at [3]
and stop at [5]** — swapped relative to the manual's list positions for those two — per the run
data. spec-echem drives 0/1/2/6 and 4 (multi-cycle).

Other writable Doubles: `FHSetSetpointPotential` param [0] (**preconditioning potential**),
`FHWait` param [0] (**duration** / pre-staircase wait, seconds — default 5.0).

### Recorded data (Q4 — clean mapping)
- Read **after** `proc.IsMeasuring` goes `False`, from
  **`proc.Commands["FHCyclicVoltammetry2"].Signals`** (a `CommandParameterSignalList`, same shape
  as `CommandParameterList`: `.Names`, `.IdNames`, indexer, enumerator).
- Each entry: `sig.ValueAsObject` → a .NET `List<Double>`; `list(sig.ValueAsObject)` in Python.
- Channels present in the standard CV (1640 points each for the default sweep):

| `.Names` | `.IdNames` | units | use |
|---|---|---|---|
| Potential applied | `SetpointApplied` | V | commanded staircase |
| Time | `CalcTime` | **s** | time axis (see offset note) |
| WE(1).Current | `EI_0.CalcCurrent` | **A** | current |
| Scan | `ScanNumber` | int | cycle index (all 1 for one cycle) |
| WE(1).Potential | `EI_0.CalcPotential` | **V** | **measured** potential — use this, not SetpointApplied |
| Index | `Index` | int | 1..N |
| Q+ / Q− | `QPlus` / `QMin` | — | empty in this procedure config; ignore |

- **`CalcTime` is wall-clock from procedure start** and begins at ≈ the `FHWait` value (~6 s),
  not 0. Driver: `t = CalcTime − CalcTime[0]`.
- `EI_0.CalcCurrent` is **amps** — 100 µA arrives as `1.0e-4`. No ×1000.

### Live scalars during a run (Q5)
- `inst.Ei.Sampler.GetSignal("WE(1).Potential")` → a **`Signal`** with only `.Name` and
  `.Value: Double` — a **scalar** (instantaneous), not an array. Good for a live readout;
  the trace comes from `.Signals` at the end.
- `Signal` names available: `inst.GetSignals` (property) →
  `['External(1).External 1', 'External(1).External 2', 'WE(1).Potential', 'WE(1).Current',
    'WE(1).Power', 'WE(1).Resistance', 'WE(1).Charge']`.

### Direct `Ei` control (Q3 — for a no-`.nox` software-timed hold, if ever wanted)
- `inst.Ei`: `Setpoint` (rw Double), `PotentialApplied` / `Potential` / `Current` (r Double),
  `Mode: EIMode` (rw), `CurrentRange: EICurrentRange` (rw), `Bandwidth: EIBandwidth` (rw),
  `Cell: Boolean` (r), `PotentialOverload` / `CurrentOverload` (r Boolean), `Sampler` (rw).
- **`Ei.CellOnOff` is the nested enum `EI.EICellOnOff`** (`On = 1`, `Off = 0`). pythonnet 3.0
  **rejects a bare bool/int** — you must assign the member:
  `from EcoChemie.Autolab.Sdk import EI; ei.CellOnOff = EI.EICellOnOff.On`.
  Read back the state with `ei.Cell` (bool).

### Trigger (Q8 — NO DIO in the standard CV)
- The SDK standard CV has no digital-I/O command. `fire()` must pulse
  **`inst.Dio.DioPortsP1[0]`** from Python (the line `examples/query_avantes_trigger.py` already
  proved fires the Avantes, polarity correct) — OR a P1.A pulse command is added to the `.nox` in
  NOVA (the rig's `PC_Spectral*` procedures have one).
- Because `Measure()` returns immediately, **the driver owns trigger timing**. The standard CV's
  `FHWait` (5 s, before the staircase) is the arm-margin window: arm spectrometer → `Measure()` →
  pulse DIO during the wait → staircase starts on an already-armed detector. Late is safe, early
  is fatal (matches the legacy ordering).

---

## 2. Dummy-cell validation — PASSED (10 kΩ 1%, 2026-08-31)

Wiring (2-electrode): **W + WS (red) on one leg**, **RE (blue) + CE (black) on the other leg**.
(First attempt read open-circuit — an alligator clip had backed out of its connector. Reseated → correct.)

Standard CV, 0 → +1 → −1 → 0 V at 0.1 V/s:

| Applied E | Reported I | E / 10 kΩ | E/I |
|---|---|---|---|
| +0.976 V | +98.8 µA | +97.6 µA | 9878 Ω |
| +0.569 V | +57.4 µA | +56.9 µA | 9913 Ω |
| −0.987 V | −99.5 µA | −98.7 µA | 9920 Ω |

- **Units:** amps. **Sign:** current same sign as V/R — **no inversion**. **Linearity:** flat
  ~9.9 kΩ across the whole ±1 V sweep (within resistor tol + instrument accuracy).
- **Timebase:** Δt = 0.024414 s constant; array starts at ~6 s (the `FHWait`), so zero it.
- **`Scan`** stayed 1 for one full cycle — multi-cycle behaviour of idx-4 still unknown.

---

## 2b. Driver status (2026-08-31)

`AutolabPotentiostat` is **written and test-covered** in `spec_echem/potentiostat.py`, ahead of the
bench scripts, with every unresolved point as a named constant or an explicit stub. `FakeAutolab`
(`spec_echem/fakes.py`) mimics the SDK surface recorded here so the driver is testable with no
hardware — 200 tests pass. **What is still blocked: the chronoamperometry parameter map**, which
covers three of the four data types; a chrono segment raises `NotImplementedError` by design.

**`docs/autolab-driver-finishing.md` is the checklist** for turning bench results into a finished
driver. The GUI wiring is deliberately not written yet.

## 3. Nine-method contract → Autolab SDK

`potentiostat.py` contract: `open, prepare, fire, finish, stop, pump, last_data, live_data, close`.

| method | Autolab implementation |
|---|---|
| `open` | `Instrument()`, set ADX/HDW, `Connect()`, assert `IsConnected` |
| `prepare(segment)` | `proc = LoadProcedure(template_for(segment.data_type))`; write CV vertices / scan rate / conditioning V / wait into `proc.Commands[...]CommandParameters[idx].ValueAsObject` from settings + `run_number` (doping V = `start + run_number*step`) |
| `fire` | `proc.Measure()` (returns immediately); then pulse `inst.Dio.DioPortsP1[0]` during the `FHWait` window (spectrometer already armed) |
| `finish` | poll `proc.IsMeasuring` → False; read `proc.Commands["FHCyclicVoltammetry2"].Signals`; build `data.EchemData(time = CalcTime − CalcTime[0], potential = EI_0.CalcPotential, current = EI_0.CalcCurrent)`; hand to `write_echem_file()`; honour `segment.save == False` |
| `stop` | `proc.Abort()`; then `_switch_cell(inst.Ei, off)` as a backstop |
| `pump` / `live_data` | `inst.Ei.Sampler.GetSignal("WE(1).Potential"/"WE(1).Current").Value` (scalars) |
| `last_data` | the `EchemData` built in `finish` |
| `close` | `inst.Disconnect()` (belt-and-braces `_switch_cell(off)` first) |

Mode wiring: add `potentiostat_mode = autolab` alongside `external` / `toolkitpy` in
`bench.py` / `settings.py`; guard the SDK import (`import clr` + `clr.AddReference`) the way
`import toolkitpy` is guarded, auto-forcing `external` where the SDK is absent; GUI potentiostat
status indicator becomes live (green/red) in this mode; doping-potential fields **drive** the run
(like Gamry Python mode), saved to run-metadata JSON in all modes.

---

## 4. Still to resolve (do with the resistor back in, during driver work)

1. **Multi-cycle:** idx-4 is "number of (stop) crossings" (2 = one cycle). Set it to 4, confirm
   two cycles run, `ScanNumber` goes 1→2, point count doubles, and cycles concatenate in the
   `.Signals` arrays.
   → **CONFIRMED — 2026-09-03** (`bench_autolab_cv.py`, `bench_autolab_cv_2cycle.csv`). crossings
   4 → 3280 points, exactly 2× the 1640 at crossings 2; `ScanNumber` runs 1→2; one concatenated
   `.Signals` array. The driver sets idx-4 = `2 * settings['cv_cycles']`.
2. **Sampler lifecycle:** two `Measure()` runs back-to-back without reconnecting — does run 2's
   `.Signals` contain run 1's points? If so the driver must call `inst.Ei.Sampler.Reset()` (or
   reload the procedure) between segments.
   → **ANSWERED — 2026-09-03** (`bench_autolab_cv.py` phase 2; matches `bench_autolab_ca.py`). A
   second `Measure()` on the **same procedure object is INERT** — returns in 0.00 s, `IsMeasuring`
   never True, nothing runs; the `.Signals` still holds run 1's data. After an explicit
   `LoadProcedure()` **reload**, run 3 = 1640 points, byte-for-count identical to run 1 — **reload
   is a clean reset**. So the driver reloads the procedure per segment (which it does anyway);
   **no `Ei.Sampler.Reset()` needed**, and there is no cross-segment `.Signals` accumulation to
   guard against because you cannot re-measure a used object at all.
3. **`Abort()` semantics:** mid-run `proc.Abort()` — do `.Signals` hold the partial trace (spec-
   echem must discard it); is the instrument immediately ready for the next `Measure()`;
   does `IsMeasuring` go False cleanly.
   → **ANSWERED — 2026-09-03** (`bench_autolab_cv.py` phase 4). `Abort()` 5 s into a run:
   `IsMeasuring` goes False cleanly, settled in ~1.3 s. `.Signals` is **completely empty (0
   points)** — not a truncated partial, *nothing* — so a partial can never be mistaken for a
   complete run. The very next run (after reload) returns a full 1640 points; the instrument is
   ready immediately, no reconnect or power-cycle.
4. **Errored run:** does `IsMeasuring` go False (looks like success) or hang True on a fault? Is
   there a status/result object to distinguish completed / aborted / errored?
   → **`examples/bench_autolab_fault.py`** (written 2026-08-31; instructions in
   `examples/bench_autolab_fault_setup.md`). Premise: the dangerous fault is
   probably one the SDK does *not* report — an open cell or an overload finishes normally and fills
   `.Signals` with meaningless numbers. It compares a clean baseline against a software-induced
   current overload, an open cell, and a USB pull, sampling `Ei.PotentialOverload` /
   `Ei.CurrentOverload` / `IsConnected` **during** each run via the new `watch` hook in
   `autolab_common.run()`. If only the overload flags differ, polling them *is* fault detection —
   and the driver must report an overloaded segment rather than write it as if it were fine.

   **RESULT — 2026-09-03 (10 kΩ 1% dummy, `bench_autolab_fault_report.txt` + `_baseline.csv`
   / `_open_circuit.csv`):**
   - **An open cell is invisible to every status signal.** Baseline vs one lead unclipped for the
     whole run: `points` 1640 = 1640, `IsMeasuring` → False in both (a dead run looks *complete*),
     `IsConnected` stays True, `PotentialOverload` / `CurrentOverload` **never fire** — not even
     for a fully open cell. Every `Procedure.*` status field (`Status` / `State` / `Result` /
     `IsFinished` / `Aborted` / `HasError` / `Error`) is `<absent>`; **there is no status/result
     object**. The lifecycle flags cannot distinguish completed from errored.
   - **The only observable that moves is the recorded current itself:** `max|I|` 101 µA (baseline)
     → **22 nA** (open) — a factor ~4600, i.e. flat-line noise. So the driver's *only* handle on
     "dead run" is a data sanity check: flag a segment whose `max|I|` never rises out of the noise
     floor. The SOP must warn that a complete-looking file can carry no electrochemistry — same
     failure shape as the 2026-07-27 Gamry truncation bug.
   - **Overload sub-case is not reachable through the stock procedure.** `Cyclic voltammetry.nox`
     command [1] `FHGetSetValues` sets `CurrentRange = CR10_1mA` at run start and command [5]
     `FHPreCurrentRangingCV` auto-ranges before the staircase, so a small range pre-set via
     `inst.Ei.CurrentRange` never survives to the measurement (`RUN_OVERLOAD` was tried at
     `CR12_10uA` / ~10× over and the current channel still reported the true 101 µA, flag False).
     Whether `Ei.CurrentOverload` fires for a *genuine* mid-run overload is still unproven; the
     stock CV protects itself against a software range error.
5. **Trigger integration:** one script — arm Avantes (external-trigger mode) → `Measure()` →
   pulse `Dio.DioPortsP1[0]` → poll to completion → confirm the spectrum landed and is aligned
   within the `FHWait` window.
   → **`examples/bench_autolab_coacquire.py`** (written 2026-08-31). Reads the procedure's own WAIT
   duration rather than assuming 5 s, pulses at that offset, and reports the **skew** —
   `CalcTime[0]` minus the pulse offset — i.e. how far the echem t=0 sits from the optical t=0,
   plus the `PULSE_DELAY_S` that would zero it. That delay is what `fire()` will use. Good to
   roughly a tenth of a second (both clocks are host-side and `Measure()` takes ~0.3 s to return),
   which is enough to choose the delay but is not a calibration. `NUM_SPECTRA > 1` rehearses the
   real pattern — spectrum 0 triggered, the rest free-running, as `acquisition.py` does.
   `RUN_EARLY_PULSE_CONTROL` deliberately fires the edge before arming, to confirm on this hardware
   that it is missed (the rule `diag_trigger_timing.py` established on the Gamry).
6. **CA template:** doping / dedoping / pre-dedoping are chronoamperometry holds. Pick
   `Standard Nova Procedures\Chrono amperometry.nox` as the template, map its parameter indices
   the same way, decide where the trigger pulse lives.

   **RESULT — 2026-09-03 (10 kΩ 1% dummy, `examples/bench_autolab_ca.py`,
   `bench_autolab_ca_report.txt` + `bench_autolab_ca_run1.csv`):**
   - **`Chrono amperometry.nox` is a THREE-step template** — `(FHSetSetpointPotential → FHLevel
     "Record signals" → PlotsIvst) × 3`, bracketed by `FHSwitchCell` on/off, with a single
     `FHWait` before the first hold. spec-echem needs one hold per segment, so the driver drives
     step 1 and must **zero or drop steps 2–3** (leaving them runs two extra holds — harmless on a
     resistor, not on a sample). A single-step template (`Chrono amperometry fast.nox`) exists but
     uses a different `Levels` / `LevelShortSetpoint` model (fast-ADC); not adopted.
   - **Parameter map — CONFIRMED against recorded data** (applied a distinctive value, ran, checked
     the recording):

     | quantity | command | param idx | check |
     |---|---|---|---|
     | hold potential (V) | `FHSetSetpointPotential` (not the recorder) | `[0]` | set 0.35 → mean `EI_0.CalcPotential` 0.3496 V |
     | hold duration (s) | `FHLevel` | `[1]` | set 8.0 → `CalcTime` span 7.95 s (`Correctedtime` 0→7.95) |
     | sampling interval (s) | `FHLevel` | `[0]` | set 0.05 → median Δt 0.05 s (160 pts) |
     | (unused) | `FHLevel` | `[2]` bool | left untouched |
     | trigger arm window | `FHWait` | `[0]` | 5.0 s present — same window the CV path uses |

     The potential is on a **separate** `FHSetSetpointPotential` command, exactly as in the CV
     template — it is *not* a parameter of the recorder. `Commands["FHLevel"]` resolves to the
     first of the three steps. Recorder channels: `CalcTime` (procedure clock), `Correctedtime`
     (hold-relative, starts 0), `EI_0.CalcPotential`, `EI_0.CalcCurrent`, `Index`.
   - **Lifecycle: a reused procedure object is INERT.** A second `Measure()` on the same `proc`
     returned in 0.00 s with `IsMeasuring` never True — run 2 did not execute; its `.Signals` was
     just run 1's data still resident. **The driver must call `LoadProcedure()` for every
     segment** — it cannot re-`Measure()` one object. (Whether a fresh reload then behaves like the
     first run is checked by the updated phase 2 but not yet re-run on hardware; it is the same
     `LoadProcedure` call the CV path already relies on.)
7. **USB-lost liveness:** pull the USB mid-run, confirm `IsConnected` flips (for `device_lost()`).
   → covered by `bench_autolab_fault.py` run 4, which is opt-in (`RUN_USB_PULL`) and deliberately
   last: it leaves the cell energized with no software control, so dummy resistor only, and
   recovery may need a reconnect or a power cycle.

   **RESULT — 2026-09-03 (10 kΩ 1% dummy, USB pulled ~5 s into a live `Measure()`):**
   - **A mid-run USB loss wedges the SDK session.** `proc.IsMeasuring` **stuck True** — it did not
     go False and did not throw, so `autolab_common.run()`'s `while ... IsMeasuring` poll loop
     never terminated. (`timeout=120 s` would eventually have fired, but only after two minutes of
     the next point.)
   - **The Adk.x subprocess floods the console** with native errors that Python cannot suppress
     (they are written below the `clr` layer). Not a Python `print` — nothing in the loop prints.
   - **Reconnecting the cable did not recover the session.** Ctrl-C was required; the Autolab was
     then power-cycled for a clean slate.
   - No `usb_pull` fingerprint or CSV was produced — the run died inside the poll loop before
     `fingerprint()`, and `write_transcript()` never ran (it is post-`main()`).
   - **Consequence for the driver:** `device_lost()` **cannot** be a passive check inside a poll
     loop guarded on `IsMeasuring` — that flag lies on a dead link. The driver needs a hard
     wall-clock ceiling (expected run duration + small margin) that on expiry **kills the Adk.x
     process**, not merely calls `proc.Abort()`. Using `IsConnected` (throw = lost) as the loop
     guard is worth trying but is unproven here: the loop never got to act on it.
   - `RUN_USB_PULL` is left `False` in the script with a warning; do not re-run it casually.

---

## 5. Bench scripts — WRITTEN (2026-08-31), awaiting a rig session

`examples/bench_autolab_cv.py` and `examples/bench_autolab_ca.py` are committed, with the shared
proven SDK calls in `examples/autolab_common.py`. Both default to `ENERGIZE_CELL = False`, which
prints the command list and parameter map and energizes nothing — worth running even without the
resistor, and the only way the CA map gets made.

With the 10 kΩ back in:

- **`bench_autolab_cv.py`** closes items **1, 2 and 3**. Item 2 is the one that matters most: it
  runs the same procedure twice back-to-back and compares point counts, so a `.Signals` buffer that
  accumulates across runs is caught before it silently contaminates every segment after the first.
  It also re-runs after an explicit reload, to show whether reloading is a clean reset.
- **`bench_autolab_ca.py`** closes item **6**, the biggest gap — doping, dedoping and pre-dedoping
  are all chronoamperometry holds and only CV is mapped. The CA indices are unknown, so the script
  establishes them the way the CV map was established: apply a distinctive value, run, and check the
  recording agrees (potential → mean `CalcPotential`; duration → `CalcTime` span; interval → median
  Δt). Each index is reported CONFIRMED or NOT. It also reports whether the CA template has a WAIT
  command, since the standard CV's 5 s `FHWait` is what gives the driver its arm-margin window for
  the trigger — if CA lacks one, doping/dedoping trigger timing needs a separate answer.

Original spec for the CV script:

- Load the standard CV, run it energized, poll `IsMeasuring` to completion.
- Write `CalcTime`, `SetpointApplied`, `EI_0.CalcPotential`, `EI_0.CalcCurrent`, `ScanNumber` to
  a CSV under `examples/` (or `data/`).
- Print the resolved CV-staircase parameter-index map: write a distinctive value into each slot
  one at a time, re-read, and (optionally) run + read back the recorded extremes to confirm
  which slot is start / upper / lower / step / rate.
- Repeat with idx-4 bumped → resolve multi-cycle + `Scan`.
- Second run without reconnect → resolve sampler lifecycle.
- `Abort()` after ~10 s → resolve abort semantics.
- Everything to a report file, same `say()` pattern as `query_autolab_run.py`.

---

## 6. Environment / gotchas

- Run from the **64-bit `SpecEchem`** conda env: `conda run -n SpecEchem python examples/<x>.py`.
  Nothing here needs numpy / Qt.
- **`conda run` cannot take a multi-line `python -c`** ("Support for scripts where arguments
  contain newlines not implemented") — write a scratch `.py` file instead.
- `query_autolab_run.py` `ENERGIZE_CELL` is a module constant; it is committed as **`False`**.
  Flip to `True` only with a dummy / empty cell in place. The script switches the cell off in a
  `finally` and verifies `Ei.Cell`, and polls the run to completion (150 s cap → `proc.Abort()`).
- `EICellOnOff` is nested under `EI` (`EcoChemie.Autolab.Sdk.EI+EICellOnOff`), not importable
  from the namespace root.
- Nested enums / typed parameters: pythonnet 3.0 will not coerce `int`/`bool` — assign the enum
  member.

---

## 7. Uncommitted work in this session (branch `gui-dev`)

Ready to `git add` / commit / push:

- **`examples/query_autolab_run.py`** — `NOX` default set to the SDK standard CV; real fixes:
  `proc` handle = `LoadProcedure()` return; command names via `.Names` / `.IdNames`; sampler is
  `Ei.Sampler` not `inst.Sampler`; `_switch_cell()` enum-safe via `EI.EICellOnOff`;
  `run_procedure()` polls `IsMeasuring` to completion and reads `.Signals`; abort/liveness section
  reflects the `Procedure` object; `dump_members()` tolerates error strings; `_first_attr()` helper.
- **`examples/query_autolab_run_setup.md`** — `NOX` → SDK `Standard Nova Procedures\` folder;
  `PC_Spectral*` energized-run caveat (USB seize); §2 wording; new §3.5 findings; §4 empty-cell
  guidance; "UW rig" → "Metrohm rig".
- **`examples/autolab_api_report.txt`** — regenerated (passing 10 kΩ dummy-resistor run).
- **`docs/metrohm-rig-status.md`** — PGSTAT10 → PGSTAT302N; Open item #3 expanded into the
  driver-ready API map.
- **`docs/autolab-run-api.md`** — this file.
