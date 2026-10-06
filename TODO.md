# spec-echem — TODO

Running list of planned work and deferred cleanups. (Active design/status notes live in CLAUDE.md.)

## Now — 2026-10-03

**One list of what is open.** Replaces three dated "Next up" lists (2026-09-24, -25,
-28) that had started to disagree — several of their items were already done
(HDF5, figure export). Items whose detail lives in a section further down just point
there; the ones that only ever lived in those lists carry their full text here.
Closed sections are in **Archive** at the bottom, verbatim.

**The caveat over all of it:** as of 2026-10-03 nothing has been used on a real
experiment. Every default and threshold below was judged on test runs and synthetic
data.

### Needs the rig

- [ ] **First real-sample run — the gold standard.** Real film, real dark (lamp
      blocked) and reference (blank, lamp on), the full multi-cycle sequence in one
      Start, then confirm it analyses cleanly downstream (`OECT_processing`). Gates
      almost everything under *Needs a decision*.
- [ ] **Confirm the current-range ladder on a Reference 610+ and an Interface 1010.**
      Was planned for 2026-09-30; not recorded as done. `examples/probe_gamry_ladder.py`
      — read-only, cell-safe, needs no spectrometer and no dummy cell. The 610+ should
      match the documented Reference 600 column; the 1010 should report 10 nA..1 A in
      1/10/100 decades, so ITS `IERange 8` is 100 uA rather than 600 uA. That is the
      whole reason the ladder is read from the instrument, and it has only been
      verified on one model.
- [ ] **Write down how toolkitpy gets into the conda env.** Recorded NOWHERE — not
      here, not in CLAUDE.md, not in docs/ — and installing Gamry Framework may not be
      sufficient on its own. Capture it on a machine where it works
      (`python -c "import toolkitpy; print(toolkitpy.__file__)"`, `pip show toolkitpy`)
      beside the documented `avaspec.py` setup. Same category as the undocumented
      trigger-cable build (below). Moot if the pip-installable 64-bit toolkit lands.
- [ ] **Confirm the stray-Stop fix on hardware.** A run ended after its CV with "Stop
      requested" logged and Stop untouched; diagnosed by reasoning, not reproduced —
      Start is disabled at run start, Qt hands focus to Stop beside it, and a
      Space/Return left over from the confirmation dialog lands on it. Stop and Abort
      are ClickFocus now. **If a run stops on its own again, it is something else.**
- [ ] **Decode the acq_data overload field.** Name and encoding undocumented; it fired
      on 721 of 721 points at 1.24% of full scale, so the warning now requires the
      current to corroborate it. The raw distinct values are logged at DEBUG on any run
      where flags appear — read them from a real run, then decide whether an
      uncorroborated warning is worth restoring.
- [ ] **Figure export step 5: open an exported CSV beside its plot**, on real data.
      Only ever done against synthetic. See *Figure output*.
- [ ] **Document the trigger-cable build** — see its section. Needs the bench notes.

### Needs a decision

- [ ] **Move the current-range control to Tab 1, beside the potentiostat selection.**
      Requested 2026-09-25. Two range dropdowns in the doping group — "Current range
      (Ei mode):" (Autolab) and "Current range (Gamry):" — and on 2026-09-25 it was not
      clear which had been set, costing two runs. Show ONE control next to the radio
      buttons that pick the instrument, switching with the mode; none in External mode.
      Listed under decisions only because it moves a control people have learned.
- [ ] **A current range per segment TYPE?** `20260925_test10` wanted 600 uA for its CV
      (71 uA peak) and 6 uA for its chrono hold (0.8 uA peak); one setting covers both,
      so a run with both is a compromise. The per-segment advisory already names the
      better range for each. Not a defect — a decision.
- [ ] **Past Gamry data.** Settled currents from Python-mode runs before 2026-09-25 are
      not quantitatively trustworthy (1-2 uA of noise on 1-26 uA signals). Does
      anything need re-taking?
- [ ] **When to stop writing the ascii.** HDF5 is written, read, and selectable; the
      default stays `h5+ascii`. Retiring the text is a decision for after real data,
      and the downstream reader takes the text files — tell its maintainer first.
- [ ] **Where the HDF5 design narrative lives.** `private-notes/hdf5-design.md` holds
      the reasoning (cycle keys not potentials, no `charge`, `time_spectrometer`, the
      rejected alternatives) but carries ~21 personal and institution names. Probably
      leave it private now that `docs/data-format.md` §4 carries the spec — that file
      is a specification, not a design diary.
- [ ] **biexp fast tau vs the sampling interval — the user said yes, then the data
      disagreed with the premise.** Adopted 2026-10-03 on "not reproduced" (0 of 160
      synthetic fits). On REAL data it flags **204 of 8119 converged biexp fits over
      700-900 nm (2.5%)**, nearly all in the 20260710 run's first two doping/dedoping
      steps: 51 points over 5 s at 102 ms, fast tau median 41 ms, slow tau ~2.9 s,
      |B_fast|/|B_slow| ~1.0 -- a step finished between t=0 and the first spectrum.
      The fast tau IS unmeasurable, but the check flags the WHOLE fit, and ladders
      average passed fits only, so ~20% of those segments' wavelengths would leave the
      tau statistics though their slow tau may be fine. **Built and tested on the local
      branch `fast-tau-check` (`9daaee6`), NOT merged.** Options: merge as is; flag
      only the fast component (needs a per-component flag); or drop it.
- [x] ~~**Auto probe: guard interior gaps in the significance mask**~~ — DONE
      2026-10-03 (`bae0d55`). A peak needs kept pixels for a full smoothing window on
      both sides. Across 64 real segments, every film run with a real band is
      unchanged, including the bench-verified +0.8 V doping 7 (815.2 nm); 3 picks
      moved, all on segments with no band to find.
- [ ] **Follow-up: the probe's fallback when no peak survives.** It is the global
      argmax, which can still walk into the NIR -- a 'test' run went 808 -> 1042 nm
      once the stricter rule left it no peak. Saying "no band found" may be more
      honest than a number. Only reachable on data with no band; not urgent.
- [ ] **Single-column figure preset**, and moving the figure-export design doc to
      `docs/` — both after figures are being made from real data. See *Figure output*.

