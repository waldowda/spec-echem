# spec-echem — Project Status

A short, human-readable snapshot of where the project is and what's next, so the thread
isn't lost between sessions. Task-level detail lives in [`TODO.md`](TODO.md); design context
in [`CLAUDE.md`](CLAUDE.md); output formats in [`docs/data-format.md`](docs/data-format.md).

_Last updated: 2026-07-27_

---

## 🎉 RELEASED: v0.2.0 on `main`, tagged (2026-07-14) — START HERE

**v0.2.0 is merged to `main` (`--no-ff`) and tagged `v0.2.0`.** `gui-dev` stays the dev branch and is
now **well ahead of `main`** with the post-tag work below. **168 tests** (150 at the tag).

**The 0.2.0 theme is instrument setup.** 0.1.0 could run an experiment; 0.2.0 helps you set the
instrument up correctly first, and remembers how your rig is configured:

- **Linearity check** — hardware-validated. Key finding: the detector stays linear to ~1% right up to
  the hard clip, so a deviation-only criterion gives no headroom (lands at 94% of full scale). The
  recommendation takes the tighter of *5% below the linearity limit* or a **max-fill fraction**
  (default **85% fill / 2% tol**, both confirmed by Dean). `Find saturation` bisects.
- **Wavelength window** (opt-in) — crops the noisy lamp edges out of every file.
- **Test (sample)** — read the beam without overwriting dark/ref. Closes a real hole: the reference is
  taken with a blank FTO insert, and after swapping in the sample a plain "Collect New" would have
  silently destroyed the reference by recording the sample as 100%T.
- **Bench defaults** (`config/*.ini`) — per-rig settings, layered under per-experiment settings.
- **Pre-dedoping "run it, but discard the data"** — conditions the film, writes nothing (no spectra,
  no echem, no `.DTA`), stays out of Results. **Verified on hardware in Python mode 2026-07-14** —
  that was the last merge gate, and the `.DTA` suppression path had never executed before it.

### Landed after the tag (on `main`, unreleased — see CHANGELOG `[Unreleased]`)

- **Hardware is now named** in README/SOP: Avantes **AvaSpec-VRS2048CL-EVO** (2048 px, 300–1100 nm
  optical config, 50 µm slit) and a Gamry **Reference 600** — *not* a 600+; that error had been in
  `CLAUDE.md` since before the GUI existed. Everything else is "expected to function within its own
  respective limits". Gamry compatibility is grounded in the vendor's ToolkitPy help:
  `set_digital_out()` is on the **generic `Pstat` class**, not model-gated. New
  `examples/identify_hardware.py`.
- **Reading the spectrometer label explained the `[395:1660]` crop:** the SDK reports a wavelength for
  all 2048 pixels (~144–1308 nm) but the *optics* are only specified 300–1100 nm.
- **Build id** — `spec_echem.build_id()` → `0.2.0`, `0.2.0+7.g0f26a7a`, `.dirty` for an edited tree.
  Written to the **run metadata JSON**, the **run log's first line**, and the **GUI title bar**, so a
  data folder can name the code that produced it. `build_info.py` is now the single source of the
  version (setup.py reads it by regex). Confirmed on the Win11 box.

### Cross-model review + hardening (2026-07-15 → 07-27, `gui-dev`, not yet merged to `main`)

A **Fable** (different-model) review of `gui/` and the concurrency core returned 10 findings; all 10
held up against the code and all are fixed. The headline: after a spectrometer arm-failure, `finish()`
still released the Gamry thread, so **the instrument ran the full CV blind on the sample with zero
spectra recorded**. Also: a silent hang on Gamry setup failure, potentiostat errors logged where no
handler could see them, Stop disabling Abort, the pre-dedoping *Duration* field being collected but
ignored, an `int(x+1)` off-by-one, and mid-run settings mutation.

**All four bench items are now validated on the Win11 rig (2026-07-27) — the review is closed.**
Unplugging the Avantes before Start produced `AVS_Measure failed (code -3)` and the Gamry correctly
did **not** run the waveform; unplugging the Gamry made `prepare()` raise instead of hanging.

Hardening that came out of the same sessions:

