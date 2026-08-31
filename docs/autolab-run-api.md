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
2. **Sampler lifecycle:** two `Measure()` runs back-to-back without reconnecting — does run 2's
   `.Signals` contain run 1's points? If so the driver must call `inst.Ei.Sampler.Reset()` (or
   reload the procedure) between segments.
3. **`Abort()` semantics:** mid-run `proc.Abort()` — do `.Signals` hold the partial trace (spec-
   echem must discard it); is the instrument immediately ready for the next `Measure()`;
   does `IsMeasuring` go False cleanly.
4. **Errored run:** does `IsMeasuring` go False (looks like success) or hang True on a fault? Is
   there a status/result object to distinguish completed / aborted / errored?
5. **Trigger integration:** one script — arm Avantes (external-trigger mode) → `Measure()` →
   pulse `Dio.DioPortsP1[0]` → poll to completion → confirm the spectrum landed and is aligned
   within the `FHWait` window.
6. **CA template:** doping / dedoping / pre-dedoping are chronoamperometry holds. Pick
   `Standard Nova Procedures\Chrono amperometry.nox` as the template, map its parameter indices
   the same way, decide where the trigger pulse lives.
7. **USB-lost liveness:** pull the USB mid-run, confirm `IsConnected` flips (for `device_lost()`).

---

## 5. Bench script to write next — `examples/bench_autolab_cv.py`

A single command for the next bench session (needs the 10 kΩ back in):

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
