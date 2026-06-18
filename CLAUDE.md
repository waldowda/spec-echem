# spec-echem — Claude Code Project Context

## What This Project Is

`spec-echem` synchronizes electrochemical measurements (Gamry Ref-600+ potentiostat) with
UV-Vis spectroscopy (Avantes spectrometers) for spectroelectrochemistry experiments. The system
performs combined measurements during cyclic voltammetry and doping/dedoping cycles, enabling
simultaneous analysis of electrochemical and optical properties — primarily conjugated polymers
and organic mixed ionic-electronic conductors (OMIECs).

The key technical challenge is precise temporal correlation between the two instruments, solved
via GPIO-based hardware triggering.

**GitHub:** github.com/waldowda/spec-echem (private)  
**Zenodo DOI:** 10.5281/zenodo.17221314  
**Status:** Pre-release — API is not stable

---

## Repository Structure

```
spec-echem/
├── spec_echem/                      # Python package (import spec_echem)
│   ├── __init__.py
│   ├── spectrometer.py              # AvantesSpectrometer class
│   └── globals.py                   # Global variables for Avantes SDK
├── notebooks/
│   └── spec_echem_exp_*.ipynb       # Jupyter experimental workflows
├── gamry/
│   └── *.GSequence                  # Gamry sequence files with digital triggers
├── docs/
│   └── data-format.md               # Output file format specification (DO NOT CHANGE)
├── examples/                        # Example scripts (in development)
├── tests/                           # Unit tests (in development)
├── data/                            # Sample data directory
├── README.md
├── setup.py
├── requirements.txt
└── .gitignore
```

New Python modules go in `spec_echem/`. Notebooks go in `notebooks/`. Gamry files in `gamry/`.

---

## Hardware Architecture

Three coordinated components:

1. **Gamry Ref-600+ Potentiostat** — Applies potentials, measures current. Runs sequences defined
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

### `get_spectra()` — Core data acquisition function

Currently lives in notebooks; being modularized into `spec_echem/`.

```python
get_spectra(
    measconfig,           # Spectrometer config from init()
    added_path,           # Output subfolder, format: YYYYMMDD_Description
    dark=None,            # Dark spectrum — None preserves previously stored value
    ref=None,             # Reference spectrum — None preserves previously stored value
    deltaTime=0.100,      # Seconds between spectral acquisitions
    num_echem_points=301, # Number of spectra to collect
    data_type=1,          # See data type codes below
    run_number=0,         # Cycle counter for file naming
    trigger=False         # Whether to use GPIO triggering
)
# Returns: (spectra, absorb7)
```

### Sequence Wizard

Interactive script for a complete experiment:
1. Collects user input (folder name, CV parameters, chrono parameters, number of cycles)
2. Runs CV with synchronized spectra
3. Runs pre-dedoping baseline
4. Loops through N doping/dedoping cycles with incrementing potentials

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

Files are tab-separated, saved to:
`C:\Users\inst-chem\Documents\specechem_data\{added_path}\{filename}`

**8 columns:**

| # | Header | Content | Notes |
|---|--------|---------|-------|
| 1 | Wavelength (nm) | Wavelength calibration | Same for every time point |
| 2 | Absorbance | Calculated absorbance | A = −log₁₀((Sample − Dark) / (Ref − Dark)) |
| 3 | Column 3 (a. u.) | Dark spectrum | Only for time_point == 0; empty otherwise |
| 4 | Column 4 (a. u.) | Reference spectrum | Only for time_point == 0; empty otherwise |
| 5 | Measured value (a.u.) | Raw intensity | Direct spectrometer output |
| 6 | Spectrum number | Integer index | Sequential across all time points |
| 7 | Time (s) | Absolute timestamp | Seconds since acquisition start |
| 8 | Corrected time (s) | Relative timestamp | Time relative to first spectrum in step |

Downstream analysis tools at UW depend on this format. Do not change column names, order,
separator, or naming conventions without explicit instruction.

**Downstream analysis repo:** `rajgiriUW/OECT_processing` (github.com/rajgiriUW/OECT_processing),
maintained by Raj Giri. The `oect_processing/specechem/read_files.py` module reads spec-echem
output files and explicitly depends on the `spectra(N).txt` / `dedopingspectra(N).txt` naming.

**Known bug in OECT_processing (not spec-echem):** Two commits May 26–27 2026 accidentally
changed `read_files.py` to read `Potential`/`Vf` from spectra files instead of the Gamry steps
files (`WE(1).Potential (V)`). The 8-column spec-echem format is correct — no changes needed.
Fix: in `read_files.py` lines ~76–85, revert `specfiles[0]` back to `stepfiles[0]`. Notify Raj.

