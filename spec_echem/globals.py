"""Global variables for Avantes SDK.

LEGACY: kept only so the original control notebook
(``notebooks/SpecEchem Avantes 0.996-20250717.ipynb``) runs unmodified for
validation. The current modular code (``spectrometer.py``, ``acquisition.py``)
does NOT use this module — it keeps device state on the instance instead.
"""
dev_handle = None
pixels = None
wavelength = None
spectraldata = None
stopscanning = False