- [ ] **PITT (equilibrium staircase) segment — DESIGNED, ALL decisions made 2026-10-04,
      NOT built** (the user: "don't build yet"). Must be ONE physical waveform with the
      cell held throughout: both drivers switch the cell OFF between segments, so
      stacked chrono holds would sit at open circuit between steps and count the leaked
      charge in each step's dQ -- the quantity PITT measures.
      **The user's decisions:**
      1. A step ends when |I| falls below a cutoff fraction of that step's peak, OR at a
         max hold; both are SETTINGS (cutoff default 1%); record which rule ended it.
      2. **HDF5 only** -- the spectral volume is huge. This also avoids the downstream
         trap: `OECT_processing` sorts by `'spectra(' in name`, so a text file named
         like `pittspectra(0).txt` would be read as DOPING. Say "HDF5 only" in the UI,
         since everything else follows the global data_format setting.
      3. **Stored per step, not as one block**: the cell stays on, but the data is split
         at each setpoint change -- one h5 group per step with potential_set /
         potential_measured, as the doping cycles have. Plus a step table (start/end,
         end rule, dQ) and the shared spectrometer time axis so steps can be stitched.
         The Results/Analysis tabs can then list steps as segments.
      4. Its own Parameters section with a checkbox, beside CV, pre-dedoping and
         dope/dedope. **Start AND stop potentials are user-set** (2026-10-04), so a
         pre-dedoping before the PITT is the user's optional conditioning step. Still
         to confirm: order when dope/dedope is also ticked (proposed: PITT last).
      5. Ceiling user-set, default +0.7 V (not everything is aqueous; vs Ag/AgCl +0.8 V
         can be fine); never exceeded, rounding down like n_doping_cycles.
      **Drivers: AUTOLAB FIRST** (the user, 2026-10-04: the UDC4 can travel to UW, and
      UW visits are frequent). The Gamry waits for the 64-bit pip toolkitpy; the user
      suspects toolkitpy has a staircase signal, which would make it one curve with
      fixed holds there. Build
      the segment, settings, storage and analysis instrument-independent. Before either
      driver, a small probe on a dummy answers its one unknown: Gamry -- can per-step
      curves run back to back in one toolkitpy session with the cell left on, and how
      long is the gap? Autolab -- can the Ei setpoint change mid-measurement with the
      cell on? The 64-bit pip toolkitpy is imminent and may touch the Gamry layer.
      **All decisions answered 2026-10-04 -- the design is complete:**
      - Order when dope/dedope is also ticked: **dope/dedope first, then PITT.**
      - Direction: **"also step back down" is a checkbox, with a time warning** showing
        the estimated duration it adds.
      - End: **an optional dedope at the end of the PITT** -- part of the same continuous
        waveform (cell still on), saved as its own h5 group, then cell off. **Its OWN
        potential, user-set, default -0.5 V** (the user, 2026-10-04) -- not the doping
        ladder's dedoping_potential -- and its own hold time (default 30 s).
      - "Current level" = **the existing max-current (range) setting** already used for
        dope/dedope on the Autolab and Gamry; the PITT shares it by default. Whether it
        gets its OWN range is the existing "range per segment TYPE" decision. Keep the
        WARNING (not a block) when the cutoff current falls below the selected range's
        noise floor (~3.5 nA on the fine ranges).
      - Taken as proposed: step size a setting [10 mV]; a MIN hold [5 s] beside the max
        hold [120 s]; cadence [full rate for N = 5 s, then one spectrum per M = 1 s]; a
        "repeat on the blank" convenience later; band choice and whether dQ includes the
        fast transient belong with the analysis.
      **✅ AUTOLAB PROBE PASSED ON HARDWARE 2026-10-05** (PGSTAT302N, a Randles dummy
      100 Ohm + (1 MOhm || 1 uF), `examples/probe_autolab_pitt.py`): Ei.Setpoint steps
      cleanly with the cell held ON -- the instrument read the cell ON after all six
      changes, E within 0.19 mV of setpoint, each reached by the first sample (~65 ms),
      no straddled reads. On CR13_1uA the current followed V/R to ~1%: 50.4 / 100.1 /
      149.7 nA, fitted R = 1.010 MOhm. On CR09_10mA the same cell was invisible (a
      ~-0.8 uA range offset). The Autolab PITT driver (`7770e19`) is what the probe
      ran. **And the GUI PITT ran on hardware the same day**: 4 steps +0.10 -> +0.20 V in
      50 mV plus a -0.5 V end dedope, one segment, cell held, one .h5, `done`. On
      CR13_1uA the peaks were 102 / 153 / 202 nA (V / 1 MOhm to 2%), every step ending
      at its max hold -- correct for that cell. On CR10_1mA (test 1) the range read
      ~90 nA low, and step 0 ENDED AT 'CUTOFF' at 4.4 s from ONE noisy sample grazing
      zero -- fixed: the cutoff now needs SETTLE_SAMPLES = 5 consecutive samples
      (`pitt.py`). **Test 3 showed that was not enough**: CR10_1mA reads in whole counts
      of 3.0518 nA (1 mA / 327,680 -- every peak logged on that range was an integer
      multiple), so at +0.1 V the reading was mostly EXACTLY zero, the 'peak' one count,
      and five zeros met a 1% cutoff no reading can express. Now a cutoff below one
      count of the range is never judged met (`AUTOLAB_COUNT_FRACTION`, StepEnd
      `resolution_a`, per-step `cutoff_unresolved`); the step runs to its max hold
      and the log says the range is too coarse. **NOT yet confirmed on hardware**: the
      user ran it after this and saw it 'working pretty well' but the log was not
      captured, and whether that run had `ff38197` is unknown.
      **WEDNESDAY 2026-10-07 at the UW rig, in order:**
      1. `git pull`; check the build in the GUI title includes `ff38197` or later.
      2. Re-run test 3 exactly (CR10_1mA, +0.1 -> +0.2 V in 50 mV, min 2 s, max 5 s,
         -0.5 V end dedope). Expect all three forward steps at `max_hold`, each with
         "settling could NOT be judged ... finer than one count"; none at cutoff.
         Paste the status-pane log back.
      3. The cutoff rule on a real decay -- never yet seen on hardware: 1 MOhm in
         SERIES with 1 uF, nothing in parallel (tau = 1 s), CR13_1uA, 0.15 V steps,
         10% cutoff. Expect each step at `cutoff` after ~2.3 s + ~0.4 s.
      4. Only then a film -- the user's call -- with the current range raised.
      NEXT after that: re-run test 1's
      settings (CR10_1mA) to confirm no early cutoff; then the cutoff rule itself on a
      SLOW series RC (1 MOhm in series with 1 uF, tau = 1 s, 0.15 V steps, 10% cutoff).
      **GAMRY PITT REWRITTEN 2026-10-05 as ONE CURVE PER STEP** (after the probes on
      the Reference 600: direct reads take ~176 ms each; a curve samples at exactly
      0.100 s; StopAt ends a whole array2 curve and is ignored on m_step; between
      curves the cell stays ON at the previous potential, ~80 ms to the next run()).
      A dedicated thread (`_GamryPittRunner`) owns the session, cell on once, one
      single-potential curve per step, restarted in place if it runs out; OUR step-end
      rule (% of each step's peak, min hold, 5 consecutive, one count) decides from
      the curve's live points; every hardware point is kept and SAVED (not the loop's
      samples). Tested on a curve-on-a-clock fake; 4 mutations caught. NEXT, on the
      rig: `examples/probe_gamry_pitt.py --energize` (the real loop + real driver,
      stand-in spectrometer), then a short GUI PITT in Python mode.
      (Superseded:) **GAMRY PITT DRIVER WRITTEN 2026-10-05, not yet run on hardware.** toolkitpy has
      direct control -- `set_voltage` (applied only with the cell on), `measure_v`,
      `measure_i`, `cell()` -- so the Gamry PITT mirrors the Autolab's Ei driver: NO
      curve, so no dedicated thread; the session lives on the thread running the
      staircase. `examples/probe_gamry_pitt.py` (SpecEchem32, UDC4 Randles) checks the
      steps with the cell read back ON, the DC resistance, how long a measure_v +
      measure_i pair takes against the 0.1 s tick, and one current COUNT of the range
      from within-step jitter -- the number current_resolution_a() needs for the Gamry
      (None until measured, so the sub-count rule is off on the Gamry for now). The
      user has the Gamry rig through 2026-10-06. toolkitpy also offers
      signal_m_step_new (a fixed-hold staircase) -- not used: no current cutoff.
      **Parameters section + saved-run display built 2026-10-04** (`24b29ac`,
      `4327b08`): the PITT section with a live estimate and return-leg warning; Load
      Run shows PITT steps even from a text-first load; the Results tab draws echem
      from HDF5 (which also gave 'HDF5 only' runs an echem plot for the first time);
      `docs/data-format.md` section 5. **What remains is the Autolab staircase driver**
      (after the UW probe), then the PITT analysis when the user is ready.
      **Built 2026-10-04** (`4a1807f`, `c30f089`, `dd4d681`): plan, step-end rule,
      cadence, the continuous loop, per-step HDF5, run wiring, Start refusals. Answered
      after: Abort KEEPS the data (fine); step results appear after the staircase ends
      (fine); a no-current step ends at its min hold (fine). **PITT-specific analysis
      (dQ per step, g(E), electrical vs optical) is DEFERRED** -- the user: present the
      results, wait on the analysis.
      **FUTURE -- check on the UW rig, deferred by the user:** does the PGSTAT302N have
      hardware charge integration (an integrator module)? Integrating SAMPLED current
      misses the head of every step's transient, 1 - exp(-t_first/tau) of its charge;
      sampling first after each setpoint change holds t_first to ~one Ei sample
      (~50 ms, ~5% at tau = 1 s), and each step records `first_sample_s` so the
      analysis can extrapolate. A hardware integral would remove the loss entirely.
      **Later:** a popup to choose the ORDER of the enabled sections, rather than the
      fixed CV -> pre-dedoping -> dope/dedope -> PITT.
      **When building:** the instrument-independent parts (settings, segment, h5 storage
      per step, GUI section, the fakes) need no rig; the Autolab driver follows the probe
      on a UW visit, with the UDC4.
      **Bench dummies:** the UDC4 Randles side is 200 Ohm + (3.01 kOhm || 1 uF), so its
      transient is ~0.19 ms -- invisible at 50 ms sampling; every step looks like a jump
      to a DC floor of dV/3.21 kOhm. It tests the MAX-HOLD rule and gives an EXACT dQ
      (I x t) to validate the integration. The CUTOFF rule needs a slow RC, e.g. 1 kOhm +
      1000 uF NON-polarised (the staircase crosses 0 V), tau = 1 s.

