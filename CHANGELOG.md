# Changelog

All notable changes to spec-echem are recorded here.

This project is **pre-release** — the API is not stable, and minor versions may change it.
The output file format is the exception: it is treated as fixed, because downstream analysis
([`OECT_processing`](https://github.com/rajgiriUW/OECT_processing)) depends on the exact column
names, ordering, and filenames. See [`docs/data-format.md`](docs/data-format.md).

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
