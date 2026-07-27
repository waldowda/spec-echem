# Changelog

All notable changes to spec-echem are recorded here.

This project is **pre-release** — the API is not stable, and minor versions may change it.
The output file format is the exception: it is treated as fixed, because downstream analysis
([`OECT_processing`](https://github.com/rajgiriUW/OECT_processing)) depends on the exact column
names, ordering, and filenames. See [`docs/data-format.md`](docs/data-format.md).

---

## [Unreleased]

### Added

- **Build identity.** `spec_echem.build_id()` reports `0.2.0` at a tag and `0.2.0+5.gaadf15a`
  between tags (`.dirty` if the tree has uncommitted changes). It now appears in three places:
  the **run metadata JSON** (`spec_echem_version`), the **first line of every run log**, and the
  **GUI title bar**.

  The gap it closes: a run folder recorded every *setting* it used but nothing about the code that
  applied them — so two runs a release apart looked identical on disk even though one of them could
  crop the wavelength axis and the other couldn't. Most runs happen on commits *between* tags, which
  is why a bare version string wouldn't have been enough. `build_info.py` is also now the single
  source of the version: `setup.py` reads it out rather than keeping a second copy that can drift.

- **App log — logging now starts at launch, not at Start.** `{data_root}/logs/spec-echem.log`,
  rotated nightly and kept indefinitely, opened before the window appears. There is an
  **Open Log Folder** button on the Run tab.

  Each launch writes a banner carrying the build id, the **Python version, bitness and conda env**,
  and whether `avaspec` / `toolkitpy` imported. That last part earns its place: "Python mode is
  greyed out" looks like a dead potentiostat but is nearly always the wrong environment, since
  `toolkitpy` is 32-bit only. A pasted log now answers that without anyone having to ask.

  Until now the only logging began when you pressed Start, so everything before a run — connecting
  the spectrometer, collecting a dark, a *failed* connect — went nowhere but the shell, if anywhere.
  The Run tab's status pane looks like a log but is per-run and cleared at Start, so a student
  looking there after a bad connect saw nothing at all. The handler sits on the `spec_echem` package
  logger, so every module reaches it, and the run logger propagates up: a run appears in both logs.

  The **per-run log is unchanged** and still written inside the data folder, so it travels with the
  data. The two answer different questions — "how was this folder produced?" versus "what did I do
  this afternoon, and where did it go wrong?"

- **Instrument identity is recorded.** A run folder named its code (the build id) and every setting
  but never its *hardware*. The run log and the metadata JSON (`instruments`) now carry the
  spectrometer and potentiostat as reported at Connect — e.g. `Avantes serial 7513391SP` /
  `Gamry Duck (serial 08083)`. Simulated runs self-label as `simulated`, so they can't later be
  mistaken for real data. In External mode the potentiostat is recorded as not queried rather than
  guessed at, since Python never opens it there.

- `examples/identify_hardware.py` — read-only script that prints the attached instruments' identity.
- `examples/query_avantes.py` + `query_avantes_setup.md` — standalone, zero-dependency "can this PC
  talk to the Avantes from Python?" self-check (plus a read-only Metrohm/Autolab USB-presence scan),
  for anyone setting up the repo on new hardware.
- `examples/query_autolab.py` + `query_autolab_setup.md` — the Metrohm counterpart: a standalone,
  read-only, **cell-safe** Autolab connect probe (our own `pythonnet`/`clr`, not a dependency on the
  stale pyMetrohmAUTOLAB). Connects, reports `IsConnected`, disconnects — never touches the cell.
- README and SOP now name the **exact tested hardware**: Avantes AvaSpec-VRS2048CL-EVO
  (2048 px, 300–1100 nm optical configuration, 50 µm slit) and a Gamry Reference 600 — with an
  explicit note on what else *should* work (any Gamry `EchemToolkitPy` supports, e.g. the Interface
  1010 series) but has not been exercised. Models only; no serial numbers.

### Fixed

A cross-model code review (GUI + concurrency) surfaced ten issues; all are fixed and, where
possible, covered by new headless tests. Timing-critical paths (arm-then-fire ordering, the Gamry
poll loop, the live-plot timer) were deliberately left untouched.

- **Gamry no longer runs the waveform blind on the sample after a spectrometer failure.** If the
  spectrometer failed to arm (or any early error occurred) in Python mode, `finish()` still released
  the Gamry thread and it applied the full CV/hold with zero spectra collected. `finish()` now
  cancels a segment that never fired. *(the headline; new test)*
- **Setup failures surface instead of hanging.** A Gamry open/build failure now raises from
  `prepare()` rather than arming the spectrometer for a trigger that never comes; and potentiostat
  errors now go to the **run log + status pane** (they were logged to a sibling logger that no
  handler watched, so they were silent). *(new test for the raise)*
- **Stop no longer disables Abort** — pressing Stop while a segment waits for its trigger used to
  strand the app with no way out; Abort now stays live.
- **Pre-dedoping "Duration" now works.** The field was collected but ignored; pre-dedoping used the
  doping/dedoping step time. It now drives the pre-dedoping duration (spectra count and Python-mode
  hold). *(new test)*
- **Spectra count off-by-one.** `int(x + 1)` truncated exact ratios that land at `N−ε` in binary
  float (e.g. 1.2 s / 0.1 s → 12 instead of 13); now `round`. Clean ratios are unchanged. *(new test)*
- **A run is frozen at Start** — the potentiostat read the live settings dict, so a mid-run "Save
  Settings" could change the potentials applied to later segments; it now gets a snapshot.
- **Results tab shows only the current run** — `win.results` is cleared on Start (stale segments from
  a longer prior run no longer linger), and its segment selector keeps your selection across updates.
- **Instrument-tab hygiene** — Connect can no longer be re-enabled mid-run by toggling the mode
  radios; the acquisition worker/thread now tear down with the safe `deleteLater` pattern.
- **The spectrometer no longer prints to the shell.** Ten `print()` calls left over from the
  notebook era became log records, so their content is kept (timestamped, in the app log) instead of
  scrolling past in a terminal nobody keeps. The per-segment trigger-mode line is `debug`, file-only.
- **"Invalid index" is now a real error message** — `No Avantes spectrometer found. Check the USB
  cable, and close AvaSoft or any other program using the spectrometer.` The old text named an
  internal condition, which tells a student nothing about what to do. The connect-failure text now
  lives in a **wrapping** label under the button rather than inline beside it: a `QLabel` in a layout
  asks for its full text width and the layout grants it, so the longer message dragged the whole
  window past its half-column width. This is also the **first automated test coverage of `gui/`**.
- **The run log no longer claims a segment is armed before it is.** The line was written before the
  Gamry setup that precedes arming, so a setup failure read as though the spectrometer had already
  armed into a trigger that would never come.
- **The blind-run safety net now says so.** When a segment that never fired is cancelled, the log
  records that the waveform was not applied — previously it acted silently, so a log showing only
  the upstream spectrometer error left the Gamry's behaviour unaccounted for.

---

## [0.2.0] — 2026-07-14

The instrument-setup release. 0.1.0 could run an experiment; 0.2.0 helps you set the instrument up
*correctly* before you do, and remembers how your rig is configured.

### Added

- **Spectrometer linearity check** (Instrument tab). Ramps the integration time with the reference
  in the beam, fits the linear region, and recommends a working integration time.

  The detector turned out to stay linear to within ~1 % right up to where it hard-clips, so a
  deviation-from-fit criterion alone leaves almost no headroom (it lands at ~94 % of full scale).
  The recommendation therefore takes the **tighter** of *5 % below the linearity limit* or a
  **max fill fraction of full scale** (default 85 %) — and in practice the fill cap is what binds.
  **Find saturation** brackets the clip point by doubling, then bisects it, so it reports a real
  number rather than a power of two. Hardware-validated 2026-07-13.

- **Wavelength window** (opt-in). Crop the noisy lamp edges (below ~400 nm, above ~1050 nm) so they
  aren't written into every file. Includes a data-driven suggestion from the noise floor of a blank
  test-absorbance. Off by default: the full range remains the default output.

- **Test (sample) view** — take a single spectrum of whatever is in the beam **without overwriting
  the dark or the reference**. This closes a real hole in the workflow: the reference is taken with
  a blank FTO insert, and once you swap in the actual sample, a plain "Collect New" would have
  silently destroyed the reference by recording the sample as 100 %T.

- **Bench defaults** (`config/`). A hand-editable INI layer for the settings that describe *the rig*
  (lamp, detector, machine paths) as opposed to *the experiment*. `config/defaults.ini` is tracked
  and lab-wide; `config/bench.ini` is per-machine and gitignored, written by **Save as defaults**.
  Precedence: code defaults → lab defaults → this machine → a loaded experiment JSON. The reader is
  deliberately forgiving — a typo in a hand-edited file warns and is skipped rather than taking the
  app down on launch. See [`config/README.md`](config/README.md).

- **Pre-dedoping: "Run it, but discard the data."** Runs the step normally (the film is still
  conditioned) but writes nothing for it — no spectra `.txt`, no echem `.txt`, no `.DTA` — and keeps
  it out of the Results tab, since there's no saved data to review.

### Changed

- **Instrument tab reorganized.** Spectrometer Settings and Linearity Check share the left column;
  Dark / Reference / Test (sample) are tabs on the right. The wavelength range moved in with the
  integration time, where it belongs. The tab no longer sprawls compared to the other three.
- **Integration time now shows 4 decimal places** — at a ~0.02–0.11 ms working range, 3 decimals was
  rounding the recommendation to two significant figures.
- **`docs/sop.md` rewritten around the GUI.** The notebook workflow is preserved as an appendix.

### Fixed

- Applying a narrowed wavelength window now re-slices the test-absorbance too, and redraws it —
  previously the plot you were looking at when you clicked Apply was the one that didn't update.
- Widening the window (or changing it to an unrelated range) now **drops** the dark/reference rather
  than leaving stale arrays behind, and the status labels no longer claim data that is gone.
- Dark/reference can no longer be loaded before the spectrometer is connected, which used to accept
  a spectrum with no wavelength axis to match it against.
- Instrument-tab plots no longer expand without bound.

### Notes

- `EchemToolkitPy` (Python Gamry mode) remains **32-bit only** until Gamry ships 64-bit support,
  targeted ~Sept 2026. In a 64-bit environment the app automatically falls back to External mode.
- The `gui/` package still has no automated test coverage; the core package has 143 tests.

---

## [0.1.0] — 2026-07-10

First release of the unified package + GUI line.

### Added

- **PyQt5 GUI** (`python -m gui`) — four tabs (Instrument, Parameters, Run, Results) replacing the
  Jupyter-notebook workflow as the primary way to run an experiment.
- **Modular `spec_echem` package** — the acquisition, orchestration, data-writing, and settings code
  moved out of notebooks into an importable, testable package with no Qt and no hardware imports.
- **Python Gamry control** via `EchemToolkitPy` (`ToolkitPotentiostat`), selectable alongside the
  original External (`.GSequence`) mode. Both drive the same segment recipe; they differ only in who
  starts the Gamry. DIGOUT0 → Avantes hardware trigger sync is preserved in both.
- **Echem capture** (Python mode) — potential/current written next to the spectra, plus native
  Gamry `.DTA` files.
- **Results tab** with per-segment review and **Load Run…** for reopening previous runs.
- Run metadata JSON and a per-run log file written into every data folder.

### Notes

- Output format unchanged from the notebook era and verified against `OECT_processing`.
