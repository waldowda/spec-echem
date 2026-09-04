# spec-echem — TODO

Running list of planned work and deferred cleanups. (Active design/status notes live in CLAUDE.md.)

## Release gate for v0.3.0 — one bench run before merging `gui-dev` → `main` (Dean, 2026-07-27)

Almost everything since the v0.2.0 tag is additive (logging, provenance, docs). **One thing is not:**
the lost-potentiostat handling can now *stop a run*, and it has only ever executed against fakes. A
false positive would abort a good experiment mid-sample — worse than the bug it fixes. So the gate is
a **normal** run, not a failure case.

1. **No false positive (the actual gate).** A complete Python-mode run must still end
   `Run finished: done.` with every segment ✓.
2. **Timing unaffected.** Every segment logs its real cadence, so this is measurable rather than
   assumed. Compare against the 2026-07-27 baseline from before these changes:

   | Segment | mean (target 100 ms) | jitter (sd) | max |
   |---|---|---|---|
   | CV (41 pts) | 101.1 | 1.2 | 104.0 |
   | Pre-dedoping (101) | 101.2 | 1.5 | 111.4 |
   | Doping (301) | 102.0 | 4.5–5.6 | 149–170 |
   | Dedoping (301) | 101.6–101.9 | 2.6–3.5 | 127–134 |

   **Expectation: no change**, because nothing was added to the per-spectrum path. The only
   per-spectrum call is `on_tick = potentiostat.pump` (`acquisition.py:62`), which was not touched.
   `tkp.pstat_is_valid()` sits in the *Gamry* poll loop (20 Hz, its own thread) and predates this
   work; `_note_early_exit()` runs once per segment after that loop; `device_lost()` is checked once
   per segment in the worker. A rise in mean or jitter would mean something reached the acquisition
   loop that shouldn't have — investigate before tagging.
3. Optional confirmation: repeat the mid-segment USB pull — warning names the right segment, files
   still written, run stops there.
4. Banner sanity: `32-bit`, `env SpecEchem32`, `toolkitpy: yes`.

Then: bump `__version__` in `spec_echem/build_info.py` (single source — `setup.py` reads it),
`CHANGELOG` `[Unreleased]` → `[0.3.0]`, commit, `merge --no-ff` to `main`, tag `v0.3.0`, push both.
Theme for the release notes: **provenance and diagnosability**.

## Document the trigger cable build (Dean, 2026-07-14)

`docs/sop.md` §2.1 gives the trigger *endpoints* (Gamry DIGOUT0 → Avantes DB26 pin 6) but not how
the cable is **made**: Gamry-side connector and which conductor carries DIGOUT0, DB26 shell and pin-6
termination, ground/shield, cable length. That knowledge currently exists only in Dean's head and in
the single cable on the bench — if it's damaged, or a second rig is built, there's nothing to work
from. A placeholder marks the spot in the SOP. **Needs Dean's bench notes / photos.**

## Mid-run Gamry USB pull — DIAGNOSED + FIXED 2026-07-27

**What actually happens** (Dean pulled the cable during Pre-dedoping, Python mode):
`tkp.pstat_is_valid()` in the Gamry poll loop *does* notice, so the loop exits and the echem data
stops. But the thread then falls through to "capture data, write `.dta`, done" with `_error` still
`None` — **an abnormal exit was indistinguishable from the step finishing.** The spectrometer runs
its own loop and knows nothing about it, so the segment completed with a *full* spectra file beside a
*truncated* echem file, was marked ✓, and the only error appeared one segment later
(`Gamry setup for 'Doping 0' failed`) — naming the wrong segment.

**Fixed (the silent part):** `_note_early_exit()` now logs a warning naming the segment, how far into
the step the instrument stopped responding, and how many echem points were captured. Runs after the
poll loop on the Gamry thread — no acquisition-timing cost. Covered by tests.

**Also fixed — the run now stops at the segment that failed** (Dean's call: write the partial data,
then stop). `Potentiostat.device_lost()` is the seam; the worker checks it *after* writing and
emitting the segment, then breaks with `reason="error"`. Deliberately a controlled break, **not** an
exception raised from `run_one_segment`'s `finally` — that would have masked any genuine upstream
failure. External mode always answers False: it can't know, so it must not stop runs on a guess.

Result: the interrupted segment keeps its complete spectra and its partial echem, appears in Results,
and the run ends naming the right segment instead of blaming the next one.

Confirmed with the fakes end-to-end: lost-device run emits only the first segment and finishes
`error`; a healthy run still emits both and finishes `done`.