### Needs the user's Igor history

- [ ] Style one graph in Igor and hand over the command history — settles the
      `resid.` label, the legend (decided: match matplotlib's parameter block), and
      how the 721-trace spectra graph should be shown. See *Igor .itx export*.

### With UW

- [ ] DOS v2 (capacitive baseline), absolute energy axis, scan-rate series — see
      *In-GUI analysis*.

### Code, no rig needed

- [x] **Code review of `v0.3.1..gui-dev` — DONE 2026-10-03** (`/code-review high`,
      scoped to `gui/`, `igor_export`, `analysis`). 10 findings, each verified before
      acting:
      - **7 real, fixed, each with a test that fails on the old code:** a cancelled
        Fit all reported as whole; a new run's dropdown kept the old run's potentials;
        band figure/CSV names and titles from the live controls (plus a single
        segment saved as `band_all_segments`); export renders appending status notes;
        the Gamry ladder probe running with Connect live; the spectra export ignoring
        the wavelength window; Igor prefixes colliding on long names. `7cc30d5`,
        `b025698`, `2032dec`, `862b034`.
      - **1 not a bug:** the delta-time hint is kept current by a side effect of the
        cadence advisory. Code left alone; a test now guards the side effect.
      - **2 left for the user** — see *Needs a decision*.
      - **Declined:** the ladder rebuilding `BandFit.table()` per render. A cost on a
        preview toggle, not a correctness problem.
      - **Not done, needs the rig:** a Gamry connect is still two open/close sessions
        (identity, then ladder). Merging them halves the cycles on a USB stack that
        has failed under repeated open/close, but changes toolkitpy session handling.
      `/code-review ultra` (cloud, user-triggered, billed) remains the deeper option.
- [ ] **Then cut v0.4.0** — after the review and a first real-data run. 106 commits
      since v0.3.1; the v0.3.0 lesson is review BEFORE the merge.
- [ ] macOS KVO console warnings — watching, see its section.
- [ ] GUI test targets still uncovered — see *Automated tests for the GUI layer*.

### Backlog — wanted, not scheduled

- [ ] Richer models (joint polaron/pi-pi* fit with shared tau FIRST), same-sign
      prefactor option — see their sections.
- [ ] Auto-verify the Gamry on selecting Python mode; student-facing folder guide;
      AvaLight lamp control; `.nox` import — see their sections.
- [ ] **Expose the other hard-coded `measconfig` fields (future, the user 2026-07-10).** Now that the window
      is config-driven, `_create_measurement_config` could expose smoothing, **saturation detection**
      (ties to the linearity-check item below), and the averaging model instead of hard-coding them.
- [ ] **Linearity: per-source ramp defaults (the user, 2026-07-13).** Saturation depends strongly on the
      light source — the user has a halogen+ND (saturates ~0.11 ms) and an Avantes **AvaLight**. Start/Stop are
      manual and "Find saturation" auto-adapts, so switching sources already works; only the *default*
      Stop (0.15 ms) is tuned to the halogen. If source-swapping becomes routine, remember the last-used
      Start/Stop per source in settings rather than shipping one default.

## Figure output — code COMPLETE, used on both platforms (2026-10-02)

**NOTE on everything below: as of 2026-10-02 the system has only ever been used for
TESTING. No real data has been taken or analysed with it.** Judgements recorded here
about defaults, thresholds and formats are from test runs and synthetic data, and
should be revisited once real measurements are going through.

**Design: `private-notes/figure-export-design.md`.** It moves to
`docs/figure-export.md` in the commit that finishes the work — which has NOT
happened: steps 5-6 and rig verification are outstanding, so it stays in
private-notes for now.

**Done (`gui-dev` `8eea502`, 693 tests). All six steps are written; what is left is
verification that only the two machines can give.**

- [x] **1-2. Fixed-size rendering.** `MplCanvas._retarget()` + `render_to_figure()`.
      The seam is the FIGURE the canvas points at, not an `ax=` argument: every draw
      method already rebuilds its own figure, and `_layout_footnote` needs the
      FIGURE's width, which an axes parameter would not give it. Verified from a
      widget set to 13.0x3.2 in: 6.5x4.5 @ 300 dpi gives exactly 1950x1350 px.
- [x] **3. The preview dialog** (`gui/widgets/figure_dialog.py`). One reusable modal:
      4 size presets, dpi, optional provenance stamp, CSV. `PlotToolbar` keeps
      pan/zoom/home and loses Save and Subplots — used in the dialog AND on tab 6, so
      no plot anywhere offers two saves.
- [x] **4. Wired to all five canvases** — tab 4 absorbance + echem, tab 5 fit +
      ladder, tab 6 band. The canvas records its own last draw (`@_records`,
      `last_draw()`, `last_data()`), so no tab keeps bookkeeping that could fall out
      of step with the figure, and the CSV is derived from the same recorded call.
      **Tab 4's "Save Plots" is deleted** — per-figure only, no save-all there.

- [x] **Used on the bench all day 2026-10-01**, which found and fixed: the ladder
      saving the single-segment plot (custom compositions were invisible to the
      exporter — any new one MUST call `MplCanvas.record_draw`), figures not naming
      the wavelength or band they were taken at, the fit legend landing on the
      transient, plots collapsing to ~150 px, and the actions row scrolling off the
      bottom. All fixed; see git log for 2026-10-01.
- [x] **6. Tab 5's save-all** — three traces of the current segment plus the ladder,
      each with its CSV, provenance stamped. Bounded and per-segment, which is why it
      is safe to define where tab 4's is not. Found three bugs: `show_message` left
      the PREVIOUS plot saveable under the new name; provenance landed on top of the
      x-axis label on fit figures (constrained gridspec, which `tight_layout` cannot
      reserve a band in); and reserving it through the layout engine let the residual
      panel climb over the suptitle. All three fixed and asserted.

**Left — needs the instruments, not the editor:**

- [ ] **5. Open an exported CSV and check it against the plot it came from.**
      ("round-trip" was jargon: it just means read the file back and confirm the
      numbers are the ones on screen — same wavelengths, same taus, same rows.)
      Only ever done against synthetic data. NOTE: band CSVs written before
      `17fff77` may have tau1/tau2 mixed at some wavelengths — see below.
- [x] ~~**Win11 smoke test**~~ — run on the rig 2026-10-01, no problems noticed
      relative to macOS.
- [x] ~~**Cross-platform figures**~~ — used on both macOS and Win11 2026-10-01/02:
      "the save figures all look good or at least the same between platforms". Good
      enough for now. `examples/compare_figures.py` is there if a byte-level check is
      ever wanted; it has not been run, and does not need to be unless something
      looks off.
- [ ] **Single-column preset — revisit after real use.** Its text is proportionally
      larger than the double-column preset's and that is a floor, not a bug (matching
      the proportions needs 5 pt, below journal minimums) — but 3.25 x 2.25 in at
      7 pt has only been judged on screen, never in a manuscript. Review once figures
      are being made from real data.
