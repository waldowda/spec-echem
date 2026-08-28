# spec-echem — Claude Code Project Context

## What This Project Is

`spec-echem` synchronizes electrochemical measurements (Gamry Ref-600 potentiostat) with
UV-Vis spectroscopy (Avantes spectrometers) for spectroelectrochemistry experiments. The system
performs combined measurements during cyclic voltammetry and doping/dedoping cycles, enabling
simultaneous analysis of electrochemical and optical properties — primarily conjugated polymers
and organic mixed ionic-electronic conductors (OMIECs).

The key technical challenge is precise temporal correlation between the two instruments, solved
via hardware triggering — the Gamry's DIGOUT0 output is wired directly to the Avantes trigger input.

**GitHub:** github.com/waldowda/spec-echem (private)  
**Zenodo DOI:** 10.5281/zenodo.17221314  
**Status:** Pre-release — API is not stable

---

## Repository Structure

```
spec-echem/
├── spec_echem/                      # Core package — NO Qt, NO hardware imports
│   ├── __init__.py
│   ├── spectrometer.py              # AvantesSpectrometer class
│   ├── potentiostat.py              # Gamry control (External + Python/toolkitpy)
│   ├── acquisition.py, experiment.py  # Segment acquisition + orchestration
│   ├── data.py, gamry_data.py       # Spectra + echem file writers/readers
│   ├── linearity.py                 # Integration-time linearity fit + recommendation
│   ├── spectral_range.py            # Wavelength-window recommendation
│   ├── settings.py                  # Experiment settings dict (per-run)
│   ├── bench.py                     # Bench defaults (per-rig), reads config/*.ini
│   ├── build_info.py                # __version__ + build_id() — SINGLE source of the version
│   ├── logging_config.py            # Per-run log file
│   ├── fakes.py                     # Hardware fakes — the suite runs with no instruments
│   └── globals.py                   # Global variables for Avantes SDK
├── gui/                             # PyQt5 GUI (4 tabs); run via `python -m gui`
├── config/                          # defaults.ini (tracked, lab-wide) + bench.ini (per-rig, ignored)
├── notebooks/                       # Legacy Jupyter workflow (still functional)
├── gamry/
│   └── *.GSequence                  # Gamry sequence files with digital triggers
├── docs/
│   ├── data-format.md               # Output file format specification (DO NOT CHANGE)
│   ├── sop.md                       # Standard operating procedure (GUI-first)
│   └── inspect-run.md
├── examples/                        # Bench/validation scripts + identify_hardware.py
├── tests/                           # Unit tests (165) — no hardware required
├── data/                            # Sample data directory
├── CHANGELOG.md                     # What changed between versions
├── STATUS.md                        # Human-readable project status + next steps
├── TODO.md                          # Task list / deferred cleanups
├── README.md
├── setup.py                         # Reads the version out of build_info.py — don't hardcode it
├── requirements.txt
└── .gitignore
```

New Python modules go in `spec_echem/`. Notebooks go in `notebooks/`. Gamry files in `gamry/`.

---

## Hardware Architecture

Three coordinated components:

1. **Gamry Ref-600 Potentiostat** — Applies potentials, measures current. Runs sequences defined
   in `.GSequence` files. Uses DIGOUT0 (digital output pin) to send trigger pulses.

2. **Avantes Spectrometer** — Collects UV-Vis spectra. Controlled via the proprietary `avaspec`
   Python module (comes with the Avantes SDK, not pip-installable).

3. **Hardware Triggering** — Gamry's DIGOUT0 output is wired directly to the Avantes hardware
   trigger input. The `avaspec` SDK detects the trigger via `m_Trigger_m_Mode` in `MeasConfigType`.

---

## Dependencies

```
numpy>=1.19.0
matplotlib>=3.3.0
pandas>=1.3.0
scipy>=1.5.0
avaspec           # Avantes proprietary — NOT pip-installable, requires Avantes SDK + hardware
EchemToolkitPy    # Gamry proprietary — NOT pip-installable, requires Gamry Framework install
```