- **App log** — logging now starts at **launch**, not at Start: `‹data root›\logs\spec-echem.log`,
  rotated nightly, nothing deleted. Everything before a run (connecting, dark/ref, a *failed*
  connect) used to go nowhere but the shell. Each launch banner records the build, the Python
  env/bitness, and driver availability. The per-run log is unchanged and still travels with the data.
- **Instrument provenance** — the run log and `_metadata.json` now name the spectrometer and
  potentiostat that produced the data, not just the code and settings.
- **The spectrometer no longer prints to the shell** — ten notebook-era `print()` calls became log
  records. Confirmed silent through a full run on the instrument box.
- **First `gui/` tests** (`tests/test_gui_layout.py`) — prompted by a real regression where a longer
  error message dragged the window past its half-column layout, because a `QLabel` in a Qt layout
  demands its full text width rather than clipping.

### Still open (none blocking)

- **The trigger cable's *build*** — connector, pinout, shielding — is undocumented; only its endpoints
  are. It exists in Dean's head and in the one cable on the bench. See `TODO.md`.
- **A mid-run Gamry USB pull surfaces late.** Pulling the cable during a run stops it, but the error
  banner appears to arrive only on the *next* Start rather than at the moment of failure. Observed
  twice, not yet pinned down. See `TODO.md` for the decisive test.
- **`gui/` is still barely tested** — 4 of 168 tests touch it. *Every* bug in the 0.2.0 cycle lived in
  GUI wiring and the core suite passed through all of them. The layout tests establish the pattern
  (headless via `QT_QPA_PLATFORM=offscreen`, `importorskip("qtpy")` so no-Qt envs still run).

---

## Landed since the release: spectrometer linearity check (2026-07-13, `gui-dev`)

The Instrument tab has a **`Linearity Check`** box beside Spectrometer Settings (which is now half
width). Run it with the reference solution in place: it ramps integration time, tracks one fixed peak
pixel, fits the linear region, and recommends a working integration time you can accept or override.
**Hardware-validated on the Win11 box the same day.** 126 tests.

**The scientific lesson (worth keeping):** the detector stays linear to within ~1% *right up until it
hard-clips*. So a "deviation from the fitted line" criterion never fires — the ramp just ends at the
clip, and "5% below the limit of linearity" lands at **94% of full scale**, with no headroom for lamp
drift. Tightening the tolerance cannot fix this (the real deviation there was 0.95%). The recommendation
therefore takes the **tighter of two constraints**: 5% below the linearity limit, *or* peak counts at or
below a **max-fill** fraction of full scale. Defaults **85% fill / 2% tolerance**, both confirmed good by
Dean on hardware. `Find saturation` bisects to the true threshold (plain doubling could only report a
power-of-two multiple — it said 0.176 ms when saturation was really ~0.111 ms).

Saturation is strongly source-dependent (halogen+ND saturates ~0.11 ms; the AvaLight will differ), so
Start/Stop/Steps stay manual and `Find saturation` re-adapts on a source swap. See `TODO.md` for the
per-source-defaults follow-up.

---

## 🎉 RELEASED: gui-dev → main (2026-07-10)

The GUI development line is **merged to `main` at v0.1.0** (`--no-ff` merge `3dd623a`, pushed).
`main` was the old notebook-era version; it now carries the full GUI: 4-tab PyQt5 app, modular
package, External + Python (EchemToolkitPy) Gamry with the DIGOUT0→Avantes trigger sync, Phase 2.5
echem capture, Results / Load-Run / live-echem, and the opt-in wavelength window. **`gui-dev` stays
the working branch.** Version stays 0.1.0 (pre-release); a citable Zenodo 0.2.0 can be tagged later.

**Merge gate met:** output validated end-to-end through Raj Giri's `OECT_processing`, including a
**cropped run** (2026-07-10, salt blank) — the narrower wavelength axis reads cleanly downstream.

Landed this cycle:
- **Configurable wavelength window** — opt-in *software* crop of the noisy lamp edges, with a
  data-driven, override-able recommendation (absolute "Max noise (OD)" knob). Default = full window →
  output byte-identical to before. `spec_echem/spectral_range.py` + `set_wavelength_window`.
  (The `m_StartPixel/m_StopPixel` hardware approach was abandoned — it mis-mapped on real hardware.)
- **Results "Load Run…"** + absorbance y-autoscale.
- **Doc hygiene + README refresh** — deps/DOI/README, `docs/data-format.md`; the README now presents
  the GUI as the workflow with the 32-bit (Python Gamry) vs 64-bit env note.