**Steps files dependency:** Gamry `.DTA` steps files must be in the same folder as spectra files
for `current_vs_time()` to work. Gamry and spec-echem output directories must match.

**Absorbance calculation pipeline:**
```
raw spectra (wavelength_pixels × num_time_points)
    → transmittance = (spectra - dark) / (ref - dark)
    → absorbance = -1 * np.log10(transmittance)
    → absorb3_df = pd.DataFrame(absorb3)
    → absorb4 = absorb3_df.T
    → absorb5 = absorb4.set_index(wavelengths)
    → absorb6 = absorb5.T  →  absorb6.index = timeStamp_diff
    → absorb7 = absorb6.T  ← this is what gets written to file
```

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

### Gamry ToolkitPy Migration
Migrating from `.GSequence` files + GPIO to Gamry's `EchemToolkitPy` Python library.

- **Current:** `.GSequence` files define experiment sequences; GPIO is used as the sync signal
- **Planned:** `gamry_interface.py` module using `EchemToolkitPy` API directly in Python
- **Status:** EchemToolkitPy is 32-bit Python only; Gamry targeting 64-bit support ~September 2026
  (historically late). Plan around 32-bit until further notice.
- **Architecture gate PASSED (2026-06-18):** in one 32-bit env (`SpecEchem32`, Python 3.7.13),
  `toolkitpy 7.11.0` and `avaspec` both import and the spectrometer measures — so Phase 2 is a
  single 32-bit app driving both instruments (no two-process split). Setup recipe + the trigger
  validation that preceded it are captured in the project memory notes.
- **What stays the same:** Avantes interface, `get_spectra()` output format
- **What changes:** `.GSequence` files → Python scripts; GPIO middleman may be eliminated if
  ToolkitPy can trigger spectrum collection directly

When `gamry_interface.py` work begins: confirm ToolkitPy API patterns first, then implement.

### GUI
Planned instrument control GUI to replace the Jupyter notebook workflow.

- **Which env runs the GUI (important):** the GUI currently talks ONLY to the Avantes spectrometer
  (`avaspec`), which lives in the **64-bit `SpecEchem`** env (Python 3.13). So run the GUI there NOW:
  `conda activate SpecEchem; pip install PyQt5 qtpy matplotlib; python -m gui`. PyQt5 + PyQt5-sip
  have prebuilt cp313 win_amd64 wheels → no compiler needed.
- **Phase 1 (now):** 64-bit SpecEchem env. PyQt5 + QtPy + embedded matplotlib. No Gamry Python
  control yet — `.GSequence` + hardware trigger; Python only drives the spectrometer.
- **Phase 2 (EchemToolkitPy integration, until Gamry ships 64-bit ~Sept 2026):** GUI must run in the
  **32-bit `specechem32`** env to call EchemToolkitPy. CAUTION: `pip install PyQt5` fails there —
  `PyQt5-sip` has no prebuilt 32-bit wheel and tries to compile (needs MSVC C++ Build Tools).
  Solve when we get there (prebuilt 32-bit sip wheel, or install Build Tools in specechem32).
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
- **Doping-potential fields are documentation-only in this phase** — the Gamry sequence file holds
  the real potentials. GUI fields (`doping_potential_start/end/step`, `dedoping_potential`,
  `prededoping_potential`) are saved to the run metadata JSON but do NOT drive the experiment until
  EchemToolkitPy arrives. Label them clearly in the UI as "recorded for reference."
- **Logging:** one log file per run, named to match data folder and saved alongside data:
  `specechem_data/YYYYMMDD_Name/YYYYMMDD_Name.log`. DEBUG to file, INFO to UI status log.
- **No live plot updates** — plots update post-segment only (simplifies threading)
- **Stop vs Abort:** Stop finishes current acquisition cleanly; Abort is immediate (confirm dialog).
  NOTE: Abort must check `abort_event` inside the spectrometer measure poll loop, not just the
  acquisition loop — otherwise Abort won't respond while blocked waiting for a trigger (the most
  common wait state). `threading.Event` is stdlib, so this doesn't violate the no-Qt-in-core rule.
- **Run metadata:** `write_run_metadata()` writes `{folder}/{folder}_metadata.json` at run start —
  sample name, electrolyte, notes, and full settings snapshot — making each data folder self-documenting.

### Modularization
- Move `get_spectra()` from notebooks into `spec_echem/` as a proper module function
- Add unit tests that mock hardware dependencies

---

## Citation

```
Waldow, D. (2025). spec-echem: Spectroelectrochemistry instrument control system.
Zenodo. https://doi.org/10.5281/zenodo.17221314
```