## Automated tests for the GUI layer

**Started 2026-07-27** — `tests/test_gui_layout.py` is the first coverage of `gui/`: 4 tests, headless
via `QT_QPA_PLATFORM=offscreen`, guarded with `pytest.importorskip("qtpy")` so the suite still runs
where Qt isn't installed. That resolves the "Qt in the 32-bit env" objection below — the tests skip
rather than fail.

Still only 4 of 173 tests touch `gui/`. Every bug in the 0.2.0 cycle (stale absorbance after a
wavelength re-slice, status labels outliving their data, load-before-connect, a discarded segment
still reaching the Results tab) lived in **GUI wiring**, and the core suite passed through all of
them. Highest-value targets next, all reachable with the same offscreen pattern:

- Run-tab state machine: Start → finish → Start, Stop vs Abort button enablement.
- Instrument-tab guards: load-before-connect, dark/ref dropped when the wavelength window widens.
- Results tab: segment selector across refreshes; discarded segments staying out.

## "Test your setup" probes — Avantes done, Autolab connect probe to follow (Dean, 2026-07-14)

Standalone, read-only "can this PC talk to the instrument from Python?" self-checks — useful for
anyone adopting the repo (and prompted by a colleague with a Metrohm **Autolab PGSTAT302N** + an
Avantes **AvaSpec-ULS2048i**-class spectrometer). Full plan: `~/.claude/plans/parallel-bubbling-hare.md`.
Design findings live in the `hardware-portability` memory.

- [x] **`examples/query_avantes.py` + `query_avantes_setup.md` — DONE (2026-07-14).** Opens the
      Avantes via the AvaSpec-DLL, prints serial/name/pixels/wavelength span, closes. No `spec_echem`
      import; hardened for a *different* model (`AVS_GetParameter` best-effort). Plus a Windows-only
      Metrohm/Autolab **USB-presence** scan (PowerShell, no deps). Emailable to the colleague.
- [x] **`examples/query_autolab.py` + `query_autolab_setup.md` — DONE (2026-07-22).** Read-only,
      **cell-safe connect probe** via our own ~15 lines of `pythonnet`/`clr` (NOT a dependency on the
      stale pyMetrohmAUTOLAB — credited as reference). `clr.AddReference(SDK)` →
      `from EcoChemie.Autolab.Sdk import Instrument` → set `Adk.x` + model `HardwareSetup*.xml` →
      `Connect()` → report `IsConnected` → `Disconnect()` in `finally`. **Never** `set_CellOnOff` /
      `Measure` / load a `.nox` (cell stays off — connect and cell power are separate in the SDK).
      Editable `SDK`/`ADX`/`HDW` paths with PGSTAT302N defaults. Stays in `examples/`, off the
      `potentiostat.py` seam. Graceful no-pythonnet path smoke-tested on the Mac (exit 0).
      **Still needs the colleague's Win box to confirm:** (a) pythonnet/SDK **bitness** match,
      (b) `Connect()` really leaves the cell off (verify on a dummy cell first). Built ahead of the
      original "wait for the Avantes check" gate at Dean's direction (2026-07-22).
- **Findings that make an eventual Autolab *backend* look modest, not scary** (see memory): the SDK
  is **procedure-based** — CV/CA are `.nox` procedure files you `LoadProcedure` + `Measure()`, which
  mirrors your existing **External mode** (`.GSequence` holds the recipe; Python runs it).

- **BENCH-CONFIRMED on a real Autolab (PGSTAT10, 2026-08-28) — see [`docs/metrohm-rig-status.md`](docs/metrohm-rig-status.md).**
  - `query_autolab.py` connects under **64-bit** Python → no 32/64-bit split on an Autolab rig
    (one interpreter can hold avaspec + the SDK).
  - The "no digital I/O" note above was **wrong for SDK 2.1**: `Instrument.Dio` exposes
    `DioPortsP1[]/DioPortsP2[]`, and each `DioPort` has `PortDirection {Input,Output}`, `Value:Byte`,
    `SetPortBit/GetPortBit`. Also `Ei` (potentiostat), `LoadProcedure`, `Sampler`, `Adc`, `Dac`.
  - **The trigger works.** New `examples/query_avantes_trigger.py` arms the Avantes for a hardware
    trigger and pulses Autolab DIO `DioPortsP1[0]` (P1.A) from the same Python process — the scan
    completes, polarity correct. NOVA's own spectro-EC procedures pulse the same P1.A line.
  - So a Python-drives-everything Autolab backend in `potentiostat.py` (analogue of
    `ToolkitPotentiostat`, all 64-bit, one process) is the recommended direction. Note: NOVA and
    spec-echem can't both own the Avantes over USB.

