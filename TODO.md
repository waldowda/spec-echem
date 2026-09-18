# spec-echem — TODO

Running list of planned work and deferred cleanups. (Active design/status notes live in CLAUDE.md.)

## Sanitising the repository history — the user, 2026-09-15, future

Requested: *"I wonder about sanitizing the repo at some point in the future and resetting the
repo so to speak. That history thing is a challenge."*

**What is exposed** (names only; the data files' contents are clean 8-column format):

- `tests/golden/<name>/` — 7 tracked files whose FOLDER name encodes a blend ratio and
  electrolyte. On `origin/main` and `origin/gui-dev` since `7c49c02` (June 2026).
- `notebooks/SpecEchem Avantes 0.996-20250717.ipynb` — composition in 18 places.
- Earlier commits on `gui-dev` from 2026-09-14/15, before `c4f1316` genericised them.

**Options, roughly in increasing order of disruption:**

- [ ] **Rename forward only.** Rename the golden folder, update the three tests that read
      it (`test_data_format.py`, `test_gamry_data.py`, `test_spectra_reader.py`), commit.
      Cheap and safe, but **history keeps the old name** — this fixes what a browser sees
      today, not what `git log -p` sees.
- [ ] **Rewrite history with `git filter-repo`** (the maintained replacement for
      `filter-branch`). Genuinely removes the names from every commit. Costs: every SHA
      changes, so a force-push is required, anyone who has cloned keeps the old objects,
      open PRs break, and GitHub may keep unreferenced objects cached until asked to
      purge them.
- [ ] **Fresh repository from the current tree.** Cleanest result, loses all history —
      including the bench-session record, which is a real cost here since those commit
      messages carry the MEASURED findings.

**What NONE of these can undo:** the Zenodo archive (DOI 10.5281/zenodo.17221314) is a
snapshot taken at release and is not affected by anything done to GitHub. Any existing
clone or fork keeps what it has.

**Recommendation:** decide what the repository is FOR first. If it stays public as an NSF
outcome, rename-forward plus the new CLAUDE.md rule is probably proportionate — the
exposure is a folder name, not data or interpretation. Reach for `filter-repo` only if
the composition itself must genuinely not be discoverable.

## Richer models — the user, 2026-09-15, explicitly for later

Design settled in [`docs/analysis-design.md`](docs/analysis-design.md); not started.

- [ ] **1. Joint fit of polaron and π–π* with SHARED τ — do this FIRST.** One parameter
      vector, shared timescales, per-band amplitudes and baselines; stack the traces into
      one residual vector, no new solver. Start on the rungs where the prefactor signs
      agree (+0.2…+0.5 V), where it should work; let it break at +0.6/+0.7 V.
- [ ] **2. Capture the polaron → bipolaron leakage derivations.** They are the
      specification for the τ₃ term and are not in this repo. `private-notes/` if they
      carry sample specifics, `docs/` otherwise. Needed BEFORE implementing τ₃.
- [ ] **3. Tri-exponential**, τ₃ for the bipolaron channel, with τ₁ and τ₂ pinned by
      step 1. **The order is not a preference — τ₃ is not identifiable on its own.** The
      bipolaron band is invisible here (silicon QE ends ~1050 nm; MEASURED 66 counts at
      1100 nm, 0 past 1150 — it needs InGaAs), so bipolaron formation shows only as
      polaron absorbance going MISSING. A free tri-exponential fitted to the polaron band
      alone will trade a wrong τ₃ against a wrong τ₁ and land somewhere plausible.
- [ ] **Re-check the model ranking per system.** biexp beating stretched 4:1 is a fact
      about P3HT 90:10 / KPF₆ at 800 nm, not a default to carry forward.

## Constrain prefactors to the same sign — the user, 2026-09-15, not started

Requested: *"Sometimes a poor fit switches prefactor signs. We should consider adding a check
box for same sign-ness."*

`FitResult.mixed_amplitude_signs` already FLAGS it (`46fec8e`). What is missing is the
option to forbid it, for when the flip is the optimizer wandering rather than real
competing processes.

- [ ] A checkbox on the Analysis tab, something like **"require same-sign prefactors"**,
      off by default — competing processes are real and must stay fittable.
- [ ] `curve_fit` bounds cannot express "same sign" directly. The clean way is to fit
      TWICE, once with every B bounded ≥ 0 and once with every B bounded ≤ 0, and keep
      whichever has the lower residual. Two cheap fits, no new solver, and it reports an
      honest failure if neither converges.
- [ ] Applies to any sum-of-parts model, so key it off the `B*` names in `MODELS`
      rather than hardcoding biexp.

**Evidence the flag tracks physics, not fit noise** (MEASURED on
`the 20250710 reference run`, biexp): at the polaron band (800 nm) the prefactors agree in
sign at +0.2…+0.5 V and go MIXED at +0.6 and +0.7 V; at π–π* (550 nm) they agree at
**every** rung including those two. the user predicted exactly that asymmetry — a bipolaron
steals from the polaron band without creating a competing process at π–π*. A numerical
instability would have shown at both wavelengths.

## Minimum integration time — BOTH detectors probed, and wired (2026-09-16)

MEASURED on both parts in this project. The SDK exposes NO minimum under either
spelling on either one, so `minimum_integration_time()` bisects what
`AVS_PrepareMeasure` accepts.

| detector | SensorType | floor | how it refuses below it |
|---|---|---|---|
| the fast part | 22 | **0.009033 ms** | n/a — accepts, and genuinely integrates |
| `AvaSpec-ULS2048L` | 10 | **1.048 ms** | **rejects outright, code -11** |

A **~100x spread**, so nothing may hardcode an exposure. On the fast part the accepted
minimum was verified genuine against the detector's own integral (`counts = 112 +
26051·t`, within 1% from 0.009 to 0.1 ms). On the ULS2048L the question does not arise:
sub-floor requests are refused, not clamped.

- [x] ~~Probe the other detector~~ — done 2026-09-16, full write-up in
      [`docs/metrohm-rig-status.md`](docs/metrohm-rig-status.md). The ~1.05 ms that doc
      carried was RIGHT; it now rests on hardware rather than a datasheet.
- [x] ~~Wire it~~ — `init()` caches the floor (~70 ms MEASURED, bit-reproducible);
      Connect **raises** `integration_time_ms` and `lin_start_ms` to it when they sit
      below, and never lowers a value already above it.
- [x] ~~Scale `lin_stop_ms` from the floor~~ — done, via `LIN_STOP_FLOOR_SPANS`, but see
      the open item below: it is a starting guess, not a derived constant.
- [x] ~~Reject out-of-range exposures with a clear message~~ — `set_integration_time()`
      names the request, the floor and the serial.
- [x] ~~Tie the floor to the specific spectrometer~~ — recorded per serial in
      `config/bench.ini` under `[detector.<serial>]`, and written into each run's
      metadata JSON. The hardware is still asked at every Connect; the stored value is
      only a fallback, because a stale floor fails silently in exactly the way the probe
      exists to prevent.

### Still open

- [x] ~~Finish stage 3 on the ULS2048L~~ — done 2026-09-16 with the beam attenuated,
      and it changed the answer. **The accepted minimum is not the usable minimum.** At
      exactly 1.048 ms the detector accepts the request and integrates ~2.1 ms, about
      double; from 1.05 ms up it is linear to within 1% (`counts = 683 + 1361·t`, out to
      5 ms). Reproducible across four scans and on a revisit after every longer exposure,
      so not a first-scan artifact. `init()` therefore rounds the probe result UP before
      exposing it, which steps off the one exposure this detector gets wrong.
- [x] ~~Does the fast detector's boundary value misbehave too?~~ **No — MEASURED 2026-09-18 on the VRS2048CL-EVO**: at exactly 0.009033 ms mean counts sit 0.7% off the line through the 0.02–0.1 ms rows (1378.5 vs 1369), where the ULS2048L integrated double. The doubling is that detector's firmware; rounding up stays as a harmless precaution. Original note: Its accepted minimum
      (0.009033 ms) was verified genuine against `counts = 112 + 26051·t`, but that check
      started at 0.009 ms rather than at the bisect's last accepted value, which is the
      one that failed here. Same test, four scans at the exact boundary against the fitted
      line. Cheap, and it decides whether the rounding-up is a general rule or a
      single-detector workaround.
- [ ] **`LIN_STOP_FLOOR_SPANS = 8.0` has no physics behind it.** The two known detectors
      do not share one multiplier: 0.15/0.009033 is ~17x, while a 1.048 ms part wants
      ~8 ms, or ~8x. The stop is really "where curvature appears", which is lamp- and
      optics-dependent, not a property of the detector. It gets the ramp into runnable
      territory; `Find saturation` is what finds the real top.
- [ ] **`Find saturation` cannot advise "lower Start" at the floor.** Its message says
      "Lower Start, or attenuate the light", and on a rig that saturates at the minimum
      exposure the first half is impossible. Worth detecting that Start is already at the
      floor and saying only the half that can be acted on.

## Getting data and figures OUT (2026-09-15) — the ask, not started

Requested: *"eventually it would be good to get data out in a nice format such as the
graphs... One other option I think might be nice is to export an Igorpro file I can open
in Igor with all the data and graph info setup to print, since Igor has such amazing
graphing and formatting capabilities."*

Suggested order — **the numbers matter more than the pictures**, because a figure is
where editing stops:

- [x] ~~CSV of the fit results~~ — **done**. *All fits…* now carries every fitted
      parameter with its SD (columns built from `MODELS`, so they follow the model),
      plus y(0), ⟨τ⟩, 95% CI, point count and the residual split. **Copy as CSV** and
      **Save CSV…** on the dialog.
- [ ] **2. Figure export via `NavigationToolbar2QT`.** ~5 lines per canvas and it brings
      pan/zoom/save for free. **Prefer SVG or PDF** — vector, so it drops into
      Illustrator or Igor without resampling.
- [ ] **3. HDF5**, settled WITH Raj first (see the questions in `private-notes/`). The
      vendor-neutral layout serves his Jupyter analysis, and an Igor loader can read it
      directly — which stops the Igor work becoming a third independent format.
- [ ] **4. Igor Text (`.itx`), not `.pxp`.** `.itx` is plain text and documented, and
      carries both waves AND `X` command lines Igor executes on load, so one file can
      create the waves and `Display` them. `.pxp` is an undocumented binary container,
      not worth reverse-engineering. Testable without Igor, since it is text.

      **Emit well-named, correctly-scaled waves plus a minimal `Display`, and let the user
      style it in Igor.** Generating `ModifyGraph` calls means guessing at formatting
      conventions he already has, and he would end up fighting the generated styling
      rather than using Igor's strengths. Roughly a day's work for the wave export.

## In-GUI analysis — open items (2026-09-14)

Tab 5 works and is validated on real data (see STATUS.md). What is left:

- [ ] **Decide whether the auto wavelength should lock across a ladder.** It is chosen
      PER SEGMENT and drifts 783 → 808 nm monotonically with potential on
      `the 20250710 reference run` — probably a real red-shift of the polaron band with
      doping level, not noise, which is why it was not silently locked. The ladder title
      says `(AUTO, VARIES)` when the points do not share a wavelength. the call.
- [ ] **`FIT_MAX_TAU_SPANS = 10` and `FIT_SD_REJECT_FRACTION = 0.5`** are judgement
      calls that now have real data behind them but have not been tuned against a
      second sample.
- [ ] **A second probe wavelength for the bipolaron band.** Requested: bipolaron formation
      eats the polaron population at high doping, so τ at 800 nm is not purely polaron
      growth. The tab already fits any wavelength typed; what is missing is fitting two
      at once and comparing.
- [x] ~~Density of states from the CV~~ — **v1 built**: Tab 4 → Optical view →
      *Density of states (CV only)*, film geometry on Tab 2. Last cycle, directions
      separate, dQ/dV fallback without a volume.
- [ ] **Absolute energy axis for the DOS.** Reference to vacuum via an internal
      ferrocene standard: E = −(E vs Fc/Fc⁺ + 4.8 eV). Needs a reference-offset setting
      and a measured ferrocene E½. Note the 2026 absolute-calibration work puts
      ferrocene at 4.94 ± 0.05 eV, and scales differ by up to 0.3 eV — and a PSEUDO
      reference cannot place an absolute scale without a ferrocene calibration in the
      same electrolyte. Until then the axis is relative and the manual says so.
- [ ] **DOS v2 — the capacitive baseline.** Double-layer charging is not density of
      states, and v1 subtracts nothing. Needs a decision about how, and it must be
      visible on the plot.
- [x] ~~Confirm the electroactive area~~ — it is the IMMERSED coated area, one side.
      Default 1.6 cm² = 2 cm immersed × 0.8 cm wide (the slide is cut narrower than a
      1 cm cell). Check the depth each run; spin-coating coverage is the real
      uncertainty.
- [ ] **Scan-rate check** — run several rates and confirm i/v collapses onto one
      curve. A bench protocol, not code, but the GUI should not present a DOS that has
      never had it.
- [x] ~~Log y-axis on the ladder~~ — done, `log y` checkbox beside the Show toggles,
      with a title hint when needs-review points are off scale.
- [x] ~~A table of fit data across all potentials~~ — done, **All fits…** on the
      Analysis tab. CSV export of the same rows is still item 1 under "Getting data
      and figures OUT".

## Release gate for v0.3.0 — one bench run before merging `gui-dev` → `main` (the user, 2026-07-27)

**PASSED 2026-09-18** on the Gamry Reference 600 rig, build `0.2.0+215.gc0ddfad`: 8 segments,
`Run finished: done`, no false potentiostat-lost stop. Every cadence mean is exactly
**100.0 ms**, not the July 101–102 — expected, because spectra moved onto an absolute
grid on 2026-09-04 (`cd69030`), after this baseline was taken. The grid removes the drift
(July's 301 spectra spanned ~30.5 s against 30.0 s of electrochemistry) at the cost of
wider per-interval jitter (3.3–8.9 ms sd; min as low as 23.6 ms is a catch-up after a late
spectrum). The table below is the pre-grid baseline — kept for the record, no longer the
comparison. The same run exposed the ladder overshoot fixed in `0828fd3`.

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

## Document the trigger cable build (the user, 2026-07-14)

`docs/sop.md` §2.1 gives the trigger *endpoints* (Gamry DIGOUT0 → Avantes DB26 pin 6) but not how
the cable is **made**: Gamry-side connector and which conductor carries DIGOUT0, DB26 shell and pin-6
termination, ground/shield, cable length. That knowledge currently exists only in the head and in
the single cable on the bench — if it's damaged, or a second rig is built, there's nothing to work
from. A placeholder marks the spot in the SOP. **Needs the bench notes / photos.**

## Mid-run Gamry USB pull — DIAGNOSED + FIXED 2026-07-27

**What actually happens** (the user pulled the cable during Pre-dedoping, Python mode):
`tkp.pstat_is_valid()` in the Gamry poll loop *does* notice, so the loop exits and the echem data
stops. But the thread then falls through to "capture data, write `.dta`, done" with `_error` still
`None` — **an abnormal exit was indistinguishable from the step finishing.** The spectrometer runs
its own loop and knows nothing about it, so the segment completed with a *full* spectra file beside a
*truncated* echem file, was marked ✓, and the only error appeared one segment later
(`Gamry setup for 'Doping 0' failed`) — naming the wrong segment.

**Fixed (the silent part):** `_note_early_exit()` now logs a warning naming the segment, how far into
the step the instrument stopped responding, and how many echem points were captured. Runs after the
poll loop on the Gamry thread — no acquisition-timing cost. Covered by tests.

**Also fixed — the run now stops at the segment that failed** (the call: write the partial data,
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

## "Test your setup" probes — Avantes done, Autolab connect probe to follow (the user, 2026-07-14)

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
      original "wait for the Avantes check" gate at the direction (2026-07-22).
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
      the design the user chose is: hard limits read per spectrometer from the device at connect, a
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
- [ ] **Move pre-dedoping output to a subfolder (the user, 2026-07-10) — maybe make it the default.**
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

- [x] **Live echem timing SIGNED OFF (the user, 2026-07-07).** Ran the CV live/off A/B ×2 pairs on the
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
- [ ] **Expose the other hard-coded `measconfig` fields (future, the user 2026-07-10).** Now that the window
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
      good by the user on hardware (halogen + ND: saturates ~0.11 ms → recommends ~0.0885 ms).
- [ ] **Linearity: per-source ramp defaults (the user, 2026-07-13).** Saturation depends strongly on the
      light source — the user has a halogen+ND (saturates ~0.11 ms) and an Avantes **AvaLight**. Start/Stop are
      manual and "Find saturation" auto-adapts, so switching sources already works; only the *default*
      Stop (0.15 ms) is tuned to the halogen. If source-swapping becomes routine, remember the last-used
      Start/Stop per source in settings rather than shipping one default.

## GUI UX — Instrument tab potentiostat controls (the user, 2026-07-05)

- [x] **Reconsider the layout — DONE (2026-07-07, `329ed23`).** Instrument tab now pairs the
      Spectrometer Connection and Potentiostat boxes side by side, and the potentiostat mirrors the
      spectrometer: "Connect Potentiostat" button (renamed from "Identify"), gated by the Python
      radio, with a green/red status dot ("● Connected — Gamry Duck (serial …)").
- [ ] **Auto-verify the Gamry when Python mode is selected.** Now a small follow-on to the pairing:
      selecting "Python" could auto-run the Connect probe (call `on_connect_pstat`) instead of
      requiring the click, so the green "● Connected — …" appears on switch. Weigh against the probe's
      cost (opens/closes a toolkitpy session) and doing it silently on every toggle.

## GUI UX — data folder guidance / existing-folder warning (the user, 2026-07-06)

- [x] **Warn when the target run folder already exists.** DONE (2026-07-06): Start now checks
      `{data_root}/{data_folder}`; if it exists and contains files, a confirm dialog (default Cancel)
      warns that continuing may overwrite a previous run. Prompted by the user actually overwriting older
      data by forgetting to rename the folder — silent `mkdir(exist_ok=True)` clobbered same-named files.
- [ ] **Still to do: a short student-facing guide/tooltip** that Save location = the PARENT and Data
      folder name = the subfolder the app creates (the 1–2 sentence quick-start noted 2026-07-03), to
      also head off the *double-nesting* case (browsing into a run folder, then typing its name too).

## Future — light-source control (AvaLight-HAL-S-Mini2 halogen source) (the user, 2026-07-07)

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

## Future — "import a .nox and run it" as a first-class feature (the user, 2026-09-06; not now)

Today the Autolab templates are two paths in `config/bench.ini` (`autolab_nox_cv`,
`autolab_nox_ca`), hand-edited in NOVA, with the driver writing known parameters into known
commands. The idea: let the GUI **import an arbitrary `.nox`**, show what is in it, and run it.

Why it is plausible rather than fanciful: this is all any Python wrapper does. Two independent
projects (`shuayliu/pyMetrohmAUTOLAB`, `helgestein/metrohm_autolab_python`) and the vendor's own
manual converge on load-a-procedure-and-execute-it — there is no lower-level waveform API to find.
So an import-and-run feature is not a workaround, it is the SDK's actual model surfaced to the user.

What it would need:

- **Introspection at import.** `Commands.IdNames` and, per command, `CommandParameters.IdNames`
  (see the read-only pass) — enough to render an editable list without knowing the technique.
- **A mapping from procedure to spec-echem segment.** The driver currently assumes "CV template ⇒
  DATA_TYPE_CV" and writes named parameters. An arbitrary `.nox` needs the user to say what it is,
  or the segment type inferred from the commands it contains.
- **Where the trigger goes.** A hand-supplied procedure may or may not carry a digital-output step;
  `_require_dio_step()` already refuses the mismatch, and that check becomes load-bearing.
- **Reading the data back generically.** `cmd.Signals` names vary by command
  (`EI_0.CalcCurrent` on a staircase, the same on `FHLevel`) — needs a channel map rather than the
  current fixed three.

Real appeal: it would let a user run *their* electrochemistry with spec-echem's spectroscopy,
instead of only the two techniques the driver knows. It also subsumes the single-step-CA and
template-swap work, which are both special cases of "point it at a different `.nox`".

Prerequisite: the read-only introspection pass, which is already planned.


## After 2026-09-09 (the `Ei` session) — see `docs/bench-2026-09-09.md`

Closed that day: trigger pin found, parameter keys measured, abort validated on hardware,
`FHWait` removed, edge/recorder alignment fixed, `Ei` mode built and working.

Open, roughly in order of value:

- **`Ei` mode has never seen a film.** Only a 10 kΩ resistor, which has no transient — so
  the exact thing `Ei` was built for, capturing the start of a doping current, is still
  unobserved. This is the next real test.
- **`examples/bench_ei_sampling.py` is written but never run.** Phase A (no cell) times a
  single `Ei` read, which sets the floor for any grid faster than 100 ms. Everything above
  100 ms is already known to work.
- **The GUI silently overwrites a completed run.** It destroyed `20260904_test1` and the
  original `20260909_test8` in one day. Wanted: at Start, if the target folder already holds
  data files, a "folder already contains N files — overwrite?" confirm. `write_run_metadata`
  or the Run tab's Start handler is the seam.
- **Live spectra plotting during a run — WANTED, but gated on the loop timing budget
  (the user, 2026-09-11).** Today plots update post-segment only; `CLAUDE.md` records that
  as a deliberate simplification, with a throttled 2-5 Hz redraw noted as feasible.
  Rendering happens on the GUI thread, never the acquisition thread, so in principle it
  cannot touch the timing budget — the acquisition loop never blocks on the GUI, and
  Qt's queued connections absorb a busy GUI thread.

  **The open question is whether that still holds now.** When that note was written the
  loop was exposure + ~30 ms; it is now exposure + ~50 ms of `pump()` in a 100 ms slot,
  and `20260909_test12` / `20260911_test1` both show occasional stretched intervals.
  Emitting a 1220-point array per spectrum at 10 Hz adds cross-thread traffic on top of
  that. So: **measure before building.** Establish the per-spectrum headroom first (the
  `next_deadline` item below is the same question from the other side), then decide
  whether live plotting fits at 10 Hz, at a throttled 2-5 Hz, or only on a decimated
  trace. If it does not fit, a single live number (latest current, spectrum count) costs
  nothing and gets most of the reassurance.

- **`next_deadline()` compensates for the measurement but not for `pump()`.** This is
  the mechanism behind the outliers, and the more precise version of the item below.
  `acquisition.py` calls `on_tick()` (= `potentiostat.pump`) once per iteration, for
  every spectrum including spectrum 0, positioned between `measure()` and the pacing
  sleep. But `measure_cost` is timed around `spec.measure()` ALONE, so the ~50 ms
  `pump()` spends is unaccounted work sitting inside the slot:

  ```
  100 ms slot = 22 ms exposure + ~50 ms pump + ~28 ms sleep
  ```

  It holds at 100 ms because the deadline is ABSOLUTE (`anchor + (j+1)*delta_time -
  measure_cost`), so an overrun takes the "already late — go straight on" branch and
  the next deadline is still measured from the anchor. Hence mean 100.0 ms with
  occasional stretched intervals rather than cumulative drift — exactly what
  `20260909_test12` showed.

  **Candidate fix:** fold the pump into `measure_cost`, i.e. time the whole iteration
  (`measure()` + `on_tick()`) rather than just the measurement, so the deadline
  compensates for the real per-iteration work. Cheap to write, but it CHANGES PACING
  BEHAVIOUR, so it needs a bench run to confirm — compare cadence mean/min/max against
  `20260909_test12` on the same settings before believing it. **the user to test next
  session.**

  Note in `Ei` mode `pump()` is not a side job — it IS the echem acquisition, so the
  echem grid inherits the spectra's jitter by construction. That is honest (timestamps
  record when the sample was actually taken) and keeps the two series paired, but it
  means anything done here moves the echem timing too.

- [x] ~~**`pump()` costs ~50 ms per spectrum and the cadence advisory does not know it.**~~ **(a) FIXED 2026-09-16.**
  MEASURED 2026-09-09 (`examples/bench_ei_sampling_report.txt`): `Sampler.Sample()` is
  25.0 ms and each latch read is 5.0 ms — the reads are NOT free. `pump()` does five
  reads plus the sample, so ~50 ms of a 100 ms slot, against a `SPECTRUM_OVERHEAD_S` of
  30 ms that predates all of it. Two pieces of work: (a) teach
  `spectrum_cost_seconds()` / the cadence advisory that a potentiostat costs something,
  or it will approve a grid that cannot hold; (b) throttle the overload and
  `IsConnected` checks from every spectrum to ~1 Hz, worth ~15 ms — but FIRST establish
  whether the overload flags latch until read or can clear between checks, because
  throttling a self-clearing flag loses events.

  **(b) is now unblocked: the flags SELF-CLEAR** (MEASURED 2026-09-16,
  `examples/probe_overload_report.txt`) — so throttling the overload check to ~1 Hz
  WOULD lose events, and that part must not be done. The `IsConnected` check can still
  be throttled; the overload read cannot.

  **(a) is FIXED.** `spectrum_cost_seconds()` and `suggest_scan_averages()` take a
  `potentiostat_mode` and charge `POTENTIOSTAT_POLL_S` for it (Autolab 50 ms; the Gamry
  path is left at zero rather than guessed, since it polls its own curve and has never
  been measured — a wrong number quoted to the user would be worse than a missing one).
  The advisory now shows the term in its breakdown. Predicts 102 ms for the run below,
  against 103.9-106.0 measured. The evidence that prompted it:

  **(a) was OBSERVED, not just predicted.** The GUI run `20260916_test1` (1.1 ms x 20
  averages, 100 ms slot, `Ei` mode, idle machine) came out at a mean of **103.9-106.0 ms
  across all five chrono segments**, never at the 100 ms target. The arithmetic closes
  exactly: the segment logs put `EDGE -> spectrum 0` at **56 ms** — matching
  `spectrum_cost_seconds`'s prediction of 52 ms — and 56 + `pump()`'s ~50 ms is ~106 ms,
  which is what the loop actually ran at. So the advisory told the user 52 ms against a
  100 ms slot, i.e. comfortable, while the real per-loop cost EXCEEDED the slot. The
  science is unaffected (every spectrum carries its own Avantes timestamp), but the
  advisory currently approves grids that cannot hold.

- **Cadence outliers in `Ei` mode.** Means hold at 100.0 ms but single intervals of 249.8 ms
  (spectra) and 214.9 ms (echem) appeared in `20260909_test12`. `pump()` now does a
  `Sample()` USB round trip it did not before. Measure before changing anything.
- **`autolab_setup_lag_cv_s = 1.16` rests on one CV segment** and came in ~45 ms over. Worth
  a second CV before tuning. Only affects `procedure` mode.
- **CV still pays the procedure preamble** (~1.16 s). It does not need to be fixed — cycle 1
  is discarded by practice — but if it ever does, the route is a minimal `.nox` (delete the
  `FHGetSetValues` / `FHSetSetpointPotential` / `FHSwitchCell` commands in NOVA and set them
  from Python first), not an `Ei`-generated staircase.
- **`UseFastOptions` suppressed the ADC dither** (settled `sd` 3.8e-10 → exactly 0) with no
  timing benefit, so it was reverted. If it is ever wanted for another reason, that side
  effect is unexplained and worth understanding first.
- **Trigger cable build** (connector, pinout, shielding) is still undocumented — only its
  endpoints, and now its pin: bit 0 / pin 1 of P1.Port_A.


## After 2026-09-11 (first film data) — see `docs/bench-2026-09-11.md`

Closed that day: `Ei` mode validated on real samples; six GUI/driver defects found by
running an actual experiment rather than by testing.

- **Re-run a fresh film on `CR10_1mA`.** The single highest-value item. Every film run on
  2026-09-11 used `CR09_10mA`, nothing anywhere exceeded 625 µA, and that range has a
  MEASURED +1.6 µA zero offset — 10–100% of the settled currents recorded. The peaks are
  fine; the steady-state currents are not quantitatively trustworthy. Dedoping −0.5 V,
  and do not go past +0.7 V (the test film does not survive +0.8 V — film A never recovered).
- [x] ~~`examples/probe_overload.py`~~ — **written and run 2026-09-16**; transcript in
  `examples/probe_overload_report.txt`. It answers all three questions, and the first
  answer dissolves the CV mystery:
  - **An overloaded range does NOT clip the reading.** On `CR13_1uA` (1 µA full scale)
    asked for 10 µA it reported **9.488 µA** — 10× full scale, only ~6% low — while
    flagging on 40/40 samples. So "every CV flags and nothing clips" was never a
    contradiction: the instrument flags and keeps reporting a plausible number.
  - **It is the applied POTENTIAL that goes wrong.** Measured potential drooped to
    0.0968 V against 0.0998 V on the control, same 0.100 V setpoint — the amplifier
    cannot carry the load, so the potentiostat loses control. Both readings stay
    self-consistent, so **an overloaded segment cannot be recognised from its own data.**
    The flag is the only evidence, which is why `Ei.Current` being a latch mattered.
  - **The flag SELF-CLEARS**: 0/20 with the cell off afterwards, 0/40 driven again on a
    good range. So `pump()` needs no re-arming, a flag means the overload is happening
    NOW, and a CV that flags has a real excursion worth chasing.
- **Record the range's zero offset, do NOT subtract it** (the user, 2026-09-16:
  *"Why not just record what is measured? I generally don't like to change raw data."*).
  Measured across six ranges on the dummy at a 0.000 V setpoint —
  `examples/probe_zero_offset.py`, transcript in `probe_zero_offset_report.txt`:

  | range | full scale | cell OFF | % FS | cell ON | % FS |
  |---|---|---|---|---|---|
  | `CR09_10mA` | 10 mA | −1.084 µA | −0.0108% | −1.068 µA | −0.0107% |
  | `CR10_1mA` | 1 mA | −0.1125 µA | −0.0113% | −0.1167 µA | −0.0117% |
  | `CR11_100uA` | 100 µA | +15.5 nA | +0.0155% | +12.9 nA | +0.0129% |
  | `CR12_10uA` | 10 µA | +1.5 nA | +0.0153% | −0.8 nA | −0.0078% |
  | `CR13_1uA` | 1 µA | +0.2 nA | +0.021% | −2.2 nA | −0.22% |
  | `CR14_100nA` | 100 nA | +0.3 nA | +0.28% | −2.2 nA | −2.24% |

  **THE OFFSET IS NOT A STABLE CONSTANT.** `CR09_10mA` measured **+1.605 µA** on
  2026-09-11 and **−1.084 µA** today — same range, same instrument, opposite sign. So a
  stored offset applied at analysis time would be worse than none at all, and an earlier
  claim in this file that it is "stable and additive" and could be subtracted was wrong.
  So was the "~0.02% of full scale" rule read off two points: the signs differ BETWEEN
  ranges (CR09/CR10 negative, CR11/CR12 positive), so matching magnitudes were a
  coincidence. It predicted `CR10_1mA` at +0.2 µA; it measures −0.11 µA.

  - [ ] **Measure it per run and write it to the run metadata JSON**, alongside the
        instrument identities already there. Cell off at a 0.000 V setpoint on the run's
        own range, a second or two, mean and sd. The data stays exactly as the
        instrument reported it; the offset travels beside it so it can be applied in
        analysis, or not, with the numbers visible either way.
  - [ ] **Never subtract it from a recorded trace.** Raw data is the contract.
  - [ ] **The fine ranges have an absolute noise floor.** Scatter is ~3.5 nA (cell off)
        and ~1.7 nA (cell on), INDEPENDENT of range, so the `CR13`/`CR14` offsets above
        sit inside their own noise and are not significant. Nothing finer than about
        `CR12` buys real resolution — consistent with OMIEC currents being mA to µA.

- **Is this a calibration question?** (the user, 2026-09-16: *"On Gamry, I calibrate the
  potentiostat and cables frequently. I don't know about Autolab."*) The sign flip
  between sessions is what a drifting, re-zeroed instrument looks like, not a fixed
  hardware artifact — so this is the leading explanation for the offsets.
  - [ ] Find out what NOVA offers for Autolab calibration, and how often it is expected.
        A documentation/vendor question, not an API one.
  - [ ] **The SDK exposes no calibration surface.** Nothing matching
        calib/offset/gain/zero/trim/adjust/diagnos on `Instrument`, `Ei` or
        `AutolabConnection` (2026-09-16). Absence there is not proof the instrument
        cannot be calibrated — NOVA may own it — but spec-echem cannot trigger or verify
        one, so it cannot warn that a calibration is overdue.

- [x] ~~Which current ranges does this instrument actually have?~~ — probed
  2026-09-16 (`examples/probe_current_ranges.py`). The SDK enum defines **20** members
  and the GUI offers all of them, but this instrument accepts only **11**:
  `CR15_10nA` through `CR05_20A`. The four finest (`CR19_1pA`…`CR16_1nA`) and the five
  coarsest (`CR04_40A`…`CR00_1000A`) are refused with "Invalid argument".
  **Accepted is not the same as physically deliverable** — 20 A full scale is what the
  hardware-setup file admits, not a claim the base instrument can source it — and it is
  certainly not a current any polymer film survives. Ranges above 10 mA are now marked
  `[!] high current` in the Parameters tab, with the reason in the tooltip: an oversized
  range removes the overload protection rather than merely measuring coarsely.
- [ ] **Offer only the ranges the connected instrument accepts.** They could be probed
  at Connect the way the spectrometer's floor is, instead of offering twenty and letting
  nine fail at run time. Needs the instrument connected before the Parameters tab is
  populated, which is not the current order, so it is a real change rather than a tweak.

- **Is the CV's auto-ranging picking something too sensitive?** `FHPreCurrentRangingCV`
  presumably probes at the initial potential, where a film draws almost nothing. If so
  the fix is a NOVA edit (fixed range in the CV template), not a code change. Unverified —
  but now the leading explanation, since 2026-09-16 establishes that the flag is truthful
  and self-clearing, so those CV flags reported real excursions that the recorded sweep
  could not show.

## Live CV plot shows points the recorded data does not (the user, 2026-09-16)

Reported from the GUI run `20260916_test1`: *"Some times a data point gets plotted
slightly off but in the final data it is not off."* So it is a DISPLAY glitch on the
Run tab's live echem trace, and the saved `CV.txt` is clean.

That split is itself the clue, because the two come from different places. For a CV
(always procedure mode) the recorded trace is read from `command.Signals` AFTER the run,
while the live trace is built from `_live_samples`, which `pump()` accumulates during it.
Only the live path can glitch without the file glitching.

**A screenshot settled it, and ruled out the first theory.** The glitch is a WEDGE:
the trace runs along the line, jumps to a point OFF it, and comes back. That shape is
the whole diagnosis.

A stale (E, I) PAIR cannot draw it. Both values would be old, so the point would land
ON the line — just backwards along it — and the trace would retrace itself invisibly.
An OFF-line point requires E and I to come from DIFFERENT instants: a MISMATCHED pair,
not a stale one. So the discarded `sample_ei()` return value, the first candidate here,
is not the mechanism.

**It is an X ERROR — the point is displaced horizontally, not vertically** (the
user's reading, and the clearest way to describe it). The current is CORRECT; the
potential plotted against it is from an earlier instant. So one coordinate is stale, not
both: a partial staleness rather than the whole-sample staleness first proposed.

The direction confirms it. On this sweep E runs negative, so a stale E is LESS negative
and the point lands to the RIGHT of the line — which is the way the wedge opens.
Off the screenshot (`20260916_test1`, 100 mV/s, 10 mV steps, 10 kOhm dummy): the point
near E ~ -0.28 V carries the current belonging to E ~ -0.305 V, a displacement of a
couple of sample intervals.

That the CURRENT is the trustworthy coordinate is what pins the cause to the read ORDER
rather than to a failed refresh.

That matches the read order exactly. `pump()` builds the sample as

    (t, float(inst.Ei.Potential), float(inst.Ei.Current))

reading Potential FIRST. A latch refresh landing between those two property reads yields
the old potential with the new current — which is what is plotted.

**Why CV only.** A straddle needs something OTHER than `pump()` refreshing the latch. In
procedure mode the running `.nox` has its own recorder doing exactly that; in `Ei` mode
Python's `Sample()` is the only refresher, so there is nothing to straddle. That is
consistent with the glitch appearing on the CV and nowhere else.

**And "nothing else refreshes in Ei mode" is PROVEN, not assumed** — by `20260909_test11`,
which recorded one identical row forever because `Sample()` was never called. Had anything
else been refreshing the latch, those rows would have varied. Nothing does, and in `Ei`
mode no procedure is loaded, so there is no recorder to do it.

**So this is DISPLAY-ONLY, and minor.** The saved `CV.txt` comes from `.Signals` and is
unaffected; `steps(N).txt` cannot straddle. Worth fixing for the plot's sake, not worth
instrument time to chase further. (the user, 2026-09-16: *"I think it is minor given the
recorded data."*)

- [ ] **Close the straddle window.** Re-read the potential after the current and
      discard (or re-take) the sample when it moved — a pair that straddled a refresh is
      not a measurement of anything. Cheap, and it guards `Ei` mode too, where the
      "nothing else refreshes" assumption is untested.
- [ ] **Still use `sample_ei`'s return value.** Not the cause here, but `pump()`
      discarding it means a genuinely failed refresh is appended as a duplicate point
      with a fresh timestamp. Harmless on a CV; in `Ei` mode `_live_samples` IS the
      segment's saved echem data, so that one would reach `steps(N).txt`.
- [ ] **Consider not building the live CV trace from the latch at all.** In procedure
      mode the recorder's own arrays are the authoritative source and are what the saved
      file uses; sampling the latch alongside it is what creates the race.

**A dead end worth not repeating.** `20260916_test1` has 8-34 consecutive identical
(E, I) row pairs per chrono segment (up to 11% of rows in `steps(0)`), which looks like
evidence for a stale-sample path and is not: those segments held a fixed potential across
a 10 kOhm resistor, so the true current was constant, and quantisation of a steady signal
produces exactly that pattern. On a dummy the two are indistinguishable — and the
screenshot shows the mechanism is a mismatch rather than staleness anyway.

- [ ] **Optional, if ever curious how often:** log the potential read before AND
      after the current for each live sample; every differing pair is a straddle. Not
      needed to fix it — the guard above is correct whatever the rate — and not worth
      bench time on its own.

## HDF5 output alongside the ascii files (the user, 2026-09-11)

**Why:** disk. A single long run already writes ~1.6 M rows per spectra file, and the
8-column tab-separated format stores every wavelength value again for every time point.
HDF5 stores the wavelength axis once and the absorbance matrix as a typed array — an
order of magnitude smaller, and faster to read back. Raj's `OECT_processing` already
works this way, so there is a reference implementation and a downstream consumer.

**Constraint that makes this safe:** the 8-column format is *not* replaced. It is what
`rajgiriUW/OECT_processing` reads today and `docs/data-format.md` says DO NOT CHANGE.
H5 is written **in addition**, so nothing downstream notices until it chooses to.

Sketch:

- One `.h5` per run folder, not per segment — the point is to stop repeating the
  wavelength axis, and one file per run also collapses a 14-segment ladder into a
  single object.
- Datasets: `wavelength` once; per segment an absorbance matrix (wavelength x time),
  the raw counts, the time axis, and the echem trace.
- Attributes: the full run metadata JSON that already exists, plus `build_id()` — a run
  folder is self-documenting today and the H5 should be too, on its own.
- Read path: a loader beside `read_spectra_absorbance()` so the Results tab can open
  either.

### Raj already has the writer — `oect_processing/specechem/uvvis_h5.py`

101 lines, `save_h5(data, filename)` / `convert_h5(h5file)`. Layout:

```
/potentials                  the doping potential ladder
/charge                      optional
/<potential>/data            absorbance matrix (wavelength x time)
/<potential>/index           wavelength axis
/<potential>/columns         time axis
/current/data|index|columns
```

One group per potential, each a pandas DataFrame decomposed into data/index/columns.
It is a round-trip format for his objects rather than a general schema.

**It maps onto ours almost directly.** `compute_absorbance()` already returns a DataFrame
indexed by wavelength with corrected-time columns — exactly `data`/`index`/`columns` — and
his group-per-potential is our segment-per-doping-step. Writing a compatible file is a
small job, not a design exercise.

**The important observation:** his `save_h5` is built from a `UVVis` object, which is the
product of *parsing our ascii*. So the chain today is: we write ~150 MB of text, he parses
it, he writes ~6 MB of H5. The ascii is an intermediate nobody wants — it is only the
handoff format. Writing his layout from the acquisition side removes the parse step for
anyone who wants H5.

### Two questions to settle WITH RAJ before building (Requested: needs a conversation)

1. **Is the H5 an analysis convenience or the archival record?** His file keeps absorbance
   and current only — no raw counts, no dark, no reference. Our 8-column format carries all
   three (columns 3/4/5), and they would have nowhere to go in his layout. Matching him
   exactly means the H5 is lossy and the ascii stays the archive; making it a superset means
   it is no longer his format. That decision drives everything else.
2. **Metadata.** His file has no attributes at all — no `build_id`, no settings snapshot, no
   sample or electrolyte. Our run folders are self-documenting today
   (`{folder}_metadata.json` + the run log), so an H5 that is not would be a step backwards
   for anyone who moves the file on its own. Adding `attrs` would not break `convert_h5()`,
   which reads named datasets — but it should be agreed rather than assumed.

Also worth raising with him: whether he would *read* an acquisition-written H5 directly, or
would rather we keep producing ascii and leave his pipeline untouched. If the latter, this is
purely a disk-space feature for us and the layout is ours to choose.

### The format should be vendor-neutral (the user, 2026-09-11)

**None of this is Avantes- or Autolab-specific, and the layout should not pretend otherwise.**
Everything the file holds is generic: a wavelength axis, raw counts, a dark and a reference,
an absorbance matrix, a time axis, and per segment a `time / potential / current` trace. No part
of that depends on who made either instrument.

We have already done this refactor once, one level down. `data.EchemData(time, potential,
current)` exists because `write_echem_file()` used to reach into the array for toolkitpy's `vf` /
`im` / `time`, which meant a non-Gamry driver had to fabricate Gamry field names to be writable.
**The H5 is the same move at the file level**, and both potentiostat backends already produce
`EchemData`, so the echem half is neutral today.

Consequences for the design:

- **Vendor identity goes in attributes, never in structure.** Instrument names, serials, the
  build id and the settings snapshot are provenance; `write_run_metadata()` already collects
  exactly this (it takes an `instruments` dict). A reader learns what produced the file without
  the layout depending on it.
- **The writer belongs in `spec_echem/data.py`**, beside `write_spectra_file()` and
  `write_echem_file()`, driven by the same arguments. Nothing vendor-specific reaches it.
- **It makes the archival-superset version the natural choice.** A neutral, complete file is a
  data format; an analysis-shaped one is a convenience for one pipeline. And it survives the
  deferred hardware work — an Ocean Optics spectrometer or a third potentiostat changes nothing
  about the file (see the `hardware-portability` notes).

Practical framing for the conversation with Raj: write **our** neutral superset, and make it
trivially convertible to his layout, rather than adopting a shape derived from his objects.

### Sizes, measured (2026-09-11)

A realistic 14-segment run, 1265 wavelengths, ~5100 time points — **645 MB of ascii today**:

| layout | size | vs ascii |
|---|---|---|
| Raj's, as written (`df.values`, float64) | 51.6 MB | 12x |
| Raj's, float32 | 25.9 MB | 25x |
| **archival: absorbance f32 + counts u16 + dark + ref** | **38.8 MB** | **17x** |
| counts only, absorbance derived | 13.0 MB | 50x |

Raw counts are `uint16` **exactly** — the ADC is 16-bit — so they cost half what float32
absorbance does. The complete archival file is a ~50% surcharge over absorbance-only, and is
still *smaller* than Raj's current float64 file while holding strictly more.

Absorbance is derivable from counts/dark/reference, so dropping it would reach 13 MB. **Do not:**
a reader would have to reproduce `compute_absorbance()` exactly, including where NaN and inf fall
out when the reference approaches the dark (the 2026-09-04 shutter-closed run produced 8982 NaN +
513 inf, which are correct outputs worth preserving rather than regenerating), and it would make
the file useless to anyone without our arithmetic.

Not included above: HDF5 per-dataset gzip/lzf. Counts should compress 2-4x (smooth spectra,
limited range), absorbance less. The archival file plausibly lands nearer 15-25 MB in practice —
worth measuring on real data rather than estimating.

## Current range — a range-finding test run, NOT autoscaling (the user, 2026-09-11)

**Decision: do not autoscale.** `autolab_current_range` stays a single value for a whole
run, chosen deliberately. Two reasons, and the second is the stronger:

1. **Timing.** A mid-run switch lands in the middle of the fast decay that doping and
   dedoping steps exist to measure, and adds a discontinuity exactly there. Pre-scaling
   before each hold would spend the startup time `Ei` mode was built to remove
   (1134 ms -> 19-30 ms).
2. **The zero offset is per range.** `CR09_10mA` carries +1.6 µA (MEASURED, 2026-09-11);
   a finer range carries a different one, and a different quantum. If the range changed
   between rungs of a ladder, **each segment would sit on a different baseline** — so the
   doping trend, which is the measurement, would be corrupted by the instrument shifting
   underneath it. Even per-segment prediction from the previous rung fails on this.

### What to build instead — a range-finding test run

A short probe that determines a practical range before the real run:

- Drive only the **extremes** of the planned ladder: the highest doping potential and the
  dedoping potential (both from the same settings the run will use). Everything in
  between draws less.
- Start on a deliberately coarse range so the probe itself cannot clip, hold each for a
  few seconds, and take the peak. Order of magnitude is all that is needed — the
  difference between 600 µA and 6 mA, not a calibrated value.
- Report the peak and the suggested range (`suggest_current_range()`, 20% headroom),
  then leave the cell off.

That feeds the setting on the Parameters tab; the per-segment advisory added
2026-09-11 then confirms after the fact whether the choice held. Probe -> set -> run ->
advisory closes the loop without the instrument ever changing range mid-measurement.

### Still an experiment question, not a code one

Within one chrono segment the current spans ~3 decades (625 µA transient, 0.34-33.5 µA
settled, 2026-09-11). No single range serves both: choosing for the peak avoids clipping
and coarsens the settled value; choosing for the settled value clips the transient. The
probe should report **both** numbers so the choice is made with them visible.

## Analysis in the GUI — quick, during acquisition (the user, 2026-09-11)

**The value is doing it WHILE the run is going, not afterwards.** On 2026-09-11, film A
collapsed after the +0.8 V excursion in `film2` and nothing said so until the files were
analyzed later — `film3` was then spent on a film that was already dead. A modulation-per-step
number on screen would have shown it during `film2`, in time to stop.

That is the whole argument. Publication-quality and exploratory work stays in Jupyter.

### Where it goes — probably the existing Results tab, not a fifth

the own read, and it looks right: the Results tab already loads a run folder
(`discover_run_segments()` / `read_spectra_absorbance()`) and already has a segment selector and
canvases. Analysis is a second view of data it has in hand, not a new place to load things. A
fifth tab would duplicate the loading and split "look at the run" across two places.

Revisit only if the controls crowd the tab.

### What "core" means — Raj's `oect_processing/specechem/`

His `UVVis` class and `uvvis_plot` are the reference for which analyzes earn a place:

| method | what it gives |
|---|---|
| `time_dep_spectra()` | absorbance vs wavelength vs time per potential — the base object |
| `spec_echem_voltage()` | spectra at one time across the potential ladder |
| `abs_vs_voltage(wavelength, time)` | **the modulation curve** — A at one wavelength across the ladder |
| `single_wl_time(potential, wavelength)` | one wavelength vs time within a step — the kinetics |
| `current_vs_time()` | the echem trace |
| `banded_fits(wl_start, wl_stop, fittype='exp')` | exponential fits over a band — kinetics, less "quick" |
| `uvvis_plot.spectrogram()` | the 2-D map |

**For the during-a-run case, the short list is:** `abs_vs_voltage` (is the film still modulating?),
`single_wl_time` (did this step reach steady state?), and a spectrogram. Those three answer "is
this run worth continuing". `banded_fits` and the rest are Jupyter work.

### Notes

- **Not blocked on H5.** The Results tab reads the ascii today, so this can be built now and
  switched to H5 when that lands. Do not sequence it behind the file format.
- **The pieces mostly exist.** `read_spectra_absorbance()` returns a wavelength-indexed,
  time-columned DataFrame — the same shape Raj's methods operate on — and `gamry_data.read_cv()` /
  `read_chrono()` give the echem side. The work is selection UI and plotting, not analysis maths.
- **One shared definition of the ladder already exists** (`data.segment_potential`, added
  2026-09-11 so graph titles cannot drift from what was applied). An abs-vs-voltage plot should
  use it rather than re-deriving potentials.
- Worth asking Raj which of his methods he considers load-bearing versus historical, the same
  conversation as the H5 layout.

## Conversation with Raj — kept OUT of this repo

**This repository is public.** Notes about a named collaborator — which of his methods look
historical, what he has not fixed, what to ask him — do not belong in it. They live outside the
repo at `../private-notes/raj-conversation.md`.

The *technical* content stays here where it is useful: the H5 layout and sizing above, and the
`read_files.py` bugs, which are ordinary bug reports and are better sent as a pull request anyway.
