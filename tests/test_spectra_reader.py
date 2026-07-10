"""
Tests for reloading a saved run for review (Results tab "Load Run…"):
discover_run_segments() and read_spectra_absorbance() in spec_echem.data.

The round-trip test is the strongest guarantee — write a spectra file with
write_spectra_file(), read it back, and confirm the absorbance matrix and the
time axis are recovered exactly. The golden tests confirm the same on a real
(trimmed) output file.
"""
import os

import numpy as np
import pandas as pd

from spec_echem.data import (
    discover_run_segments, read_spectra_absorbance, write_spectra_file,
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
)

GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "20250715_P3HT9505_KPF6")


def test_discover_run_segments_labels_types_and_order():
    segs = discover_run_segments(GOLDEN)
    # (label, data_type, run_number) in run order: CV, pre-dedope, doping, dedoping
    got = [(label, dt, rn) for label, dt, rn, _ in segs]
    assert got == [
        ("CV", DATA_TYPE_CV, 0),
        ("Pre-dedoping 0", DATA_TYPE_PREDEDOPING, 0),
        ("Doping 7", DATA_TYPE_DOPING, 7),
        ("Dedoping 7", DATA_TYPE_DEDOPING, 7),
    ]


def test_discover_ignores_non_spectra_files(tmp_path):
    (tmp_path / "CVspectra.txt").write_text("Wavelength (nm)\tAbsorbance\n400\t0.1\n")
    (tmp_path / "CV.txt").write_text("ignored\n")          # echem, not spectra
    (tmp_path / "notes.txt").write_text("ignored\n")
    labels = [label for label, *_ in discover_run_segments(tmp_path)]
    assert labels == ["CV"]


def test_read_spectra_absorbance_golden_shape():
    df = read_spectra_absorbance(os.path.join(GOLDEN, "CVspectra.txt"))
    # 1265-pixel Avantes axis; trimmed fixture keeps one full time block
    assert df.shape[0] == 1265
    assert df.shape[1] >= 1
    assert float(df.columns[0]) == 0.0          # corrected time starts at 0
    assert np.isfinite(df.to_numpy()).all()


def test_write_then_read_round_trips(tmp_path):
    wavelengths = np.array([400.0, 450.0, 500.0, 550.0, 600.0])
    timestamps = [10.0, 10.1, 10.2]             # 3 time points; t0 offset removed on read
    n_wl, n_t = len(wavelengths), len(timestamps)
    absorb = np.arange(n_wl * n_t, dtype=float).reshape(n_wl, n_t) * 0.01
    absorb7 = pd.DataFrame(absorb)
    spectra = [np.full(n_wl, 100.0 + i) for i in range(n_t)]
    dark = np.zeros(n_wl)
    ref = np.full(n_wl, 200.0)

    path = write_spectra_file(absorb7, spectra, dark, ref, wavelengths, timestamps,
                              DATA_TYPE_DOPING, 0, str(tmp_path), "run")
    got = read_spectra_absorbance(path)

    assert got.shape == (n_wl, n_t)
    np.testing.assert_allclose(got.index.to_numpy(), wavelengths)
    np.testing.assert_allclose([float(c) for c in got.columns],
                               [t - timestamps[0] for t in timestamps])
    np.testing.assert_allclose(got.to_numpy(), absorb)
