# spec-echem — TODO

Open work only. **Pruned 2026-11-09:** finished items now leave this file when they are
closed. Their reasoning, measurements and the record of what was closed are in
[`docs/todo-history.md`](docs/todo-history.md), and section names cited by older docs and
commits ("Figure output", "Live CV plot…", the PITT build history) are there.
The CHANGELOG says what shipped; git log says when.

## Needs the rig

- [ ] **PITT on a real film — the priority.** Runs end to end on both Gamrys and the
      Autolab with dummies; cutoff-ended steps seen only on the Autolab (2026-10-19). The
      cutoff is opt-in (default: fixed holds). Next: a film on the Reference 600, with the
      return leg (kinetics vs hysteresis). A slow series-RC dummy (1 MOhm + 1 uF, tau 1 s,
      0.15 V steps, 10% cutoff) would show the Gamry ending a step on the cutoff.
      Open questions for films: judge the cutoff against the settled leak floor rather
      than zero? A noise-floor check for the Gamry? PITT analysis (dQ per step, g(E))
      waits until the user is ready.
- [ ] **GITT on dummies, then a film.** Built 2026-10-29, tested on fakes only
      (`spec_echem/gitt.py`, `docs/data-format.md` §6, probe results in
      `docs/bench-2026-10-26.md`). The UDC4 Randles (tau 3 ms) looks like a resistor at
      0.1 s, so use about **1000 uF** (electrolytic is fine for + pulses: + toward WE,
      keep E under ~1 V):
      1. **Series RC**, 1 kOhm + 1000 uF, +10 uA, 10 s pulses, 30 s relax, 3 pulses,
         window -0.1 to +0.5 V. Expect a 10 mV IR step at each pulse start and end, a
         10 mV/s ramp during pulses, flat relaxations: a 100 mV staircase. Discharge
         between runs.
      2. **Randles**: add 10 kOhm across the capacitor (tau 10 s); tick the dE/dt rule and
         the relaxations should end `relaxed` before 30 s.
      3. **Window stop**: run 1 with the upper limit at +0.15 V; it must END in the 2nd
         pulse with `e_limit`, cell off. Repeat with -10 uA and a lower limit
         (`set_stop_v_min` is untried on hardware).
      4. **Autolab**: the same three. 10 uA should be quiet about the 0 A offset; 0.2 uA
         should warn. Check `pulse_s_applied` in `gitt_steps_json`.
      Not built: return leg, end dedope, analysis (dQ, relaxed E, IR drop, D_ion).
- [ ] **Autolab impedance (ER-EIS) is blocked on hardware.** NOVA's own TestFRA fails on
      its dummy (the AC signal seems never to reach the cell). Send Metrohm the failed
      TestFRA data; ask earlier EIS users when it last worked. Once NOVA reads a 10 kOhm
      dummy correctly, rerun `examples/probe_autolab_gitt_eis.py --fra`,
      `--fra --fra-vdc 0.1`, `--fra-manual`, and a Randles for the sign convention.
      Gamry ReadZ works on both instruments; an ER-EIS runner must restore `ie_range`
      and the filters after each sweep.
- [ ] **Charge tab on a fresh run:** every view, the CSV, and a step that genuinely
      fails the 5 tau or drift check.
- [ ] **Sample the current faster in the first second of a chrono step** (as PITT's fast
      phase does), so the charge before the first sample is measured, not estimated.
      Probe the achievable rate on a dummy first. Gamry runs log no cell-ON-to-first-
      sample time, so their `Q_head_max_mC` column is empty.
- [ ] **Check one real-film run downstream in `OECT_processing`.** Real-film runs exist
      since 2026-10-07 (CVs) and 2026-11-05 (doping ladders); that the output analyses
      cleanly there has not been recorded.
- [ ] **Figure export: open an exported CSV beside its plot** on real data (only ever
      checked on synthetic).
