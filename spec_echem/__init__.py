"""
Spectroelectrochemistry Package using Avantes Spectrometers
"""
try:
    from .spectrometer import AvantesSpectrometer
except ImportError:
    pass

from .acquisition import acquire_segment
from .potentiostat import (
    Potentiostat, ExternalPotentiostat, ToolkitPotentiostat, TOOLKITPY_AVAILABLE,
)
from .settings import load_settings, save_settings, DEFAULT_SETTINGS
from .gamry_data import read_cv, read_chrono
from .data import (
    compute_absorbance,
    write_spectra_file,
    write_run_metadata,
    DATA_TYPE_CV,
    DATA_TYPE_DOPING,
    DATA_TYPE_DEDOPING,
    DATA_TYPE_PREDEDOPING,
)

__version__ = "0.1.0"
__author__ = "Dean Waldow"

__all__ = [
    "AvantesSpectrometer",
    "acquire_segment",
    "Potentiostat",
    "ExternalPotentiostat",
    "ToolkitPotentiostat",
    "TOOLKITPY_AVAILABLE",
    "compute_absorbance",
    "write_spectra_file",
    "write_run_metadata",
    "read_cv",
    "read_chrono",
    "DATA_TYPE_CV",
    "DATA_TYPE_DOPING",
    "DATA_TYPE_DEDOPING",
    "DATA_TYPE_PREDEDOPING",
]