## Wavelength window is a hardcoded pixel slice — CLOSED 2026-09-04, not worth fixing

**CLOSED 2026-09-04 — no change needed, on measured data.** With the lamp on, raw counts across
all 2048 pixels: peak 24127 at 655.5 nm against a 721-count floor (pixels 0-200, below the optics
cutoff, where no light can arrive). Signal above that floor is 1120 counts at 1000 nm, 281 at 1050,
**66 at 1100, 17 at the current 1123.7 nm edge, and 0 past 1150**. Silicon QE is finished by
~1050 nm, so the existing window already extends past usable signal and widening it toward 1326 nm
would add ~388 pixels of baseline. Numbers in `bench-2026-09-04.md`.

The premise was backwards: >1100 nm is not reachable by configuration on a silicon CCD. If NIR
polaron bands matter scientifically, that is an InGaAs spectrometer, not a code change — and only
then is the rework below worth building.

Original writeup (2026-08-28), kept because the analysis is still correct — only the payoff was
wrong:

`spec_echem/spectrometer.py` `CAL_START_PX = 395` / `CAL_STOP_PX = 1659` — a fixed `[395:1660]`
pixel window applied to **every** Avantes, chosen for the original VRS2048CL-EVO's 300–1100 nm optics.
On an **AvaSpec-ULS2048L** those pixels are **410.2–1123.7 nm**, so ~1124–1326 nm is silently dropped
(a user on that rig needs >1100 nm) and <410 nm is unreachable. `set_wavelength_window()` only crops
*within* the slice, so the GUI can't offer wider.

- [~] ~~Make the calibrated pixel window bench-configurable~~ — **not doing it.** Closed on data
      2026-09-04 (above). Revisit only with a detector that can see past 1100 nm; if that day comes,
      the design Dean chose is: hard limits read per spectrometer from the device at connect, a
      default window expressed in **nm** rather than pixels, an operator window anywhere inside
      those limits, and the best part of *that* detector's range preferred over consistency between
      instruments (a changed row count on the PLU rig is acceptable).
- [x] **GUI (options A + C, 2026-08-28):** wl spin boxes clamp to the connected spectrometer's
      calibrated span and show it; a saved crop that fits a different detector (`_window_fits`) is
      parked for an explicit Apply, not silently clamped. Does not widen past the slice — see above.

## Gamry DTA converter — cleanups for when we own the parser

The conversion (raw `.DTA` → clean `.txt`) currently runs as a manual post-collection step in
`notebooks/gamry_dta_conversion.ipynb`, using the third-party `gamry_parser` library. The GUI only
reads the clean output (`spec_echem/gamry_data.py`). When we fold the converter into the package
and/or roll our own raw-`.DTA` parser, address:

- [ ] **Pre-dedoping is skipped.** The converter ignores `prededope*` files. For consistency, add
      `prededope_#N.dta → prededoping(N).txt` (pairs with `prededopingspectra(N).txt`), even though
      it's an optional/low-value step.
- [ ] **Move pre-dedoping output to a subfolder (Dean, 2026-07-10) — maybe make it the default.**
      Pre-dedoping is a precautionary baseline (confirm the film starts un-doped), NOT part of the
      doping/dedoping analysis series — always `run_number` 0, one set per run. Idea: write the
      pre-dedoping set (`prededopingspectra(0).txt` + `prededoping(0).txt` + its `.dta`) into a
      subfolder (e.g. `prededoping/`) rather than the main run folder. Benefits: (1) the main folder
      then holds only the analysis series (CV + doping + dedoping); (2) it sidesteps the
      `OECT_processing` mis-sort where `prededoping*` matches the `dedoping*` substring test and gets
      folded in as a spurious 4th dedoping cycle — a spec-echem-side fix, independent of Raj repairing
      his reader. Touches: `data.py` write path, GUI `discover_run_segments` + Results/Load-Run (still
      let you review it), and the timing tooling. Decide default-vs-opt-in, and confirm nothing
      downstream expects pre-dedoping in the main folder (coordinate with Raj alongside the
      "combined 2026 format" discussion — see [[reference-oect-processing]] in memory).