**Deferred (no rush):** fresh/degassed real-sample scientific demo (~Sept–Oct 2026, lab reno);
a citable Zenodo 0.2.0 tag; a fuller "Analysis with OECT_processing" README section once Raj fixes his
reader upstream (`specfiles`→`stepfiles` + `prededoping` mis-sort); the pre-dedoping-subfolder idea;
exposing other `measconfig` fields.

---

## (prior) Milestone — first real-sample Python-mode run, validated end to end (2026-07-09)

Dean ran a full **Python-mode** sequence (CV + pre-dedope + 3 doping/dedoping cycles) on a real —
if aged, non-degassed — **P3HT/P3MEEMT** film (`20260709_P3HT_01`). Signal was weaker than a fresh
sample would give, but the **software plumbing is now proven on real data**:

- **File set complete and 1:1 paired** — every `*spectra*.txt` has its clean echem `.txt`
  (`CV/steps/dedoping/prededoping`) and native `.dta` partner; metadata + log present; all colocated
  in the run folder (a Claude session on the Win11 box verified contents, no files modified).
- **Spectra timing is hardware-true** (`examples/plot_spectra_timing.py`): counts exact (CV 721,
  chrono 301 each), every segment's `Corrected time` starts at 0.000, baseline 100–102 ms.
  *Honest caveat:* a ~1.5% fixed per-interval overhead (mean ~101.5 ms) plus **isolated single-point
  spikes of 110–124 ms scattered through the run** (not just warm-up). Non-cumulative, OS/GIL
  scheduling hiccups; harmless because every spectrum carries its own Avantes timestamp. If ever
  wanted gone: raise process priority or target an absolute schedule (not needed for the science).
- **Echem contents verified** — `CV.txt` cols `[Potential, Current]`, sweep −0.498…+0.700 V, both
  polarities, sensible film CV; `steps(0)` held +0.301 V with a proper charging-transient decay;
  every `.dta` CURVE-TABLE count == `.dta` rows == echem `.txt` rows. Chrono echem = 300 pts vs
  spectra 301 — **expected** (independent clocks; instruments share only the trigger).
- **Correct spectroelectrochemistry observed (the real validation).** On the 0.7 V doping step Dean
  saw the neutral **π→π\* band bleach and polaron absorption grow**, cleanly reversing on dedoping —
  the textbook p-doping signature, correlated with the potential step. So the coupled system captured
  genuine SEC behavior, not just well-formed files. Weak/late doping (little happened below ~0.7 V,
  small doping current) is a *sample* story (aged, non-degassed → high/sluggish oxidation onset),
  not an instrument story.

**Nesting question resolved: benign.** Save location had been left on an old `…\specechem_data\20260703_test`
folder, so the run nested one level deeper than canonical — not a double-nest, not a code bug (every
writer joins the path once). Zero impact on analysis. Reinforces the pending parent-vs-subfolder tooltip TODO.