**Multi-machine constraint:** `avaspec` and `EchemToolkitPy` are only present on machines with
vendor hardware physically connected. Code touching these must handle missing imports gracefully
(conditional imports or mock objects). Dean works across multiple computers.

**avaspec.py setup (Win11 instrument machine):** Copy from
`C:\AvaSpecX64-DLL_9.14.0.0\examples\PyQt5_simple\avaspec.py` to
`C:\Users\inst-chem\AppData\Local\anaconda3\envs\SpecEchem\Lib\site-packages\avaspec.py`, then make three edits:
1. Comment out `import globals` (unused vestigial import)
2. Comment out `from PyQt5.QtCore import *` (unused vestigial import)
3. Replace the Windows DLL block to use `os.add_dll_directory(r"C:\AvaSpecX64-DLL_9.14.0.0")`
   before the `WinDLL("avaspecx64.dll")` call (fixes relative path failure in Jupyter)

Do NOT copy a fresh SDK file over this without reapplying these three edits.

---

## Key Modules

### `spec_echem/spectrometer.py` — AvantesSpectrometer class

| Method | Returns | Notes |
|--------|---------|-------|
| `init()` | `(measconfig, serial_number)` | Must call before measuring |
| `set_integration_time(time)` | — | Time in **milliseconds** — passed straight to Avantes `m_IntegrationTime` (no conversion). e.g. `50` = 50 ms. Matches the `integration_time_ms` settings key. |
| `set_scan_averages(n)` | — | Scans to average per measurement |
| `measure()` | `(timestamp, spectrum)` | Single spectrum acquisition |
| `wavelengths()` | `(_, wavelength_array)` | Calibration wavelengths |
| `plot_data(wavelength, spectrum)` | — | Quick visualization |

### Acquisition pipeline (modularization DONE — the notebook `get_spectra()` is legacy)

An experiment is a list of `Segment`s, run one at a time:

```python
build_segments(settings) -> [Segment(label, data_type, run_number,
                                     num_points, delta_time, trigger, save=True)]

run_one_segment(spec, segment, dark, ref, wavelengths, data_root, added_path,
                abort_event=None, potentiostat=None)
    # -> (absorbance_df, path)  |  (absorbance_df, None) if segment.save is False
    #    None if aborted (a partial segment is never written)
```

- `acquire_segment()` (`acquisition.py`) does the triggered collection. `measure()` must fire
  `on_armed` **from inside itself, after arming** — an edge raised before the spectrometer is armed
  is silently MISSED. Only spectrum 0 of a segment is hardware-triggered; the rest free-run.
- `segment.save=False` means "run it, write nothing" (the pre-dedoping *discard* option). All three
  writers honour it: the spectra `.txt`, the echem `.txt`, and `ToolkitPotentiostat._write_dta`.
  Discarded segments also never reach `win.results`, so they don't appear in the Results tab.
- The GUI's Run tab builds the segment list and hands it to a worker thread (`gui/workers.py`).

### Version / build identity

`spec_echem.build_id()` → `"0.2.0"` at a tag, `"0.2.0+7.g0f26a7a"` between tags, `.dirty` suffix for
an edited tree, bare version when there's no git. It is written to the **run metadata JSON**
(`spec_echem_version`), the **first line of every run log**, and the **GUI title bar** — so a data
folder can always name the code that produced it.

`__version__` lives ONLY in `spec_echem/build_info.py`; `setup.py` reads it out by regex. Don't add
a second copy.

---

## Data Type Codes

| Code | Experiment | Output Filename |
|------|-----------|-----------------|
| 1 | Cyclic Voltammetry | `CVspectra.txt` |
| 2 | Doping | `spectra(N).txt` |
| 3 | Dedoping | `dedopingspectra(N).txt` |
| 4 | Pre-dedoping | `prededopingspectra(N).txt` |

N = `run_number`. **Note: parentheses in filenames are literal** — `spectra(0).txt` not `spectra_0.txt`.

---

## Output File Format — DO NOT CHANGE

**The authoritative spec is [`docs/data-format.md`](docs/data-format.md)** — spectra files
(8 columns), the Phase 2.5 echem `.txt` files (`CV.txt` / `steps(N).txt` / etc.), the native
`.dta` outputs, and the absorbance pipeline. Downstream analysis tools at UW depend on these
formats; do not change column names, order, separator, or filename conventions without explicit
instruction.

