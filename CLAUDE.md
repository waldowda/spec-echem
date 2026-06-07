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
| `set_integration_time(time)` | — | Time in seconds (e.g., 0.05 = 50 ms) |
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
- **Status:** Awaiting confirmation from Gamry on Python version constraints (docs say 3.7 32-bit
  but this likely reflects tested version not a hard constraint)
- **What stays the same:** Avantes interface, `get_spectra()` output format
- **What changes:** `.GSequence` files → Python scripts; GPIO middleman may be eliminated if
  ToolkitPy can trigger spectrum collection directly

When `gamry_interface.py` work begins: confirm ToolkitPy API patterns first, then implement.

### Modularization
- Move `get_spectra()` from notebooks into `spec_echem/` as a proper module function
- Add unit tests that mock hardware dependencies

---

## Citation

```
Waldow, D. (2025). spec-echem: Spectroelectrochemistry instrument control system.
Zenodo. https://doi.org/10.5281/zenodo.17221314
```