- [ ] **Autolab hold noise vs NOVA** on the same dummy (bandwidth, integration time).
      Also explain the isolated -5 to -6 nA single-sample dips that follow late Ei samples
      (~10% of late samples dip; <1% of a step's charge). Does NOVA's own sampling show
      them?
- [ ] **Autolab zero offset per run.** It is not a stable constant (CR09_10mA read
      +1.6 uA one day, -1.1 uA another). Measure it cell-off at 0 V on the run's range
      and write it to the run metadata; never subtract it from recorded data. Also: what
      calibration does NOVA offer, and how often?
- [ ] **Gamry "Noise Reject" equivalent** — check the Reference 600 on its 600 uA range
      first; the 1010E already reaches ~4 nA with the filters alone.
- [ ] **Decode the `acq_data` overload field** from the DEBUG values a real run logs
      when flags appear; then decide whether an uncorroborated warning is worth restoring.
- [ ] **Stray Stop:** if a run ever stops on its own again, it is not the focus bug fixed
      earlier (Stop and Abort are click-focus only). Close this if it has not recurred.
- [ ] **Document the trigger-cable build** (connector, conductor, ground/shield, length)
      in `docs/sop.md` §2.1. Needs bench notes or photos.
- [ ] **Write down how toolkitpy gets into the conda env** on a machine where it works.
      Moot once the pip-installable 64-bit toolkit lands.
- [ ] **Current-range ladder on a Reference 610+**, if one is to hand
      (`examples/probe_gamry_ladder.py`, read-only).
- [ ] **Two-thread simplification check** in `potentiostat.py` (per-segment thread +
      fresh session + `acq_data()` in the loop): still needed? Hardware-tested only.
      Related: a Gamry Connect opens two sessions (identity, then ladder); merging them
      halves the open/close cycles on a USB stack that has failed under them.
- [ ] **Does the PGSTAT302N have a hardware charge integrator?** Would remove the
      head-of-step charge loss entirely. Deferred by the user.

## Needs a decision

- [ ] **The `fast-tau-check` branch** (`9daaee6`, built 2026-10-03, not merged). On real
      data it flags 2.5% of biexp fits, nearly all in the first steps of one run, and it
      flags the whole fit though the slow tau may be fine. Merge as is; flag only the
      fast component; or drop it.
- [ ] **A hold before the CV** — on hold (2026-10-07: the doped start was a setup slip).
      Keep or drop.
- [ ] **Move the current-range control to tab 1**, one control beside the instrument
      choice (two dropdowns cost two runs on 2026-09-25). And: a range per segment TYPE?
- [ ] **Past Gamry data** from Python-mode runs before 2026-09-25 (range never set): does
      anything need re-taking?
- [ ] **When to stop writing the ascii.** Tell the `OECT_processing` maintainer first.
- [ ] **Lock the auto wavelength across a ladder?** It drifts 783 -> 808 nm with
      potential on the reference run, probably a real red-shift.
- [ ] **Probe fallback when no peak survives:** say "no band found" rather than the
      global argmax?
- [ ] **The Modulation view** does not depend on the selected segment but sits behind
      the segment selector. Move it, or show the selector does not apply.
- [ ] **Repository history: rewrite with `git filter-repo`, during a visit to the Autolab
      rig.** Dry run passed 2026-11-11; the runbook and rules are in private notes. Push
      only when every clone can be re-cloned the same day (an old clone's push restores
      the old history). Save each rig's `config/bench.ini` first.

## Code, no rig needed

- [ ] **Cut v0.4.0** — 224 commits since v0.3.1. Review (`/code-review`) BEFORE the merge
      to `main`, the v0.3.0 lesson.
- [ ] **Charge plots in "Export all figures"** (it writes the fit traces and the Kinetics
      ladder only).
- [ ] **Doping +0.9 V on the 2026-11-05 run:** exp passes at only 8 of 180 wavelengths
      (831–931 nm, tau 17–180 s). Look at that trace.
- [ ] **Same-sign prefactors option** on the Analysis tab, off by default: fit twice
      (all B >= 0, all B <= 0), keep the better; key it off the `B*` names in `MODELS`.
- [ ] **Richer models**, in this order (design in `docs/analysis-design.md`):
      1. joint polaron + pi-pi* fit with SHARED tau;
      2. write down the polaron -> bipolaron derivations (they specify tau3);
      3. tri-exponential with tau1/tau2 pinned by step 1 (tau3 is not identifiable alone);
      4. re-check the model ranking per system.
- [ ] **A second probe wavelength for the bipolaron band** — fit two at once and compare.
- [ ] **Offer only the current ranges the connected Autolab accepts** (11 of 20). Needs
      the instrument connected before the Parameters tab is filled.
- [ ] **A range-finding test run:** drive only the ladder's extremes on a coarse range,
      report peak and settled currents and a suggested range. Not autoscaling.
- [ ] **`Find saturation` at the floor:** when Start is already the detector's minimum,
      advise only "attenuate the light". (`LIN_STOP_FLOOR_SPANS = 8` is a starting
      guess, not physics.)
- [ ] **Student guide for the data folder:** Save location is the PARENT, the folder
      name is the subfolder the app creates (heads off double nesting).
- [ ] **Auto-verify the Gamry when Python mode is selected?** Weigh the session open/
      close cost against the click it saves.
- [ ] **GUI tests still missing:** load-before-connect, dark/ref dropped when the
      wavelength window widens, and the Results segment selector across refreshes.
- [ ] **Igor export:** the `resid.` label sits on the trace; populate the legend as
      matplotlib's does; two segments in one experiment never tried; the 721-trace
      spectra graph is slow (display a subset, an image, or open the .h5). Best settled
      by the user styling one graph in Igor and handing over the command history.

## With collaborators

- [ ] **DOS:** v2 with a capacitive baseline (shown on the plot); an absolute energy axis
      (needs a ferrocene calibration in the same electrolyte); a scan-rate series before
      any DOS is trusted.

## Later — wanted, not scheduled

- Per-monomer doping level (needs film density and monomer mass).
- Tune `FIT_MAX_TAU_SPANS = 10` and `FIT_SD_REJECT_FRACTION = 0.5` against a second
  sample (judgement calls, set on one).
- After figures are made from real data: review the single-column preset; move
  `private-notes/figure-export-design.md` to `docs/figure-export.md`.
- Expose the hard-coded `measconfig` fields (smoothing, saturation detection, averaging).
- Linearity ramp defaults remembered per light source.
- AvaLight lamp/shutter control (automates dark and reference). Check the hardware first.
- Import an arbitrary `.nox` and run it (Autolab).
- Pre-dedoping files in a subfolder (also avoids an `OECT_processing` mis-sort).
- Gamry DTA converter, if the parser is ever ours: pre-dedoping files, the `+100` time
  offset, positional CV column drop. Our own `.DTA` parser only if `gamry_parser`
  breaks or distribution needs it.