Files are tab-separated, saved under `{data_root}/{added_path}/`
(e.g. `C:\Users\inst-chem\Documents\specechem_data\20260705_P3HT\`).

**Downstream analysis repo:** `rajgiriUW/OECT_processing` (github.com/rajgiriUW/OECT_processing),
maintained by Raj Giri. The `oect_processing/specechem/read_files.py` module reads spec-echem
output files and explicitly depends on the `spectra(N).txt` / `dedopingspectra(N).txt` naming.

**Known bug in OECT_processing (not spec-echem):** Two commits May 26–27 2026 accidentally
changed `read_files.py` to read `Potential`/`Vf` from spectra files instead of the Gamry steps
files (`WE(1).Potential (V)`). The 8-column spec-echem format is correct — no changes needed.
Fix: in `read_files.py` lines ~76–85, revert `specfiles[0]` back to `stepfiles[0]`. Notify Raj.

**Steps files dependency:** Gamry `.DTA` steps files must be in the same folder as spectra files
for `current_vs_time()` to work. Gamry and spec-echem output directories must match.

---

## Coding Conventions

- `snake_case` for all function and variable names
- Named constants for data types: `DATA_TYPE_CV = 1`, `DATA_TYPE_DOPING = 2`, etc.
- Input validation with descriptive error messages
- Debug output gated behind a `debug` flag
- **Pandas deprecation fix:** use `df[df.columns[i]] = newvals` not `df.iloc[:, i] = newvals`
- Preserve exact numerical behavior when refactoring — output format must not change
- Hardware-dependent imports must be conditional (handle `ImportError` gracefully)

---

## Planned Work / Active Migration

### Gamry ToolkitPy Migration — IMPLEMENTED (Phase 2), hardware-validated 2026-07-04
All-Python Gamry control via `EchemToolkitPy` (`toolkitpy`) is implemented in
`spec_echem/potentiostat.py` and selectable at runtime alongside the original manual `.GSequence`
workflow.

- **Two modes, one seam:** `ExternalPotentiostat` (human starts a `.GSequence`; byte-identical to
  Phase 1) vs `ToolkitPotentiostat` (Python drives the Gamry and fires DIGOUT0 itself). Both run the
  SAME recipe from `build_segments()` — they differ only in *who starts the Gamry*. The guarded
  `import toolkitpy` auto-forces External mode where the 32-bit stack is absent.
- **Trigger unchanged (and never GPIO):** Gamry DIGOUT0 is wired directly to the Avantes hardware
  trigger input. The DIGOUT0 edge is raised only AFTER the spectrometer is armed (`AVS_Measure`),
  matching the proven legacy ordering.
- **Phase 2.5 — echem capture (2026-07-05):** `ToolkitPotentiostat` runs the Gamry on a **dedicated
  per-segment thread** (owns a fresh toolkitpy session end to end), synced to the spectrometer via an
  `armed` event; `finish()` joins it and hands `acq_data()` to `write_echem_file()` (clean `.txt`) and
  `print_default_dta_file` (native `.dta` in `dta/`). **Root-cause gotcha:** the toolkitpy signal
  object must be kept alive (a live Python ref) through `run()` and the poll loop — a dropped local is
  GC'd immediately and the curve runs an empty/degenerate waveform (0 data). Open follow-up: with the
  signal kept alive, whether the dedicated-thread machinery is still necessary is unconfirmed —
  candidate simplification.
- **Status:** `toolkitpy` is 32-bit Python only; Gamry targets 64-bit support ~September 2026
  (historically late). Plan around 32-bit until further notice. External mode stays the default + fallback.
- **Architecture gate PASSED (2026-06-18):** in one 32-bit env (`SpecEchem32`, Python 3.7.13),
  `toolkitpy 7.11.0` and `avaspec` both import and the spectrometer measures — so Phase 2 is a single
  32-bit app driving both instruments (no two-process split).
- **Validated on hardware (SpecEchem32, 2026-07-03/04):** all four segment types (CV + doping +
  dedoping + pre-dedoping) run in Python mode with golden 8-column output; the trigger handshake was
  confirmed via `examples/diag_trigger_timing.py` and `examples/bench_coacquire.py`.
- **Unchanged:** the Avantes interface and the output format.

### GUI
Planned instrument control GUI to replace the Jupyter notebook workflow.

- **Which env runs the GUI (important):** the GUI currently talks ONLY to the Avantes spectrometer
  (`avaspec`), which lives in the **64-bit `SpecEchem`** env (Python 3.13). So run the GUI there NOW:
  `conda activate SpecEchem; pip install PyQt5 qtpy matplotlib; python -m gui`. PyQt5 + PyQt5-sip
  have prebuilt cp313 win_amd64 wheels → no compiler needed.
- **Phase 1 (now):** 64-bit SpecEchem env. PyQt5 + QtPy + embedded matplotlib. No Gamry Python
  control yet — `.GSequence` + hardware trigger; Python only drives the spectrometer.
- **Phase 2 (EchemToolkitPy integration, CURRENT until Gamry ships 64-bit ~Sept 2026):** GUI runs in
  the **32-bit `SpecEchem32`** env (Python 3.7.13) to call `toolkitpy`. 32-bit PyQt5 is SOLVED —
  install with `pip install --only-binary=:all: "PyQt5==5.15.2" "PyQt5-sip==12.11.0"` (5.15.2 is the
  last self-contained release with a 32-bit win32 wheel that bundles Qt; newer PyQt5 pulls `PyQt5-Qt5`,
  which has no 32-bit wheel). Also needs qtpy/matplotlib/pandas. Python potentiostat control is
  implemented + hardware-validated in this env.
- **Phase 3 (post Gamry 64-bit):** everything 64-bit; optionally swap to PySide6 via QtPy.
- **Why QtPy abstraction:** keeps the binding swappable across these phases (PyQt5 now, PySide6 later)
  with no code changes. PySide6 has no 32-bit Windows wheel, so PyQt5 is the binding for Phase 2.
- **Why matplotlib not PyQtGraph:** all plots are post-segment/static, so PyQtGraph's live-update
  edge is moot. matplotlib is one fewer dependency, familiar, publication-style, and works under
  both PyQt5 and PySide6 via `FigureCanvasQTAgg`. Rendering happens on the GUI thread, never the
  acquisition thread, so it cannot threaten the ~50 ms/spectrum timing budget. If live update is
  ever wanted, throttled redraw (2–5 Hz) of in-memory data stays well within matplotlib's range —
  PyQtGraph would only be needed for 30–60 Hz rendering, which human monitoring never requires.
- **PySide6 version pin:** when migrating, pin below 6.9.1 (active regressions in 6.9.x)
- **UI pattern:** tabbed layout (4 tabs), not QWizard.
- **Architecture:** thin GUI over current workflow first; swap in EchemToolkitPy backend later.
  Threading: worker-object + `moveToThread()` (not QThread subclass).
- **Potentiostat status (phase-aware):** keep a potentiostat status indicator in the layout, but in
  the 32-bit phase Python CANNOT control or query the Gamry — the `.GSequence` file runs
  autonomously and fires hardware triggers. Show it as "Gamry: standalone (runs from sequence file)"
  in a neutral/disabled state now; it becomes a live green/red connection indicator when
  EchemToolkitPy arrives. The real coordination today is a two-step manual sequence: (1) student
  clicks Start → worker arms spectrometer and BLOCKS on the first trigger → UI shows "Armed — now
  start the Gamry sequence"; (2) student starts the Gamry sequence manually → triggers fire →
  collection proceeds. The UI must make this two-step start explicit so students aren't confused.
- **Triggering stays Gamry-in-charge / Avantes-listening** — Gamry DIGOUT0 wired to the Avantes
  hardware trigger input. This arrangement is kept through the EchemToolkitPy migration.
- **Doping-potential fields are mode-dependent:** in **External mode** they are documentation-only
  (the `.GSequence` holds the real potentials) and are labeled "recorded for reference"; in **Python
  mode** they DRIVE the run — `ToolkitPotentiostat` applies `doping_potential_start + run_number*step`,
  `dedoping_potential`, `prededoping_potential`, and the CV vertices (`cv_initial_v` / `cv_limit1_v` /
  `cv_limit2_v` / `cv_final_v`). Saved to the run metadata JSON in both modes.
- **Logging:** one log file per run, named to match data folder and saved alongside data:
  `specechem_data/YYYYMMDD_Name/YYYYMMDD_Name.log`. DEBUG to file, INFO to UI status log.
- **No live plot updates** — plots update post-segment only (simplifies threading)
- **Stop vs Abort:** Stop finishes current acquisition cleanly; Abort is immediate (confirm dialog).
  NOTE: Abort must check `abort_event` inside the spectrometer measure poll loop, not just the
  acquisition loop — otherwise Abort won't respond while blocked waiting for a trigger (the most
  common wait state). `threading.Event` is stdlib, so this doesn't violate the no-Qt-in-core rule.
- **Run metadata:** `write_run_metadata()` writes `{folder}/{folder}_metadata.json` at run start —
  `spec_echem_version` (the build id), sample name, electrolyte, notes, and a full settings snapshot,
  making each data folder self-documenting.

### Metrohm / Autolab rig — bring-up done, findings in `docs/metrohm-rig-status.md`
spec-echem was brought up on a Metrohm-Autolab rig (Autolab **PGSTAT10** + AvaSpec-**ULS2048L**,
2026-08-28). **Read [`docs/metrohm-rig-status.md`](docs/metrohm-rig-status.md)** — it is the
cross-session handoff. Headlines: the Autolab connects under **64-bit** Python (no 32/64-bit split,
unlike Gamry); the SDK 2.1 **does** expose digital I/O (`Instrument.Dio`) plus `Ei` / `LoadProcedure`
/ `Sampler`; and the DIO→Avantes hardware trigger works from one Python process
(`examples/query_avantes_trigger.py`). So a Python-driven Autolab backend in `potentiostat.py` (the
analogue of `ToolkitPotentiostat`) is the recommended direction. Open item there: the calibrated
pixel window (`CAL_START_PX`/`CAL_STOP_PX` in `spectrometer.py`) is hardcoded for the original
VRS2048CL-EVO and needs to become bench-configurable — a user on the ULS2048L rig needs >1100 nm.

### Modularization — DONE
`get_spectra()` is out of the notebooks and split across `acquisition.py` / `experiment.py` /
`data.py`; hardware is faked (`fakes.py`) so all 165 tests run with no instruments attached.

### Settings: two layers, don't confuse them
- **Experiment settings** (`settings.py`, `DEFAULT_SETTINGS`) — *this run*: sample, folder, CV
  vertices, potentials. Saved/loaded as JSON from the Parameters tab.
- **Bench defaults** (`bench.py`, `config/*.ini`) — *this rig*: integration time, scan averages,
  wavelength window, linearity ramp, `data_root`, `potentiostat_mode`. `config/defaults.ini` is
  tracked and lab-wide; `config/bench.ini` is per-machine and gitignored ("Save as defaults").
  Precedence: code defaults → lab defaults → this machine → an explicitly loaded experiment JSON.
  `data_root` and `potentiostat_mode` are deliberately ABSENT from the tracked file (machine-specific).

### Known gaps (see TODO.md)
- **`gui/` is barely tested.** 165 tests total, of which exactly 4 touch `gui/`
  (`tests/test_gui_layout.py`, headless via `QT_QPA_PLATFORM=offscreen`). Every bug in the 0.2.0
  cycle lived in GUI wiring and the core suite passed through all of them, so this is where new
  coverage pays. Qt-dependent tests must `pytest.importorskip("qtpy")` — the suite has to keep
  running in environments with no Qt.
- **The trigger cable's build** (connector, pinout, shielding) is undocumented — only its endpoints.

---

## Citation

```
Waldow, D. (2025). spec-echem: Spectroelectrochemistry instrument control system.
Zenodo. https://doi.org/10.5281/zenodo.17221314
```