- [ ] **Then move `private-notes/figure-export-design.md` to `docs/figure-export.md`**
      (a move, not a copy) and drop this section to a one-line pointer.

**Rig checklist — Win11, 2026-10-01:**

1. **Check the launch banner first.** `{data_root}/logs/spec-echem.log` should say
   `drivers avaspec: yes | toolkitpy: yes | h5py: yes`. **If h5py says `no` the run
   writes ascii and NO .h5, silently** — that is exactly what happened on 2026-09-29.
   `SpecEchem32` needs `h5py==2.10.0`; win32 cp37 wheels stop there.
2. **Both file sets.** A run writes the ascii AND the per-type `.h5` with no setting
   to change — gzip is off by default (it bought only 21%).
3. **Save figure… under any plot.** Preview opens at 6.5 x 4.5; check the fonts are
   not clipped at the rig's canvas size, which is where three legend fixes died in
   September.
4. **The comparison that matters:** save the SAME segment's absorbance figure here
   and on the Mac at the same preset, then `python examples/compare_figures.py
   mac.png win11.png`. They should be pixel identical. That is the whole point of
   the work, and nothing in the test suite can show it.
5. **Save all figures… on tab 5**, with a segment fitted. Expect the three traces
   plus the ladder in `{run_folder}/figures`, each with a `.csv` of the same stem,
   and a confirmation naming the folder.
6. **Open one of those CSVs** — `pd.read_csv(path, comment="#")` — and check it
   matches the plot. This is step 5, and it has only been done against synthetic data.
7. While there: `examples/probe_gamry_ladder.py` on the Reference 610+ / Interface
   1010 if either is to hand (read-only, cell-safe, no spectrometer needed).

**Typography, settled 2026-09-30 — do not "fix" these:**

- matplotlib defaults `axes.titlesize` to `'large'` = **1.2x** the base, which made
  the TITLE the heaviest thing on a small figure. Presets now carry a full font set
  (`preset_rc()`) with the title AT label size — it only names the segment, and
  journals usually drop figure titles entirely.
- `_draw_footnote` hardcoded 7 pt, so provenance became the LARGEST text on a
  single-column figure. It now scales from the ambient font; the factors reproduce
  the on-screen 7/8 exactly at matplotlib's default 10 pt, so the GUI is unchanged.
- **A footnote on a `plot_fit` figure needs BOTH ends of the rect reserved.**
  `tight_layout` cannot lay out its constrained gridspec, and the layout engine does
  not count a suptitle as part of the rect it is given. Reserve the footnote band
  and the suptitle band, or one of them gets overprinted.
