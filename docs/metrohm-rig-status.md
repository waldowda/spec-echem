# Metrohm rig — bring-up status & findings (2026-08-28)

Companion to [`metrohm-bench-check.md`](metrohm-bench-check.md), which is the *procedure*.
This file is the *result*: what was found bringing spec-echem up on a fresh Win11 box with an
Avantes spectrometer + a Metrohm Autolab, and where things stand for continued development
(mostly on the macOS source repo).

## The rig

| | |
|---|---|
| Box | fresh Win11, user `Ginger Lab`, 64-bit `SpecEchem` conda env (Python 3.13) |
| Spectrometer | **AvaSpec-ULS2048L-USB2**, serial **1404154U1**, 2048 px, min integration ~1.05 ms |
| Potentiostat | **Metrohm Autolab PGSTAT302N** (corrected 2026-08-31; earlier notes said PGSTAT10). Connects with the SDK's `Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml`. Serial/modules on this unit still to be re-confirmed at the bench. |
| Autolab SDK | **2.1**, at `C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\` |
| NOVA | 2.1, also installed |

## Bench-check results

| Step | Result |
|---|---|
| 1 — spectrometer alone (`query_avantes.py`) | ✅ serial 1404154U1, 174–1327 nm raw span |
| 2 — spectrometer as the app sees it | ✅ launch banner `drivers avaspec: yes \| toolkitpy: no` |
| 3 — Autolab connect, 64-bit (`query_autolab.py`) | ✅ connects — **no 32/64-bit split on this rig** |
| 4 — trigger path (`query_avantes_trigger.py`, NEW) | ✅ Autolab DIO `Port_A` → Avantes trigger fires, polarity correct |

### What made Step 2 work

Commit 26fea45's `SPEC_ECHEM_AVASPEC_DLL_DIR` preload **does not help** the 9.14.0.0 `avaspec.py`,
whose `ctypes.WinDLL("./avaspecx64.dll")` resolves against the CWD and never consults the loaded
modules by base name (confirmed on this box). What fixed it: the three CLAUDE.md vendored-file edits
to the env's `site-packages\avaspec.py` — comment out `import globals` and `from PyQt5.QtCore import *`,
and load the DLL by **bare name** after `os.add_dll_directory(...)`. `SPEC_ECHEM_AVASPEC_DLL_DIR` is
honoured by that edit (with a hardcoded fallback), so it still means something.

### Machine-local config written here (gitignored)

`config/bench.ini`: `data_root`, `potentiostat_mode = external`, and — because the lab-default
`integration_time_ms = 0.088` / `lin_start_ms = 0.022` are below this detector's ~1.05 ms floor and
hang the poll loop — `integration_time_ms = 1.5`, `lin_start_ms = 1.1`, `lin_stop_ms = 25`. These are
placeholders; the working value comes from the Linearity Check once the (very bright) lamp is
attenuated enough not to saturate near the floor.

## NOVA already does synchronized spectro-EC on this rig

The user's existing `.nox` procedures (`Documents\Nova 2.1\Procedures\PC_SpectralChronoAmperometry_*`,
`PC_spectralCA_CV_BIGPROCEDURE`) show NOVA itself driving **both** instruments:

- `ExecCommandAvantesStart` / `AvantesStop`, `SpectroSingleShot` ("Software triggered spectroscopy",
  USB), `SpectroTriggered` ("fast options", hardware-triggered burst). Spectrometer serial
  **1404154U1** is embedded in the procedure.
- The sync line is an **Autolab digital output**: `Dio_0` / `HDio`, written as **`P1.A:Write`**
  (connector P1, bank A, output), pulsed (`HOptionGetSetValuesPulse`), followed by `WaitMicroSeconds`.
  DIO is also used for lamp/shutter TTL ("make sure the lamps are on TTL").

Consequence: **NOVA and spec-echem cannot both own the Avantes** (both grab it over USB). For
spec-echem to drive the spectrometer, a NOVA procedure would have to run *only* the Autolab CV/CA +
the P1.A DIO pulse, with no Avantes commands — OR spec-echem drives the Autolab too (next section).

## The Autolab SDK exposes far more than the bench doc assumed

`metrohm-bench-check.md` / the old TODO say "no Autolab driver; External mode; no digital I/O".
Reflecting `EcoChemie.Autolab.Sdk` (2.1) from 64-bit Python shows otherwise:

| `Instrument.` | use |
|---|---|
| `Ei` | potentiostat control |
| `LoadProcedure(path)` / `Sampler` | run a `.nox`, read signals |
| `Dio` → `DioPortsP1[]`, `DioPortsP2[]`, `Value:Byte` | digital I/O |
| `DioPort` → `PortDirection {Input,Output}`, `Value:Byte`, `SetPortBit/GetPortBit` | per-port |
| `Adc`, `Dac`, `Fra`, `Mux`, `BAModule` | the rest |

So a **Python-drives-everything Autolab backend** (`spec_echem/potentiostat.py`, the analogue of
`ToolkitPotentiostat`) is feasible and all 64-bit / one process — `Dio.DioPortsP1[0]` is the P1.A
trigger line the trigger probe already exercised. This is the recommended direction over the
NOVA-runs-echem "External mode" path on this rig.

## Open items (for macOS-side development)

1. **Wavelength window above ~1124 nm — the user needs it, and it's blocked by a hardcoded pixel
   slice.** `spec_echem/spectrometer.py` `CAL_START_PX = 395` / `CAL_STOP_PX = 1659` is a fixed
   `[395:1660]` pixel window applied to *every* Avantes — it was chosen to bound the original
   **VRS2048CL-EVO**'s 300–1100 nm optics. On this **ULS2048L** those pixels map to **410.2–1123.7 nm**,
   so everything from ~1124 nm to the detector's 1326 nm is silently discarded, and nothing below
   410 nm is reachable. `set_wavelength_window()` only crops *within* that slice, so the GUI can't
   offer wider.
   - Recommended: make the calibrated pixel window **bench-configurable** (like the wavelength
     window already is) — e.g. `cal_start_px` / `cal_stop_px` (or `cal_wl_min` / `cal_wl_max`) in
     `config/*.ini`, **default = the current `[395:1660]`** so every existing rig's 8-column output
     is byte-identical unless a bench opts in. Then this box widens `cal_stop_px` toward 2047.
   - Validate against `tests/golden/` and re-confirm a widened run reads through
     `rajgiriUW/OECT_processing` before shipping.

2. **GUI wavelength limits — options A + C landed this session** (`gui/tabs/instrument_tab.py`):
   on connect the wl spin boxes are now clamped to the connected spectrometer's calibrated span and
   that span is shown in the status; a saved crop that clearly belongs to a different detector
   (`_window_fits`, <50% overlap) is parked in the boxes for an explicit Apply instead of being
   silently clamped. Does **not** address item 1 (still 410–1124 for this unit).

3. **Autolab backend** — `examples/query_autolab_run.py` characterised the run API against the
   PGSTAT302N + SDK 2.1 on 2026-08-31 (full listing: `examples/autolab_api_report.txt`; full
   handoff incl. dummy-cell validation, contract mapping, open items and the next bench script:
   **[`autolab-run-api.md`](autolab-run-api.md)**). Ready to write the `potentiostat.py` driver from:
   - `LoadProcedure(path)` **returns** the `Procedure` object (no `inst.Procedure`).
   - `Procedure.Commands` is a named command list; numeric `CommandParameter.ValueAsObject` accepts a
     write + reads back → a standard `.nox` is re-parameterized per cycle (CV vertices / step / scan
     rate / conditioning V / wait all writable). Address commands by `Commands['<IdName>']`, e.g.
     `FHCyclicVoltammetry2`, `FHSetSetpointPotential`.
   - `Procedure.Measure()` is **non-blocking** (returns ~0.3 s, `IsMeasuring` True) → poll
     `Procedure.IsMeasuring`; no dedicated thread. `MeasureAsync` is absent.
   - Abort/pause: `Procedure.Abort()` / `Hold()` / `Continue()` / `Skip()`. Liveness:
     `AutolabConnection.IsConnected`.
   - Recorded data: `Commands['FHCyclicVoltammetry2'].Signals` (a `CommandParameterSignalList`),
     read after `IsMeasuring` goes False. Channels seen: `CalcTime` (s), `EI_0.CalcPotential` (V),
     `EI_0.CalcCurrent` (A), `SetpointApplied`, `ScanNumber`, `Index` — each a `List<Double>` via
     `ValueAsObject`. Maps directly to `data.EchemData(time, potential, current)`.
   - Live scalars mid-run: `Ei.Sampler.GetSignal("WE(1).Potential").Value` (scalar, not array).
   - Cell: `Ei.CellOnOff` takes the nested enum `EI.EICellOnOff.On` / `.Off` (pythonnet 3.0 rejects
     bool/int). `Ei.Cell` (bool) reads back the state.
   - No DIO command in the SDK standard CV → `fire()` pulses `Dio.DioPortsP1[0]` (trigger line
     confirmed by `query_avantes_trigger.py`), or a P1.A command is added to the `.nox` in NOVA.

4. **`avaspec.py` on a fresh box** — the 26fea45 env-var mechanism is ineffective for wrappers that
   load `"./avaspecx64.dll"`; the vendored-file edit is still required. Worth folding the "bare name
   + add_dll_directory" load into the documented setup rather than presenting the env var as
   sufficient.

## Session changes on `gui-dev`

- `8f606ca` — `measure_timing()` poll timeout + arm-return check (was hanging the GUI thread on a
  sub-floor integration time); `MplCanvas.show_message()` / `show_linearity()` text no longer
  overflows a small canvas.
- this commit — `examples/query_avantes_trigger.py` (new); genericized paths in `query_autolab.py`;
  GUI wavelength options A+C; this doc; TODO/STATUS updates. `+` tests. Suite: 181 passed, 1 skipped
  (the no-hardware connect-failure test skips when a real spectrometer is attached).
