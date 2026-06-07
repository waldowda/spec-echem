"""
Spectroelectrochemistry Package using Avantes Spectrometers
"""
try:
    from .spectrometer import AvantesSpectrometer
except ImportError:
    pass

__version__ = "0.1.0"
__author__ = "Dean Waldow"

__all__ = ["AvantesSpectrometer"]
