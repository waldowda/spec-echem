# spec-echem

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.14758064.svg)](https://doi.org/10.5281/zenodo.14758064)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

A Python library for synchronized spectroelectrochemistry experiments using Avantes spectrometers and Gamry potentiostats. Currently, the approach is to use Python as a Jupyter notebook to acquire spectra with the Avantes spectrometer and be set to wait from a trigger from the Gamry potentiostat. The Gamry is initially running using the sequence wizard but in a future release it will also be run using Python in a PyQt5 windowing environment. Additional information will be provided regarding the trigger wiring as well.

## ⚠️ Pre-Release Notice

This software is currently in **pre-release** (v0.1.0). The API and functionality are subject to change. Use in production environments at your own risk.

## Overview

`spec-echem` provides tools for performing synchronized spectroscopic and electrochemical measurements, enabling real-time optical monitoring during electrochemical experiments. The package coordinates:

- **Avantes spectrometer** control for UV-Vis spectroscopy (~380-1100 nm)
- **Gamry potentiostat** triggering for synchronized data acquisition
- GPIO-based hardware triggering for precise temporal correlation
- Automated data collection and storage

## Features

- 🔬 Real-time spectrum acquisition triggered by electrochemical events
- ⚡ Hardware triggering via GPIO pins for microsecond synchronization
- 📊 Integrated data processing for both spectroscopic and electrochemical data
- 🔄 Support for complex electrochemical sequences (CV, chronoamperometry, stepping protocols)
- 💾 Synchronized data storage with timestamps
- 📈 Built-in visualization tools for spectroelectrochemical data

## System Requirements

### Hardware
- Avantes spectrometer (tested with models supporting 380-1100 nm range)
- Gamry potentiostat with digital output capabilities
- GPIO-capable system (e.g., Raspberry Pi) for trigger detection
- USB connections for instruments

### Software Prerequisites

1. **Avantes SDK and Python bindings**
   - Contact Avantes to obtain the SDK for your operating system
   - Install the `avaspec` Python module (provided with SDK)
   - Configure USB drivers for your spectrometer

2. **Python Dependencies**
   ```bash
   numpy>=1.19.0
   matplotlib>=3.3.0
   pandas>=1.3.0
   scipy>=1.5.0
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

```bash
# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
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
│   └── spec_echem_exp_0716dw_CHI.ipynb  # Example experimental workflow
├── gamry/                   # Gamry sequence files
│   └── Spec_Echem_20250714test.GSequence  # Example sequence with triggers
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

See the Jupyter notebook `notebooks/spec_echem_exp_0716dw_CHI.ipynb` for a complete example of:
- Setting up GPIO triggers
- Coordinating with Gamry sequences
- Real-time data acquisition
- Data processing and visualization

## Workflow

1. **Configure Gamry Sequence**: Load the provided `.GSequence` file or create your own with digital output triggers
2. **Initialize Spectrometer**: Set integration time and averaging parameters
3. **Setup Trigger Detection**: Configure GPIO pins to detect Gamry digital outputs
4. **Run Experiment**: Start Gamry sequence and collect triggered spectra
5. **Process Data**: Analyze synchronized electrochemical and spectroscopic data

## Documentation

Detailed documentation is in development. For now:
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
  doi          = {10.5281/zenodo.14758064},
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
- [ ] Create GUI for experiment control
- [ ] Add support for additional spectrometer models
- [ ] Expand electrochemistry integration beyond Gamry

### Future Developments
- [ ] Real-time fitting of spectroscopic features
- [ ] Machine learning integration for spectral analysis
- [ ] Cloud data storage and sharing capabilities
- [ ] Docker container for easy deployment

## Support

For questions, issues, or suggestions:
- Open an issue on [GitHub](https://github.com/waldowda/spec-echem/issues)
- Contact the author directly (see university directory)

---

**Note**: This is research software in active development. Features and APIs may change between versions.