**Also landed 2026-07-09 (Results-tab review, on `gui-dev`, 106 tests):**
- **"Load Run…"** — open a saved run folder and view its spectra + echem without re-running (the tab
  used to only show the current session's run). Reconstructs the absorbance matrices + segment map from
  disk (`read_spectra_absorbance` / `discover_run_segments` in `spec_echem/data.py`); guarded mid-run.
- **Absorbance y-autoscale** — the y-axis now rescales to the selected wavelength range, so zooming into
  the weaker polaron region no longer leaves the traces squished under the π→π* peak.

**🎯 OECT_processing TRIAL PASSED (2026-07-10) — the software gate is met.** Ran Raj Giri's
`OECT_processing` doping pipeline on the real `20260709_P3HT_01` data (Mac Mini, dedicated `oect`
conda env). `read_files` → `UVVis.time_dep_spectra` → `current_vs_time` all run **clean end to end**:
correct potentials (0.3/0.5/0.7 V), full 301×1265 spectra-vs-time per step, and doping currents
24/53/**278 µA** — matching the raw CV and the observed "little doping until 0.7 V." **spec-echem's
output format is confirmed compatible; no output change needed for the merge.** The only fix was in
Raj's reader (a May-2026 `specfiles`→`stepfiles` regression — the potential lives in the step files;
applied in our local clone, for Raj to fix upstream). Second reader nit for Raj: `prededoping*` files
mis-sort into the dedoping lists (substring match). Raj (Slack) will make his reader dual-format
(his newer single-file design vs our two-file output), so nothing forces a spec-echem change.

**`gui-dev → main`: ✅ DONE (merged 2026-07-10** — see the release section at the top). Both gates
were met: OECT_processing compatibility proven (incl. a cropped run) + doc hygiene done. The
fresh/degassed scientific gold-standard demo remains deferred (~Sept–Oct 2026, lab reno) — a quality
milestone, not a blocker, and it did not hold up the release.

---

## Where we are

**Phase 2.5 — Python-mode echem data capture — is DONE, hardware-validated, and merged to
`gui-dev`.**

When Python drives the Gamry (`ToolkitPotentiostat`), the potentiostat's current/potential is
now saved alongside the UV-Vis spectra:

- clean analysis `.txt` (`CV.txt`, `steps(N).txt`, `dedoping(N).txt`, `prededoping(N).txt`) in
  Raj's `OECT_processing` format, and
- native Gamry `.dta` in a `dta/` subfolder (on by default; `save_dta` toggle).

Validated headless (`examples/validate_echem_capture.py`, 6/6) and on a real GUI run.
98 tests pass. Also landed this cycle: consistent Load/Save folders (settings/darks/refs all
under the data root) with per-day serial filenames.

Everything before this is also done: the modular package, the 4-tab PyQt5 GUI, hardware
trigger sync (DIGOUT0 → Avantes), and all-Python Gamry control for every segment type
(CV + doping/dedoping/pre-dedoping), all validated against the golden 8-column output.

## The one lesson worth remembering

The Phase 2.5 "empty echem files" bug was a **Python reference-counting / garbage-collection
issue**, not a threading or timing problem. `_build_signal()` returned only the `curve` and
dropped the `signal` local; because `set_signal` stores the signal in C where Python can't see
it, CPython freed it immediately, and the curve then ran a freed (empty) waveform — started,
ran zero samples, died in ~50 ms. **Fix:** return `(curve, signal)` and keep *both* alive
through `run()` and the poll loop. The two-thread / acq_data-polling / build-runway theories
we chased first were all red herrings. (This is also why the current per-segment-thread
machinery is now suspect — see follow-up #1.)

## What's next (priority order)

1. **Two-thread simplification check.** Now that the bug was a lifetime issue, is the
   dedicated-per-segment-thread + fresh-session-per-segment + acq_data-in-loop machinery still
   needed, or would a simpler same-thread design work? Best done on the instrument box.
2. **First real-sample test** — a real polymer sample with proper dark (lamp blocked) and
   reference (blank, lamp on), full multi-cycle sequence in one run.
3. ~~Echem plots in the GUI (I-vs-E, I-vs-t).~~ **DONE (2026-07-06)** — absorbance and
   electrochemistry side by side per segment (Results), **plus a live echem trace on the Run tab**
   that builds mid-segment so you can watch a CV and abort before a long doping sweep. Bench-untested
   on real hardware — verify on the box. Possible follow-up: live *absorbance* too.
4. **GUI rearrangement pass** — further tab-layout cleanup (a first pass landed 2026-07-06:
   Instrument tab split into four side-by-side graphs, Run controls moved to top). Still to do:
   auto-verify the Gamry when Python mode is selected, and regroup the External/Python/Identify
   controls (see the "GUI UX" section in `TODO.md`).
5. **Linearity check** on the peak test-counts (saturation warning).
6. **Future/optional:** a compact tidy-DataFrame sidecar to cut Raj-format duplication —
   an *additional* output; the `.txt` files stay the compatibility contract.

## The true acceptance test (not yet done)

Everything so far proves the plumbing. The real end-to-end proof is a real polymer sample run
whose output analyzes cleanly in Raj's `OECT_processing`. That remains the eventual gold
standard, not yet scheduled.

## Machines

- **Mac Mini** (`~/dev/SpectroElectroChem/spec-echem`) — development; no hardware, imports
  guarded so the package + GUI run against fakes.
- **Win11 `SpecEchem32`** — instrument box (Avantes + Gamry). Claude Code is installed here now,
  which greatly speeds up hardware-loop debugging.