- [ ] **`+100` magic offset on the chrono `Time (s)` column.** The converter sets
      `Time = Corrected + 100`. Likely vestigial (downstream keys off `Corrected time`, which starts
      at 0). Confirm nothing depends on it, then drop or document.
- [ ] **Positional CV column drop is fragile.** CV conversion drops columns `[0,3,4,5,6,7,8]` by
      position. Select potential/current by name instead.
- [ ] **Multi-cycle CV is concatenated** into one series (loops overlay). Fine for I-vs-E plotting;
      just noted — revisit if per-cycle separation is ever needed.

## Integration-time unit — RESOLVED to milliseconds (2026-06-18)

The unit is **milliseconds**, end to end: `settings.py` key `integration_time_ms` → GUI spin value
passed straight through `set_integration_time()` → Avantes `m_IntegrationTime` (SDK defines it in
ms), with NO conversion. Confirmed on hardware 2026-06-18 — `spectrometer.py` printed
"Integration time set to 0.022 ms". The lone outlier was the CLAUDE.md doc (said "seconds") — now
**fixed** to ms. No code change needed (everything already agrees on ms).

- [x] **Label the GUI integration-time spin box "(ms)"** — DONE: the spin box already sets
      `.setSuffix(" ms")` (`instrument_tab.py`), so the unit shows inline in the field.

## Decide later (triggered)

- [ ] **Roll our own raw-`.DTA` parser** to drop the `gamry_parser` dependency — only when triggered
      (distribution/reproducibility need, `gamry_parser` breaks/unmaintained, or GUI-automated
      conversion). Check `gamry_parser` license first (likely MIT) to learn from it.

## Phase 2 — Python potentiostat (EchemToolkitPy)

`spec_echem/potentiostat.py` is implemented and hardware-validated (SpecEchem32, 2026-07-04):
`ExternalPotentiostat` = today's manual path, `ToolkitPotentiostat` = Python-driven. All four
segment types (CV + doping/dedoping/pre-dedoping) run in Python mode with golden output and the
DIGOUT0 handshake confirmed. Remaining items:

- [x] **Python-mode CV vertex potentials.** DONE (2026-06-30): settings now carry
      `cv_initial_v / cv_limit1_v / cv_limit2_v / cv_final_v` (replacing `cv_total_voltage`),
      Parameters tab exposes them, and `ToolkitPotentiostat._cv_signal()` builds the CV signal.
      Still bench-unconfirmed like the rest of the toolkitpy path.
- [x] **`curve.run()` blocks vs polls — SETTLED (2026-07-03):** `run(True)` is NON-blocking;
      `fire()` starts it synchronously and `finish()` polls `curve.running()`. No worker thread.
      DIGOUT0 HIGH confirmed to land while the spectrometer is armed (arm-then-fire handshake).
- [x] **toolkitpy API names verified on hardware (2026-07-03):** `initialize_pstat`, `signal_d_step_new`,
      `signal_r_up_dn_new`, `RcvCurve` / `ChronoCurve`, `pstat_is_valid`, `set_digital_out` all work.
- [x] In Python mode the doping/dedoping potential fields go live — DONE: the section note now
      reads "(Python mode drives these; External = reference)" (`parameters_tab.py` `POTENTIAL_NOTE`),
      replacing the old "(recorded for reference)" wording.
- [x] **Show the Gamry's custom name in "Identify".** DONE (2026-07-01): `probe_identity()` returns
      `(Pstat.label(), Pstat.serial_no())`; the Identify status shows "Gamry connected — {label}
      (serial {serial})" (falls back to serial-only if no label). Optionally add `Pstat.family()` later.

## Post-Phase-2.5 follow-ups (mirror of STATUS.md)

- [ ] **Two-thread simplification check.** The empty-echem-file bug was a signal refcount/GC issue,
      not threading. Re-evaluate whether the per-segment dedicated thread + fresh-session-per-segment +
      `acq_data()`-in-loop machinery in `potentiostat.py` is still needed, or whether a simpler
      same-thread design works. Best done on the instrument box (hardware-tested). The `acq_data()`
      poll in the run loop is flagged in-code as unconfirmed-necessity.
- [ ] **First real-sample test (the gold standard).** Real polymer sample, real dark (lamp blocked) +
      reference (blank, lamp on), full multi-cycle sequence in one Start; then confirm the output
      analyzes cleanly in Raj's `OECT_processing`. External mode is real-test-ready today; Python mode
      is ready now that echem capture landed.

## Echem plotting in the GUI (Phase 1)

