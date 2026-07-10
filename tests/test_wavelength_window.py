"""
Tests for the configurable wavelength window: the FakeSpectrometer honors it,
a windowed acquisition writes fewer rows per block and round-trips, and the
settings carry the (default-None) keys.
"""
import numpy as np

from spec_echem.fakes import FakeSpectrometer, N_POINTS
from spec_echem.data import (
    compute_absorbance, write_spectra_file, read_spectra_absorbance, DATA_TYPE_DOPING,
)
from spec_echem.settings import DEFAULT_SETTINGS, save_settings, load_settings


def test_fake_window_narrows_measure_and_wavelengths():
    spec = FakeSpectrometer()
    spec.init()
    # full window first
    _, wl_full = spec.wavelengths()
    _, data_full = spec.measure()
    assert len(wl_full) == N_POINTS == len(data_full)

    spec.set_wavelength_window(450.0, 950.0)
    _, wl = spec.wavelengths()
    _, data = spec.measure()
    assert len(wl) == len(data) < N_POINTS
    assert wl.min() >= 450.0 and wl.max() <= 950.0

    # None edges reset to full
    spec.set_wavelength_window(None, None)
    _, wl2 = spec.wavelengths()
    assert len(wl2) == N_POINTS


def test_windowed_acquisition_roundtrips(tmp_path):
    spec = FakeSpectrometer()
    spec.init()
    spec.set_wavelength_window(500.0, 900.0)

    _, wavelengths = spec.wavelengths()
    _, dark = spec.measure()
    _, ref = spec.measure()
    spectra = [spec.measure()[1] for _ in range(3)]
    timestamps = [0.0, 0.1, 0.2]

    absorb7 = compute_absorbance(spectra, dark, ref, wavelengths, timestamps)
    path = write_spectra_file(absorb7, spectra, dark, ref, wavelengths, timestamps,
                              DATA_TYPE_DOPING, 0, str(tmp_path), "run")

    got = read_spectra_absorbance(path)
    # fewer wavelength rows than the full detector window, and within the band
    assert got.shape[0] == len(wavelengths) < N_POINTS
    assert got.index.min() >= 500.0 and got.index.max() <= 900.0
    assert got.shape[1] == len(timestamps)


def test_settings_carry_wavelength_window_keys(tmp_path):
    assert DEFAULT_SETTINGS["wavelength_min"] is None
    assert DEFAULT_SETTINGS["wavelength_max"] is None
    s = DEFAULT_SETTINGS.copy()
    s["wavelength_min"], s["wavelength_max"] = 420.0, 1040.0
    p = tmp_path / "s.json"
    save_settings(s, p)
    loaded = load_settings(p)
    assert loaded["wavelength_min"] == 420.0 and loaded["wavelength_max"] == 1040.0
