"""
Spectroelectrochemistry Package using Avantes Spectrometers
"""
try:
    from .spectrometer import AvantesSpectrometer
except ImportError:
    pass

from .build_info import __version__, build_id
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

__author__ = "Dean Waldow"

__all__ = [
    "__version__",
    "build_id",
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