- [x] **Live echem timing SIGNED OFF (Dean, 2026-07-07).** Ran the CV live/off A/B ×2 pairs on the
      incremental-redraw build. Across all 4 runs (~160 spectra) NO 119-style spikes; steady-state
      (spectra 2–40) all within ~100–103 ms, ~±1.5 ms of the 100 ms target, live indistinguishable
      from off. The only outlier is the first interval (spectrum 0→1, ~86–97 ms) — the trigger-armed
      first-measurement settling, present in every run regardless of live/off, and harmless (it's
      timestamped). Conclusion: the incremental redraw (`update_live_line`) removed the cadence
      perturbation; the live plot is timing-safe. Minor future-if-ever: the ~first-interval dip could
      be looked at for perfectly-uniform-from-start sampling, but it's a startup artifact, not the plot.
- [x] Wire CV (I vs E) + chrono (I vs t) plots into the Results review area — DONE (2026-07-06):
      absorbance (optical) and electrochemistry are shown side by side; the Results tab loads each
      segment's clean echem `.txt` via `spec_echem.gamry_data` (`data.echem_txt_path` locates it),
      and shows a friendly note when there's no echem file (e.g. External mode). CV → I-vs-E,
      chrono → I-vs-t.
- [x] **Live echem graph during a Python-mode run — DONE (2026-07-06).** The Run tab now shows a live
      echem trace (CV → I-vs-E, chrono → I-vs-t) that updates mid-segment, above the last-completed
      absorbance — so you can watch a CV and ABORT before committing to a long doping sweep. Mechanism:
      the Gamry thread's existing `acq_data()` poll now stashes each snapshot (`potentiostat.live_data()`);
      a 400 ms QTimer on the GUI thread reads it and redraws (never touches the acquisition thread, so
      timing/50 ms budget is safe). **BENCH-VERIFIED + SIGNED OFF 2026-07-07** (see the item above) —
      after the redraw was made incremental (`update_live_line`), a first full-redraw version DID perturb
      the spectra cadence (max 119 ms / jitter 3.5) via GIL contention; the incremental version does not.
      Verification tooling shipped:
      (a) every segment logs its actual cadence — "X cadence: mean … (target …), min/max, jitter(sd), n"
      — from hardware timestamps, to the status pane + .log; (b) a "Live echem" checkbox on the Run tab
      to A/B the same run plot-on vs plot-off and compare the logged cadence. If jitter is bad, the 400 ms
      redraw interval is a one-line knob (make it tunable). Possible follow-ups: live *absorbance* too
      (needs per-spectrum emit from the worker); drop the now-purposeful `acq_data()` poll into the
      two-thread review.
- [x] **Review a past run without re-running — DONE (2026-07-09).** Results tab gained a "Load Run…"
      button: pick a saved run folder → `discover_run_segments` reverse-maps the filenames and
      `read_spectra_absorbance` rebuilds each absorbance matrix from disk (both in `spec_echem/data.py`),
      populating the Results view (absorbance + echem) exactly as a live run does. Previously the tab only
      showed the current session's run ("run a sequence first" on a cold launch). Guarded against loading
      mid-run. ("Open Data Folder" is unchanged — it opens the folder in Explorer, a filesystem shortcut,
      not an in-GUI viewer.)
- [x] **Configurable wavelength window — crop noisy lamp edges — DONE (2026-07-10).** Opt-in,
      driver-level (`m_StartPixel`/`m_StopPixel`); default = full window (output unchanged). Instrument
      tab: wl_min/max + Conservative/Balanced/Liberal + "Suggest from test-abs" + "Apply". Recommendation
      in `spec_echem/spectral_range.py` (rolling-σ of the test-abs, ref-net corroboration; knob is an
      absolute **Max noise (OD)**, default 0.010). IMPLEMENTATION = pure **software crop** (2026-07-10):
      the `m_StartPixel/m_StopPixel` hardware approach was abandoned (mis-mapped on real hardware — axis
      jumped to ~1050-1160 nm, graphs blank); `set_wavelength_window` now crops the calibrated `[395:1660]`
      window by index (`_crop`), never touching measconfig. Instrument-tab plots/loads crash-proofed.
      Downstream confirmed 2026-07-10: a **cropped run** (400.5–1049.7 nm, salt blank) reads cleanly
      through `OECT_processing`. **DONE + RELEASED to main.**
- [ ] **Expose the other hard-coded `measconfig` fields (future, Dean 2026-07-10).** Now that the window
      is config-driven, `_create_measurement_config` could expose smoothing, **saturation detection**
      (ties to the linearity-check item below), and the averaging model instead of hard-coding them.