- **Single column (3.25x2.25 @ 7 pt) is exactly half of double (6.5x4.5 @ 10 pt) and
  still looks label-heavy. That is a FLOOR, not something untuned:** matching the
  double's proportions needs 5 pt, below what journals accept. Both were spotted by
  eye on the rendered output while every test passed.

Original request: deal with graph output in a more thoughtful manner, Results AND
Analysis (tab 5), rather than patching the current button.

**Why "Save Plots" was deleted rather than renamed** (kept as the record of what
was wrong with it — the button is gone as of `3ced0fe`):

- It saves the two on-screen canvases for the CURRENTLY SELECTED segment as two
  files, `<base>_absorbance` and `<base>_echem`.
- **`_absorbance` is often a lie.** The top canvas hosts four views — Spectra,
  Kinetics (one wavelength), Modulation (across the ladder), Density of states — and
  the filename says `_absorbance` for all of them. Save a DOS plot and you get
  `Doping 0_absorbance.png` containing a density-of-states curve. Same class of error
  as a `time_spectrometer` dataset holding a copy of `time`: a name asserting what the
  contents contradict.
- **You type one filename and get two different ones.** Enter `Doping 0.png`, receive
  `Doping 0_absorbance.png` and `Doping 0_echem.png`.
- **Silent one-file case** when a segment has no echem — indistinguishable from a
  failed save.
- **The confirmation names no folder**, only basenames. (The OECT export had the same
  flaw and it cost a hunt on 2026-09-29.)
- 150 dpi is a screenshot, not a figure. PDF is offered but not signposted.

**Tab 5 (Analysis) has no export at all**, so fits and their plots cannot leave the
GUI except by screenshot.

**Superseded the old `NavigationToolbar2QT` roadmap item** (see below). The toolbar
alone turned out to be the wrong answer: its save writes at whatever size the widget
happens to be, which is the bug this work removes. Tab 6 keeps the toolbar for
pan/zoom/home, with its save button taken out.

Worth deciding at the same time: **what is the Modulation (across the ladder) view
for?** Raised 2026-09-29: it does not depend on which segment is selected, so sitting
behind a per-segment selector is misleading. Either it belongs elsewhere in the UI, or
the selector should visibly not apply while it is showing.

## Analysis correctness — fixed 2026-10-01, worth knowing about old exports

- **biexp tau1/tau2 were UNORDERED** until `17fff77`. The model is symmetric under
  exchanging the two components, so curve_fit returned either labelling — about 3%
  of fits. **Band ladders and CSVs exported before that commit may have tau1 and
  tau2 mixed at some wavelengths.** The scalar `tau` and `mean tau` were always
  safe. Re-run any band fit whose tau1/tau2 split matters.
- **The automatic probe could pick the detector edge** (`2460817`): no red-edge cap,
  and an argmax walks up a rising NIR tail. Capped at the optics' 1100 nm AND it now
  requires a peak, since the cap alone only relocates the problem. Verified on the
  bench: 1100.9 -> 815.2 nm.
- **Two measurability guards added** (`738d67c`): tau below the sampling interval,
  and `<tau>` beyond 10x the window (the old check was on raw tau, but a stretched
  fit plots the mean). Both FLAG and keep the numbers.
- **Where the line falls:** the software checks whether a number is MEASURABLE from
  its data. It does NOT judge whether the model was appropriate — "let the user
  worry about applying appropriate models". What it owes instead is making the
  choice visible, which is why every fit plot now states its model and window.

## Igor .itx export — WORKS, still rough in places (2026-10-02)

Built and loaded in Igor the same day; `spec_echem/igor_export.py`, offered beside
Save CSV in the figure preview. Waves + a Display, styling otherwise left to Igor.

**Settled by iterating against real loads:** wave names are GLOBAL so every wave is
prefixed with the figure name (two segments would otherwise overwrite each other);
names sanitised to Igor 6 rules AND uniqued; NaN written as NaN so a failed fit draws
as a gap; quotes escaped; a COLOUR per trace (without one Igor draws them all alike
and the fit vanished into the data); the title on the WINDOW, not a TextBox inside
the axes; the residual on its own panel above, `freePos={0,kwFraction}` because a
bare 0 means x=0 in DATA units; mirrored axes and matplotlib-matched symbol sizes.
The spectra view exports EVERY spectrum as one 2-D wave drawn as a fan of traces (not
an image -- an image is correct but answers a different question than the plot being
exported); see *Fixed 2026-10-02* below. It was briefly 25 traces, which was wrong.

**Still rough / open:**

- [ ] **The `resid.` axis label sits on the trace**, rotated, instead of beside the
      axis. Visible in every fit export. Probably `lblPos(resid)` or a margin.
- [ ] **Legend: populate it as matplotlib's does** (decided 2026-10-02). It reads
      `data` / `fit`; the matplotlib legend carries the whole parameter block (A, B1,
      tau1, the sign warning, the residual split). Plumbing, not new formatting — see
      the next-step note at the end of this section.
- [ ] **Never verified: two segments loaded into ONE Igor experiment.** That is what
      the wave prefixing exists for and it has not been tried.
- [x] ~~**A 2-D wave export existed and was removed**~~ — superseded 2026-10-02: the
      spectra export IS a 2-D wave again (`73f58cf`). What is still open is how it is
      DISPLAYED — see *Igor bogs down* below. The `SetScale` version is in git
      (`53c526b`) if an image view is wanted.

**Fixed 2026-10-02:** the spectra export wrote 25 evenly spaced curves, not all of
them. On a 721-spectrum CV that is a sparse fan where the figure is a dense band --
visibly a different plot. It now writes the whole block as ONE 2-D wave plus a
wavelength and a time wave, and puts it on a graph with one `AppendToGraph` per
column (longest command 75 chars, against the 14 KB a single 721-trace `Display`
would have been -- which is why it was thinned in the first place). Real CV: 12.1 MB,
0.4 s. `SPECTRA_TRACES` survives as an escape-hatch cap, defaulting to None.

**Also 2026-10-02:** the spectra plot's CSV button is no longer masked. It had been
hidden on the grounds that the block is already on disk as .h5 and .txt, but that is
not the same numbers -- the CSV is what the FIGURE shows, after any wavelength
window. The block is widened by `FigureDialog._table()`: wavelength down column 1,
one column per time, with a header line saying so because a wide table is not
self-describing. A real CV is 1261 x 722 and 18.1 MB; that size is the user's call at
save time, not ours at build time.

**Igor bogs down on the spectra graph (reported 2026-10-02).** Worth being precise
about where: the data is ALREADY one 2-D wave, so the file is not the problem. What
is slow is 721 TRACES on one graph -- 721 `AppendToGraph` plus 721 `ModifyGraph rgb`,
and then Igor redrawing all of them. the user notes this is unusual for Igor, so it is
the trace count specifically, not the volume.

