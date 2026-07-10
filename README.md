# spec-echem

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.17221314.svg)](https://doi.org/10.5281/zenodo.17221314)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

A Python library for synchronized spectroelectrochemistry experiments using Avantes spectrometers and Gamry potentiostats. Currently, the approach is to use Python as a Jupyter notebook to acquire spectra with the Avantes spectrometer and be set to wait from a trigger from the Gamry potentiostat. The Gamry is initially running using the sequence wizard but in a future release it will also be run using Python in a PyQt5 windowing environment. Additional information will be provided regarding the trigger wiring as well.

## ⚠️ Pre-Release Notice

This software is currently in **pre-release** (v0.1.0). The API and functionality are subject to change. Use in production environments at your own risk.

## Overview

`spec-echem` provides tools for performing synchronized spectroscopic and electrochemical measurements, enabling real-time optical monitoring during electrochemical experiments. The package coordinates:

- **Avantes spectrometer** control for UV-Vis spectroscopy (~380-1100 nm)
- **Gamry potentiostat** triggering for synchronized data acquisition
- Hardware triggering via Avantes trigger input for precise temporal correlation
- Automated data collection and storage

## Features

- 🔬 Real-time spectrum acquisition triggered by electrochemical events
- ⚡ Hardware triggering via Avantes trigger input synchronized to Gamry DIGOUT0
- 📊 Integrated data processing for both spectroscopic and electrochemical data
- 🔄 Support for complex electrochemical sequences (CV, chronoamperometry, stepping protocols)
- 💾 Synchronized data storage with timestamps
- 📈 Built-in visualization tools for spectroelectrochemical data

## System Requirements

### Hardware
- Avantes spectrometer (tested with models supporting 380-1100 nm range)
- Gamry potentiostat with digital output capabilities
- USB connections for instruments

### Software Prerequisites

1. **Avantes SDK and Python bindings**
   - Contact Avantes to obtain the SDK for your operating system
   - Install the `avaspec` Python module (provided with SDK)
   - Configure USB drivers for your spectrometer

2. **Python Dependencies** (see `requirements.txt`)
   ```bash
   numpy>=1.19.0
   scipy>=1.5.0
   matplotlib>=3.3.0
   pandas>=1.3.0
   PyQt5      # GUI only
   qtpy       # GUI only
   ```

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/waldowda/spec-echem.git
cd spec-echem
```

### 2. Install Avantes SDK

Follow the installation guide provided by Avantes for your specific spectrometer model. Ensure the `avaspec` Python module is properly installed and accessible.

### 3. Install the Package

Activate your conda environment (e.g., `SpecEchem`), then install in development mode. This can be done before the Avantes SDK is installed — the package handles missing hardware dependencies gracefully.

```bash
conda activate SpecEchem
pip install -e .
```

## Project Structure

```
spec-echem/
├── spec_echem/              # Main package
│   ├── __init__.py         # Package initialization
│   ├── spectrometer.py     # Avantes spectrometer control class
│   └── globals.py          # Global variables for SDK
├── notebooks/               # Jupyter notebooks
│   └── SpecEchem Avantes 0.996-20250717.ipynb  # Main experimental workflow
├── gamry/                   # Gamry sequence files
│   └── Spec_Echem_20250714.GSequence  # Example sequence with triggers
├── examples/                # Example scripts (coming soon)
├── docs/                    # Documentation (in development)
├── tests/                   # Unit tests (in development)
└── data/                    # Sample data directory
```

## Quick Start

### Basic Spectrometer Usage

```python
from spec_echem import AvantesSpectrometer

# Initialize spectrometer
spec = AvantesSpectrometer()
measconfig, serial_number = spec.init()
print(f"Connected to spectrometer: {serial_number}")

# Configure measurement
spec.set_integration_time(0.05)  # 50 µs integration time
spec.set_scan_averages(200)      # Average 200 scans

# Acquire spectrum
timestamp, spectrum = spec.measure()

# Get wavelength calibration
_, wavelength = spec.wavelengths()

# Plot results
spec.plot_data(wavelength, spectrum)
```

### Synchronized Spectroelectrochemistry

See the Jupyter notebook `notebooks/SpecEchem Avantes 0.996-20250717.ipynb` for a complete example of:
- Setting up hardware triggers
- Coordinating with Gamry sequences
- Real-time data acquisition
- Data processing and visualization

### Where your data is saved (GUI)

When you start a run in the GUI, files are written to **`‹Save location›\‹Data folder name›\`**:

- **Save location** is the *parent* folder — leave it at `…\Documents\specechem_data`. Just point at the
  parent; you don't need to create a run folder here.
- **Data folder name** is the run folder, which the program **creates for you** (e.g.
  `20260704_P3HT_KPF6`). It is pre-filled with today's date — just add a short description. If the app has
  been open past midnight, click **Today** to bump the date (it keeps your description).

> ⚠️ Don't browse *into* a folder you made yourself for the Save location, or you'll get a doubled path
> like `…\20260704_test\20260704_test\`.

## Workflow

1. **Configure Gamry Sequence**: Load the provided `.GSequence` file or create your own with digital output triggers
2. **Initialize Spectrometer**: Set integration time and averaging parameters
3. **Setup Trigger Detection**: Avantes hardware trigger input detects Gamry DIGOUT0 signal
4. **Run Experiment**: Start Gamry sequence and collect triggered spectra
5. **Process Data**: Analyze synchronized electrochemical and spectroscopic data

## Data Format

**The authoritative output-format spec is [`docs/data-format.md`](docs/data-format.md).** All output is
tab-separated. Per run folder (`‹Save location›\‹Data folder name›\`):

- **Spectra files** — 8 columns (wavelength, absorbance, dark, reference, raw counts, spectrum number,
  time, corrected time), one per segment: `CVspectra.txt`, `spectra(N).txt` (doping),
  `dedopingspectra(N).txt`, `prededopingspectra(N).txt`. (Parentheses in filenames are literal.)
- **Electrochemistry files** (Python/EchemToolkitPy mode) — the Gamry potential/current per segment:
  `CV.txt` (potential, current) and `steps(N).txt` / `dedoping(N).txt` / `prededoping(N).txt`
  (time, corrected time, `WE(1).Potential (V)`, `WE(1).Current (A)`, index). Native Gamry `.dta`
  files are also written to a `dta/` subfolder.

The potential is recorded in the **step** files (not the spectra files). These names and the column
layout are relied on by downstream analysis (Rajiv Giridharagopal's
[`OECT_processing`](https://github.com/rajgiriUW/OECT_processing)), so **do not change them without
coordination.**

## Documentation

Detailed documentation is in development. For now:
- See [`docs/data-format.md`](docs/data-format.md) for the output file format
- See `notebooks/` for example workflows
- Check `gamry/` for Gamry sequence templates
- Review source code docstrings for API details

## Contributing

Contributions are welcome! This is a pre-release version and we appreciate:
- Bug reports and feature requests via GitHub Issues
- Pull requests for improvements
- Documentation enhancements
- Additional example notebooks

## Citation

If you use this software in your research, please cite:

```bibtex
@software{waldow2025specechem,
  author       = {Waldow, Dean},
  title        = {spec-echem: Synchronized Spectroelectrochemistry with Avantes and Gamry},
  year         = {2025},
  publisher    = {GitHub},
  version      = {0.1.0},
  doi          = {10.5281/zenodo.17221314},
  url          = {https://github.com/waldowda/spec-echem}
}
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Author

**Dean Waldow**  
Department of Chemistry  
Pacific Lutheran University

## Acknowledgments

- Avantes for spectrometer SDK support
- Gamry Instruments for electrochemical control
- Contributors and early users providing feedback

## Roadmap

### Planned Features (v0.2.0)
- [ ] Eliminate global variables dependency
- [ ] Add automated calibration routines
- [ ] Implement data export to common formats (CSV, HDF5)
- [ ] Move code to a unified Python package using PyQt5 as a windowing environment.

## Support

For questions, issues, or suggestions:
- Open an issue on [GitHub](https://github.com/waldowda/spec-echem/issues)

---

**Note**: This is research software in active development. Features and APIs may change between versions.