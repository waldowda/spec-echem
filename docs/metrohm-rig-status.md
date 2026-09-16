# Metrohm rig — bring-up status & findings (2026-08-28)

> **SUPERSEDED for anything about the driver, timing or `Ei`.**
> This file remains the record of the 2026-08-28 *bring-up* (box, env, wiring, the
> four bench-check steps). For where the rig actually stands, read
> [`bench-2026-09-09.md`](bench-2026-09-09.md) — chrono holds now run from `Ei` with
> no procedure, cell-on to trigger is 19–30 ms, and `Ei.Current` turns out not to be
> a live property. Then [`autolab-driver-finishing.md`](autolab-driver-finishing.md)
> for the trip plan.

Companion to [`metrohm-bench-check.md`](metrohm-bench-check.md), which is the *procedure*.
This file is the *result*: what was found bringing spec-echem up on a fresh Win11 box with an
Avantes spectrometer + a Metrohm Autolab, and where things stand for continued development
(mostly on the macOS source repo).

## The rig

| | |
|---|---|
| Box | fresh Win11, user `Ginger Lab`, 64-bit `SpecEchem` conda env (Python 3.13) |
| Spectrometer | **AvaSpec-ULS2048L-USB2**, 2048 px, SensorType 10, min integration **1.048 ms MEASURED** (2026-09-16) |
| Potentiostat | **Metrohm Autolab PGSTAT302N** (corrected 2026-08-31; earlier notes said PGSTAT10). Connects with the SDK's `Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml`. Serial/modules on this unit still to be re-confirmed at the bench. |
| Autolab SDK | **2.1**, at `C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\` |
| NOVA | 2.1, also installed |

## Bench-check results

| Step | Result |
|---|---|
| 1 — spectrometer alone (`query_avantes.py`) | ✅, 174–1327 nm raw span |
| 2 — spectrometer as the app sees it | ✅ launch banner `drivers avaspec: yes \| toolkitpy: no` |
| 3 — Autolab connect, 64-bit (`query_autolab.py`) | ✅ connects — **no 32/64-bit split on this rig** |
| 4 — trigger path (`query_avantes_trigger.py`, NEW) | ✅ Autolab DIO `Port_A` → Avantes trigger fires, polarity correct |

### What made Step 2 work

Commit 26fea45's `SPEC_ECHEM_AVASPEC_DLL_DIR` preload **does not help** the 9.14.0.0 `avaspec.py`,
whose `ctypes.WinDLL("./avaspecx64.dll")` resolves against the CWD and never consults the loaded
modules by base name (confirmed on this box). What fixed it: the three CLAUDE.md vendored-file edits
to the env's `site-packages\avaspec.py` — comment out `import globals` and `from PyQt5.QtCore import *`,
and load the DLL by **bare name** after `os.add_dll_directory(...)`. `SPEC_ECHEM_AVASPEC_DLL_DIR` is
honored by that edit (with a hardcoded fallback), so it still means something.

### Machine-local config written here (gitignored)

`config/bench.ini`: `data_root`, `potentiostat_mode = external`, and — because the lab-default
`integration_time_ms = 0.088` / `lin_start_ms = 0.022` are below this detector's 1.048 ms floor
(MEASURED 2026-09-16; the SDK rejects them outright, code -11) — `integration_time_ms = 1.5`, `lin_start_ms = 1.1`, `lin_stop_ms = 25`. These are
placeholders; the working value comes from the Linearity Check once the (very bright) lamp is
attenuated enough not to saturate near the floor.

## NOVA already does synchronized spectro-EC on this rig

The user's existing `.nox` procedures (`Documents\Nova 2.1\Procedures\PC_SpectralChronoAmperometry_*`,
`PC_spectralCA_CV_BIGPROCEDURE`) show NOVA itself driving **both** instruments:

- `ExecCommandAvantesStart` / `AvantesStop`, `SpectroSingleShot` ("Software triggered spectroscopy",
  USB), `SpectroTriggered` ("fast options", hardware-triggered burst). Spectrometer is embedded in the procedure.
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

1. **Wavelength window above ~1124 nm — CLOSED 2026-09-04, no change needed.** Measured with the
   lamp on: signal above the 721-count floor is 66 counts at 1100 nm, 17 at the current 1123.7 nm
   edge, and 0 past 1150 nm — silicon QE is done by ~1050 nm, so the existing window already reaches
   past usable signal. Widening would add ~388 pixels of baseline. Numbers in
   `bench-2026-09-04.md`; the description below is kept for the day an InGaAs detector makes it
   real. Original writeup: `spec_echem/spectrometer.py` `CAL_START_PX = 395` / `CAL_STOP_PX = 1659` is a fixed
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

3. **Autolab backend** — `examples/query_autolab_run.py` characterized the run API against the
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

---

## Minimum integration time — MEASURED on this detector (2026-09-16)

`examples/probe_min_integration.py`, this rig's `AvaSpec-ULS2048L` (SensorType 10,
2048 px). **The ~1.05 ms figure this document carried was right**, and now
rests on the hardware rather than a datasheet.

| stage | result |
|---|---|
| 1. stated minimum | **not exposed** under either spelling — same as the other detector |
| 2. `AVS_PrepareMeasure` accepts | **1.048 ms**; 1.0 ms and everything below **rejected, code -11** |
| 3. counts vs exposure | linear from **1.05 ms** (`counts = 683 + 1361·t`, within 1% to 5 ms) — but **1.048 ms itself is not honored** |

### It rejects, it does not clamp

Below 1.048 ms the SDK refuses the request outright rather than quietly using its
minimum. That is what made the old symptom so opaque: nothing raised, the measurement
simply never arrived, and the poll loop timed out looking like a dead instrument.

**This is a ~100x spread between the two detectors in this project** — 0.009033 ms on the
SensorType 22 part against 1.048 ms here. Anything that hardcodes an exposure is wrong on
one of them.

### The accepted minimum is NOT the usable minimum

Stage 3 needed the beam attenuated first — with the cell as it stood, peak counts were
pinned at 65535 at **every** exposure the detector accepts, confirming the suspicion
recorded above that the lamp is too bright to work near the floor. (The mean-over-2048-
pixels column the probe used to print concealed this: most pixels see no lamp, so a
clipped band still reads as a modest mean creeping upward with exposure, which is
indistinguishable from dark current. The probe now reports **peak** and says so.)

With attenuation in place, the counts are unambiguous:

| requested (ms) | peak counts | `683 + 1361·t` predicts |
|---|---|---|
| **1.048** | **3555** | **2111** |
| 1.05 | 2115 | 2111 |
| 1.06 | 2137 | 2125 |
| 1.10 | 2180 | 2179 |
| 1.20 | 2334 | 2315 |
| 1.50 | 2725 | 2724 |
| 2.00 | 3374 | 3405 |
| 5.00 | 7490 | 7488 |

**From 1.05 ms upward the detector is linear to within 1%.** At exactly 1.048 ms — the
value the bisect returns as the smallest `AVS_PrepareMeasure` accepts — it returns 3555
counts where the line says 2111, which is the equivalent of **~2.1 ms of integration, 
roughly double what was asked for**.

Reproducible, and not a first-scan artifact: four consecutive scans at that setting agree
within 2%, and revisiting it *after* every longer exposure gives the same answer. Only
that one value misbehaves; 1.05 ms, one step up, is exact.

**Confirmed at two light levels differing ~17x**, which rules out anything optical — the
factor is exactly two, and it is the INTEGRATION that doubles, not the counts (the counts
ratio differs between the two because each fit has its own offset):

| | low intensity | high intensity |
|---|---|---|
| fitted response | `683 + 1361·t` | `-300 + 23250·t` |
| 1.048 ms, measured | 3555 | 48170 |
| the line predicts | 2109 | 24066 |
| **equivalent exposure** | **2.11 ms** | **2.085 ms** |
| **× requested** | **2.01** | **1.99** |

So the detector runs exactly double the requested integration at this one setting. A
firmware timing behavior at the boundary, not a lamp or optics effect.

So the bisect's boundary value is **accepted but not honored**, and the practical minimum
is the first tidy value above it. This is why `init()` rounds the probe result UP before
exposing it: `tidy_detector_floor()` turns 1.04803466796875 into 1.05, which steps off the
one exposure this detector gets wrong. That rounding is a safety property, not cosmetics.

A caution for the other detector: its accepted minimum (0.009033 ms) WAS verified genuine
against `counts = 112 + 26051·t`. Whether its exact boundary value misbehaves the same way
has not been tested — the check there ran from 0.009 ms, not from the bisect's last
accepted value.

### What now follows the hardware

- `AvantesSpectrometer.init()` probes the floor and caches it (~70 ms MEASURED, against
  ~128 ms for the rest of `init()`, and bit-reproducible across runs), rounded UP to the
  first value the detector actually honors.
- Connect **raises** `integration_time_ms` and `lin_start_ms` to the floor when they sit
  below it, and never lowers a value already above it.
- `set_integration_time()` refuses a sub-floor exposure with a message naming the request,
  the floor and the serial — instead of a poll-loop timeout.
- The floor is recorded per detector in `config/bench.ini` under `[detector.<serial>]`,
  and travels into each run's metadata JSON.

The operating value is rounded **up** to three significant figures — 1.048034… → **1.05 ms**.
Two reasons, and the second only emerged from stage 3: the bisect resolves to 1e-4 ms, so
the raw figure quotes fifteen digits of a number known to four; and the raw value is the
one exposure this detector does not honor. The raw value is still logged and stored.