So the fix is on the DISPLAY side, not the data side. The thinning that was just
removed was aimed at the right problem in the wrong place -- it threw away data to
make the graph cheap. Options, in the order they look promising:

1. **Write every column, display a subset.** All 721 in the matrix wave; `Display`
   only ~25 of them, with a comment giving the one-line loop that appends the rest.
   Keeps the file complete and the graph fast, and the full block is a click away.
2. **`NewImage`/`AppendImage` on the matrix** as the default view, with the fan as
   the opt-in. Fast at any size, and an image of absorbance(wavelength, time) is a
   legitimate view -- just not the one the figure draws.
3. **HDF5 directly.** Igor talks HDF5 (the user: via a plugin; built in from Igor 7/9,
   CONFIRM which before relying on it). We already write .h5, so this could be no new
   export at all -- just a documented "open the .h5 in Igor" path. Best long-term
   answer if the loader handles our layout; check how it names groups and datasets as
   waves, and whether the 2-D orientation survives.

Not decided. Pairs with the plot-modification history below -- how the user actually
styles a spectra graph may well settle which of these is wanted.

**THE EFFICIENT NEXT STEP, offered 2026-10-02:** the user formats one graph in Igor the
way he would want it and hands over the COMMAND HISTORY. That is worth more than any
amount of reading: it gives the exact commands, in his conventions, for the plot that
matters -- and would settle the resid. label, the legend and anything else in one
pass. Ask for it rather than guessing again.

Along with it: **populate the legend as matplotlib's does** -- the whole parameter
block (A, B1, tau1 and their SDs, y(0), the sign warning, mean tau with its CI, the
point count, the residual split). Decided 2026-10-02; the text already exists, it is
what FitResult builds for the figure legend, so this is plumbing rather than new
formatting.

**Note for whoever continues:** the Igor commands here come from the documentation,
not from experience with Igor. Four rounds of bench feedback were needed to get this
far, each one catching something that looked right in the file and wrong on screen.
Expect the same of any addition, and check it in Igor rather than by reading.

## macOS console: "has active key-value observers (KVO)" — WATCHING, not fixed

Seen on the Mac 2026-10-01, naming `QPushButtonClassWindow`. Qt's Cocoa backend
reporting that it recreated a widget's native NSWindow; no frame of ours is in it,
and it does not appear on Win11.

**Hypothesis, unverified:** native-sibling promotion. A matplotlib FigureCanvas is a
native widget and Qt promotes its siblings to native too, which recreates their
window. The counts fit — 2 messages on a tab 4 launch, which has exactly 2
`Save figure…` buttons beside canvases; 1 on the Band Fits tab, which has 1.

**Not acted on, deliberately.** The warning is benign in practice, and the offscreen
Qt platform falls back to Fusion with no Cocoa, so neither the symptom nor a fix can
be reproduced in a test. The plausible mitigation is to wrap each save row in its own
container widget so the button is not a direct sibling of the canvas — about five
lines, and one launch would show whether the messages stop. The user is gathering more
evidence first.

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

## Document the trigger cable build (the user, 2026-07-14)

`docs/sop.md` §2.1 gives the trigger *endpoints* (Gamry DIGOUT0 → Avantes DB26 pin 6) but not how
the cable is **made**: Gamry-side connector and which conductor carries DIGOUT0, DB26 shell and pin-6
termination, ground/shield, cable length. That knowledge currently exists only in the head and in
the single cable on the bench — if it's damaged, or a second rig is built, there's nothing to work
from. A placeholder marks the spot in the SOP. **Needs the bench notes / photos.**

## Automated tests for the GUI layer

**Started 2026-07-27** — `tests/test_gui_layout.py` is the first coverage of `gui/`: 4 tests, headless
via `QT_QPA_PLATFORM=offscreen`, guarded with `pytest.importorskip("qtpy")` so the suite still runs
where Qt isn't installed. That resolves the "Qt in the 32-bit env" objection below — the tests skip
rather than fail.

**Updated 2026-10-03: 257 of 779 tests now exercise `gui/`** (`test_gui_layout.py` 190,
`test_band_tab.py` 33, `test_figure_dialog.py` 21, `test_figure_render.py` 9,
`test_dark_save.py` 4). Every bug in the 0.2.0 cycle (stale absorbance after a wavelength
re-slice, status labels outliving their data, load-before-connect, a discarded segment still
reaching the Results tab) lived in **GUI wiring**, and the core suite passed through all of
them. Of the targets below, Start-in-every-mode and the segment selector now have tests;
**Stop-vs-Abort enablement, load-before-connect, dark/ref dropped on widen, and discarded
segments staying out do not appear by name** — check before assuming they are covered:

- Run-tab state machine: Start → finish → Start, Stop vs Abort button enablement.
- Instrument-tab guards: load-before-connect, dark/ref dropped when the wavelength window widens.
- Results tab: segment selector across refreshes; discarded segments staying out.

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
      folded in as a spurious 4th dedoping cycle — a spec-echem-side fix, independent of the upstream
      reader being repaired. Touches: `data.py` write path, GUI `discover_run_segments` + Results/Load-Run (still
      let you review it), and the timing tooling. Decide default-vs-opt-in, and confirm nothing
      downstream expects pre-dedoping in the main folder (coordinate upstream alongside the
      "combined 2026 format" discussion — see [[reference-oect-processing]] in memory).
- [ ] **`+100` magic offset on the chrono `Time (s)` column.** The converter sets
      `Time = Corrected + 100`. Likely vestigial (downstream keys off `Corrected time`, which starts
      at 0). Confirm nothing depends on it, then drop or document.
- [ ] **Positional CV column drop is fragile.** CV conversion drops columns `[0,3,4,5,6,7,8]` by
      position. Select potential/current by name instead.
- [ ] **Multi-cycle CV is concatenated** into one series (loops overlay). Fine for I-vs-E plotting;
      just noted — revisit if per-cycle separation is ever needed.

## Decide later (triggered)

- [ ] **Roll our own raw-`.DTA` parser** to drop the `gamry_parser` dependency — only when triggered
      (distribution/reproducibility need, `gamry_parser` breaks/unmaintained, or GUI-automated
      conversion). Check `gamry_parser` license first (likely MIT) to learn from it.

## Post-Phase-2.5 follow-ups (mirror of STATUS.md)

