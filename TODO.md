# spec-echem — TODO

Running list of planned work and deferred cleanups. (Active design/status notes live in CLAUDE.md.)

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
- [ ] **Linearity check on the raw-counts test (future):** flag when the peak test counts approach the
      detector's saturation ceiling, so the user confirms they're in the linear regime before collecting
      dark/reference/data. (The test-counts graph already annotates the peak value.)

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