- [x] **Linearity check — DONE + hardware-validated (2026-07-13).** Instrument tab has a `Linearity Check`
      box beside Spectrometer Settings: ramps integration time, tracks one fixed peak pixel, fits the linear
      region (with intercept), and recommends a working integration time. Manual Start/Stop/Steps, a
      "Find saturation" helper (bisects to the real threshold), and "Use recommended".
      `spec_echem/linearity.py`; run with the reference in place.
      **Key finding from the real run:** the detector tracks the fit to within ~1% right up to the hard ADC
      clip, so a deviation-only criterion never fires and puts the working point at ~94% of full scale. The
      recommendation therefore takes the **tighter of two constraints** — 5% below the limit of linearity,
      or peak counts ≤ a **max-fill** fraction of full scale. Defaults **85% fill / 2% tolerance** confirmed
      good by Dean on hardware (halogen + ND: saturates ~0.11 ms → recommends ~0.0885 ms).
- [ ] **Linearity: per-source ramp defaults (Dean, 2026-07-13).** Saturation depends strongly on the
      light source — Dean has a halogen+ND (saturates ~0.11 ms) and an Avantes **AvaLight**. Start/Stop are
      manual and "Find saturation" auto-adapts, so switching sources already works; only the *default*
      Stop (0.15 ms) is tuned to the halogen. If source-swapping becomes routine, remember the last-used
      Start/Stop per source in settings rather than shipping one default.

## GUI UX — Instrument tab potentiostat controls (Dean, 2026-07-05)

- [x] **Reconsider the layout — DONE (2026-07-07, `329ed23`).** Instrument tab now pairs the
      Spectrometer Connection and Potentiostat boxes side by side, and the potentiostat mirrors the
      spectrometer: "Connect Potentiostat" button (renamed from "Identify"), gated by the Python
      radio, with a green/red status dot ("● Connected — Gamry Duck (serial …)").
- [ ] **Auto-verify the Gamry when Python mode is selected.** Now a small follow-on to the pairing:
      selecting "Python" could auto-run the Connect probe (call `on_connect_pstat`) instead of
      requiring the click, so the green "● Connected — …" appears on switch. Weigh against the probe's
      cost (opens/closes a toolkitpy session) and doing it silently on every toggle.

## GUI UX — data folder guidance / existing-folder warning (Dean, 2026-07-06)

- [x] **Warn when the target run folder already exists.** DONE (2026-07-06): Start now checks
      `{data_root}/{data_folder}`; if it exists and contains files, a confirm dialog (default Cancel)
      warns that continuing may overwrite a previous run. Prompted by Dean actually overwriting older
      data by forgetting to rename the folder — silent `mkdir(exist_ok=True)` clobbered same-named files.
- [ ] **Still to do: a short student-facing guide/tooltip** that Save location = the PARENT and Data
      folder name = the subfolder the app creates (the 1–2 sentence quick-start noted 2026-07-03), to
      also head off the *double-nesting* case (browsing into a run folder, then typing its name too).

## Future — light-source control (AvaLight-HAL-S-Mini2 halogen source) (Dean, 2026-07-07)

- [ ] **Software-control the AvaLight-HAL-S-Mini2 halogen lamp from the GUI (future).** The compact
      Avantes halogen source has a shutter (and, depending on config, a TTL/software-controllable
      one). Controlling it would let the app **automate the dark/reference workflow** that's manual
      today: close the shutter → collect Dark, open the shutter → collect Reference / measure — no
      more "block the lamp by hand," fewer operator errors, and a reproducible lamp state per run.
      Could also enforce lamp warm-up/stability before a run.
      - **Investigate the control path first:** does this unit have the TTL-shuttered variant, or a
        manual shutter only? Likely options: the Avantes electronics (AS7010) digital I/O / a lamp
        TTL line, or an `avaspec` SDK call — check the SDK for lamp/shutter/digital-out control
        (parallels the DIGOUT trigger work). If it's manual-shutter-only, this needs the TTL option
        or an external relay, so confirm the hardware before designing UI.
      - **UI (once controllable):** a shutter/lamp toggle in the Instrument-tab Dark/Reference area;
        optionally auto-close for "Collect Dark" and auto-open for "Collect Reference".
      - Ties to the existing dark/ref Collect/Save/Load controls and the linearity/saturation TODO
        (a stable, known lamp state helps keep reference counts in the linear regime).