- [ ] **Two-thread simplification check.** The empty-echem-file bug was a signal refcount/GC issue,
      not threading. Re-evaluate whether the per-segment dedicated thread + fresh-session-per-segment +
      `acq_data()`-in-loop machinery in `potentiostat.py` is still needed, or whether a simpler
      same-thread design works. Best done on the instrument box (hardware-tested). The `acq_data()`
      poll in the run loop is flagged in-code as unconfirmed-necessity.
- [ ] **First real-sample test (the gold standard).** Real polymer sample, real dark (lamp blocked) +
      reference (blank, lamp on), full multi-cycle sequence in one Start; then confirm the output
      analyzes cleanly in `OECT_processing`. External mode is real-test-ready today; Python mode
      is ready now that echem capture landed.

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

### MEASURED AT THE BENCH, 2026-09-24 — the straddle is NOT the cause

Everything above this line is the 2026-09-16 reasoning. It is kept because it is a good
account of how the wedge was narrowed down, but **its conclusion is wrong** and the
guard built from it does not fix the glitch. Four runs on a 10 kOhm dummy, 100 mV/s,
10 mV steps:

- **`20260924_test1` (GUI, -0.5 to +0.7 V).** Nine glitches on screen, **zero** dropped
  samples in the log — so `_read_ei_pair` never fired on any of them. `CV.txt` had
  **zero** points off the line out of 480 (fit R = 9870.8 ohm, worst residual 3.9e-7 A).
- **`bench_ei_pair_source.py` (bare CV, no spectrometer).** Straddles essentially absent:
  mid-sweep staleness median 0.063 steps, p95 0.246. The wedge does NOT reproduce without
  the GUI's acquisition loop sharing the thread.
- **`20260924_test2` (GUI, live stream dumped).** 241 live samples. Seven mid-sweep points
  displaced by **+30.55, +31.20, +31.35, +32.12, +32.42, +32.13 and -30.68 mV** — every one
  about **three staircase steps**, with the sign following the sweep direction.

**The mechanism is a LAG, not a straddle.** At 100 mV/s, 31 mV is 0.31 s of sweep: the
potential is roughly three polls behind the current, persistently. A straddle is one
refresh between two reads and is worth at most ONE step. Because the stale potential is
*stable*, `before == after` passes every time — the guard is structurally blind to it.

**`Ei.Potential` is not quantized** (median 1.58 mV from the nearest 10 mV multiple), so
it is a measured value with its own noise, and bit-identical consecutive reads really do
mean the latch did not refresh. MEASURED: it does not refresh on 43.5% of 100 ms polls.

**A second, separate bug: the point at the origin.** `20260924_test2` samples 0-3 read
`E = 0.0000 V, I = 0.0000e+00 A` — EXACTLY zero, four times, from t = 1.114 s. `pump()`
starts sampling before the latch has ever been loaded, and the plot draws the origin.
The recorder's own `CalcTime[0]` (1.250 s there) marks when the staircase actually starts.

**Trap for anyone writing a bench script here.** `FHCyclicVoltammetry2`'s parameters put
**step at [3] and stop at [5]** — swapped relative to the order the NOVA manual prints
them in (`docs/autolab-run-api.md` §1). `bench_live_cv.py` had the manual's order and so
wrote `step = 0.0`. A zero-step staircase **records 0 points, stops early, and otherwise
looks like a clean successful run** — the same failure shape as the Gamry's GC'd signal
object. The as-loaded defaults are the tell: [3] is 0.00244 (a step), [5] is 0.0.

- [x] **Close the straddle window.** DONE (`_read_ei_pair`), and **it does not fix the
      wedge** — see above. Keep it: it costs one property read and it does guard `Ei`
      mode, where the "nothing else refreshes" assumption is still untested. But it is
      not the fix, and the TODO it came from stays open below.
- [x] **Still use `sample_ei`'s return value.** DONE. Correct on its own merits — a failed
      refresh must not be appended as a duplicate point under a fresh timestamp — and, in
      `Ei` mode, that sample would reach `steps(N).txt`. Not the cause of the wedge.
- [x] **Consider not building the live CV trace from the latch at all.** ANSWERED, and the
      answer reverses the standing assumption: **`.Signals` DOES fill during the run.**
      MEASURED 2026-09-24 (`bench_live_cv.py`, 4 cycles): 0 points at 1.2 s climbing to
      1040 at 118 s, 106 distinct intermediate counts, 1040 after completion.

      The 2026-09-03 evidence ("`Abort()` leaves .Signals completely empty") was the
      ambiguity it was flagged as: an abort that discards its buffer looks identical to a
      buffer that never filled. It never filled *because it was aborted*.

      **So this is now the recommended fix, not a dead end.** In procedure mode the
      recorder is the authoritative source, is already what `CV.txt` uses, and is
      provably clean on a dummy — drawing the live trace from it removes the lag by
      construction. In `Ei` mode the latch stays (it IS the data there), and a lag cannot
      make a wedge because the potential is held constant per segment.

- [x] **Draw the live CV trace from `.Signals` in procedure mode.** The fix for the wedge.
      `run_tab.py:377` calls `pot.live_data()`; in procedure mode that should read the
      recorder's arrays rather than `_live_samples`. Keep `pump()` sampling regardless —
      the overload flags are only readable while the run is going.
      DONE (`7b9da23`), **confirmed on the rig 2026-09-25** (`20260925_test1`): no wedge
      on screen across a 3-cycle CV, and no "could not read the recorder" warning.
- [x] **Do not plot a sample before the latch has content.** The fix for the origin point.
      An exactly-zero (E, I) pair is trivially detectable, and the first recorded
      `CalcTime` says when the staircase really began.
      DONE (`7b9da23`), **confirmed on the rig 2026-09-25**: the CV dropped 2 pre-latch
      samples; every chrono file kept all 301 rows (same count as `20260916_test1`).
- [ ] **Cadence stall, seen 2026-09-24.** `20260924_test2` logged spectra cadence
      `max 1139.0 ms, jitter(sd) 67.5 ms` against `20260924_test1`'s `max 140.5, sd 6.2`.
      A 1.1 s stall in the acquisition loop, on the same rig, minutes apart. NOT the cause
      of the wedges (the gaps at all seven glitch points were a normal 102-141 ms), but
      unexplained and new.
      **Did not recur 2026-09-25** (`20260925_test1`, 6 segments): max 140.1 ms, sd at most
      8.6 ms. Still unexplained; keep watching the cadence line.
- [x] **Spectra gaps at every chrono hand-off (2026-09-25).** FIXED and confirmed
      (`20260925_test7`: max 122-129 ms in every segment). The worker now waits for the
      GUI between segments; see `docs/bench-2026-09-25.md` §4. Original note: `20260925_test4`: 260-463 ms
      gaps in the first ~2 s of each chrono segment, CV clean. Probable cause: GUI-thread
      redraw of the previous segment (absorbance plot 360-1320 ms, Results + Analysis
      refresh 0.6-1.7 s) competing for the GIL. Fix: `LineCollection` for the absorbance
      traces; refresh Results/Analysis lazily. See `docs/bench-2026-09-25.md` §4.

