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

- [ ] **NEXT (Dean, 2026-07-07): sign off live echem timing.** On the incremental-redraw build
      (gui-dev ≥ `173bcbe`), run the live CV **2–3 times** in Python mode, then
      `python examples/plot_spectra_timing.py <run_live> <run_off>` and compare the per-spectrum
      interval plots. Pass = no 119-style spikes, live ≈ off (~target ± a few ms). The effect is
      stochastic (a single clean run isn't proof), so do a few reps. If clean, mark the live-echem
      item below fully done; if a spike persists, slow the 400 ms redraw or decimate the points drawn.
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
      timing/50 ms budget is safe). **NOT yet bench-verified (Dean, 2026-07-06): coded + looks right, but
      confirm on the box that it doesn't perturb the SPECTRA cadence** (the one real risk = GIL contention
      from the redraw on the GUI thread vs the worker's Python timing loop). Verification tooling shipped:
      (a) every segment logs its actual cadence — "X cadence: mean … (target …), min/max, jitter(sd), n"
      — from hardware timestamps, to the status pane + .log; (b) a "Live echem" checkbox on the Run tab
      to A/B the same run plot-on vs plot-off and compare the logged cadence. If jitter is bad, the 400 ms
      redraw interval is a one-line knob (make it tunable). Possible follow-ups: live *absorbance* too
      (needs per-spectrum emit from the worker); drop the now-purposeful `acq_data()` poll into the
      two-thread review.
- [ ] **Linearity check on the raw-counts test (future):** flag when the peak test counts approach the
      detector's saturation ceiling, so the user confirms they're in the linear regime before collecting
      dark/reference/data. (The test-counts graph already annotates the peak value.)

## GUI UX — Instrument tab potentiostat controls (Dean, 2026-07-05)

- [ ] **Auto-verify the Gamry when Python mode is selected.** Selecting "Python" (vs "External") is
      exactly when you'd want the connection confirmed — it should run the Identify probe automatically
      and show "Gamry connected — Gamry Duck (serial 08083)", instead of leaving it to a separate
      "Identify Potentiostat" click. At minimum, make connection verification part of switching to Python.
- [ ] **Reconsider the layout of the External/Python radios + Identify button** — there's likely a
      cleaner grouping/arrangement of those choices. Fold into the broader GUI rearrangement pass.

## GUI UX — data folder guidance / existing-folder warning (Dean, 2026-07-06)

- [x] **Warn when the target run folder already exists.** DONE (2026-07-06): Start now checks
      `{data_root}/{data_folder}`; if it exists and contains files, a confirm dialog (default Cancel)
      warns that continuing may overwrite a previous run. Prompted by Dean actually overwriting older
      data by forgetting to rename the folder — silent `mkdir(exist_ok=True)` clobbered same-named files.
- [ ] **Still to do: a short student-facing guide/tooltip** that Save location = the PARENT and Data
      folder name = the subfolder the app creates (the 1–2 sentence quick-start noted 2026-07-03), to
      also head off the *double-nesting* case (browsing into a run folder, then typing its name too).