**How to re-examine any of this without the rig.** `SPECECHEM_LIVE_DUMP=1` in the
environment makes the Autolab driver write `{folder}/{label}_live_samples.csv` — the
stream the live plot actually draws, which is otherwise never persisted and dies with the
run. Off by default; adds a file beside the data and changes no existing format.

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

**STATUS 2026-10-02: written, read, and selectable.** `data_format` on the
Parameters tab chooses `h5+ascii` (default, today's behaviour) / `h5` / `ascii`,
for WRITING and for which Load Run prefers. Reading always falls back to whatever
is in the folder. Verified on the 20250710 run: all 13 segments load identically
from either source — wavelengths and times exact, absorbance differing by 2.98e-08
(the float32 compromise) — in 0.01 s against 2.20 s, from 81 MB against 904 MB.
**Retiring the ascii is now a decision, not a blocked task:** switch to `h5` once
real data has gone through, and tell the `OECT_processing` maintainer first: its reader takes the text files.

**Why:** disk. A single long run already writes ~1.6 M rows per spectra file, and the
8-column tab-separated format stores every wavelength value again for every time point.
HDF5 stores the wavelength axis once and the absorbance matrix as a typed array — an
order of magnitude smaller, and faster to read back. `OECT_processing` already
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

### `OECT_processing` already has a writer — `oect_processing/specechem/uvvis_h5.py`

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

### Two questions to settle upstream before building (Requested: needs a conversation)

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

Practical framing for the conversation upstream: write **our** neutral superset, and make it
trivially convertible to his layout, rather than adopting a shape derived from his objects.

### Sizes, measured (2026-09-11)

A realistic 14-segment run, 1265 wavelengths, ~5100 time points — **645 MB of ascii today**:

| layout | size | vs ascii |
|---|---|---|
| `OECT_processing`'s, as written (`df.values`, float64) | 51.6 MB | 12x |
| `OECT_processing`'s, float32 | 25.9 MB | 25x |
| **archival: absorbance f32 + counts u16 + dark + ref** | **38.8 MB** | **17x** |
| counts only, absorbance derived | 13.0 MB | 50x |

Raw counts are `uint16` **exactly** — the ADC is 16-bit — so they cost half what float32
absorbance does. The complete archival file is a ~50% surcharge over absorbance-only, and is
still *smaller* than `OECT_processing`'s current float64 file while holding strictly more.

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

### What "core" means — `oect_processing/specechem/`

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
  time-columned DataFrame — the same shape the `OECT_processing` methods operate on — and `gamry_data.read_cv()` /
  `read_chrono()` give the echem side. The work is selection UI and plotting, not analysis maths.
- **One shared definition of the ladder already exists** (`data.segment_potential`, added
  2026-09-11 so graph titles cannot drift from what was applied). An abs-vs-voltage plot should
  use it rather than re-deriving potentials.
- Which `OECT_processing` methods to port is a question for upstream, the same
  conversation as the H5 layout.

## Conversations with collaborators — kept OUT of this repo

**This repository is public.** Notes about a named collaborator — which of his methods look
historical, what he has not fixed, what to ask him — do not belong in it. They live outside the
repo in `../private-notes/`. Credit their work by repository name
(`rajgiriUW/OECT_processing`), never by a person's name (the user, 2026-10-03).

The *technical* content stays here where it is useful: the H5 layout and sizing above, and the
`read_files.py` bugs, which are ordinary bug reports and are better sent as a pull request anyway.

## Archive — closed, kept for the record

Moved here verbatim on 2026-10-03 so the top of the file is only what is open. Nothing
was deleted: these are finished, and their reasoning is still worth having.

### Getting data and figures OUT (2026-09-15) — the ask, not started

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
- [x] ~~**2. Figure export via `NavigationToolbar2QT`.**~~ — **superseded 2026-09-30**
      by the figure-export work at the top of this file. The toolbar alone was the
      wrong answer: its save writes at whatever size the widget happens to be, which
      is the bug, not the feature. Pan/zoom/home are kept; SVG and PDF are offered by
      the preview's save dialog alongside PNG.
- [x] ~~**3. HDF5**~~ — written, read, and now what Load Run opens. See the HDF5
      section below and the `data_format` setting on the Parameters tab.
- [x] ~~**4. Igor Text (`.itx`), not `.pxp`.**~~ — **done 2026-10-02**.
      `Save Igor (.itx)…` beside `Save data (CSV)…` in the figure preview, writing
      the same numbers as the CSV: waves, a `Display`, and nothing else. Styling is
      left to Igor, which is the point of exporting to it.

      `spec_echem/igor_export.py`, tested without Igor on the machine because .itx
      is plain text — the tests parse it the way Igor's loader reads it.

      **Still open, if wanted:** a whole-segment export (one segment's full
      absorbance matrix as a 2-D wave with `SetScale` for wavelength and time, so
      plots can be built in Igor rather than reproduced). About 7 MB of text per
      segment, which is tolerable; not built because the ask was "the graphs".

### Release gate for v0.3.0 — one bench run before merging `gui-dev` → `main` (the user, 2026-07-27)

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

### Mid-run Gamry USB pull — DIAGNOSED + FIXED 2026-07-27

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

### "Test your setup" probes — Avantes done, Autolab connect probe to follow (the user, 2026-07-14)

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

### Wavelength window is a hardcoded pixel slice — CLOSED 2026-09-04, not worth fixing

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

### Integration-time unit — RESOLVED to milliseconds (2026-06-18)

The unit is **milliseconds**, end to end: `settings.py` key `integration_time_ms` → GUI spin value
passed straight through `set_integration_time()` → Avantes `m_IntegrationTime` (SDK defines it in
ms), with NO conversion. Confirmed on hardware 2026-06-18 — `spectrometer.py` printed
"Integration time set to 0.022 ms". The lone outlier was the CLAUDE.md doc (said "seconds") — now
**fixed** to ms. No code change needed (everything already agrees on ms).

- [x] **Label the GUI integration-time spin box "(ms)"** — DONE: the spin box already sets
      `.setSuffix(" ms")` (`instrument_tab.py`), so the unit shows inline in the field.

### Phase 2 — Python potentiostat (EchemToolkitPy)

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

### Echem plotting in the GUI (Phase 1)

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

*(Its two open items moved to **Now — backlog** on 2026-10-03.)*

