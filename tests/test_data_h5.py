"""HDF5 segment writer — the schema settled 2026-09-29.

Deliberately NOT built from a real run folder: a committed .h5 would carry
sample_name and electrolyte in its attributes, where — unlike a folder name —
nobody would ever see them. Everything here is synthesised.
"""
import json

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from spec_echem.data import (                                        # noqa: E402
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, EchemData,
    H5_SCHEMA_VERSION, compute_absorbance, counts_dtype, h5_path,
    write_segment_h5,
)

N_WL, N_T, N_ECHEM = 6, 4, 9


def _inputs(scale=1.0, integral=True):
    wl = np.linspace(400.0, 900.0, N_WL)
    dark = np.full(N_WL, 100.0)
    ref = np.full(N_WL, 5000.0)
    base = np.linspace(500.0, 4000.0, N_WL) * scale
    step = 0.0 if integral else 0.5          # averaged counts land on a half count
    spectra = [np.rint(base + i * 10) + step for i in range(N_T)]
    timestamps = [1234.5 + 0.1 * i for i in range(N_T)]   # device clock, not zero-based
    absorb = compute_absorbance(spectra, dark, ref, wl, timestamps)
    return absorb, spectra, dark, ref, wl, timestamps


def _echem():
    t = np.linspace(0.0, 0.8, N_ECHEM)
    return EchemData(time=t,
                     potential=np.full(N_ECHEM, 0.7) + np.linspace(0, 0.002, N_ECHEM),
                     current=np.linspace(2e-5, 5e-6, N_ECHEM))


def _write(tmp_path, data_type=DATA_TYPE_DOPING, run_number=0, echem=None,
           settings=None, segment=None, **kw):
    absorb, spectra, dark, ref, wl, stamps = _inputs(**kw)
    return write_segment_h5(absorb, spectra, dark, ref, wl, stamps, echem,
                            data_type, run_number, tmp_path, "20260929_run",
                            segment=segment, settings=settings)


def test_one_file_per_segment_type_named_for_the_run(tmp_path):
    _write(tmp_path, DATA_TYPE_DOPING)
    _write(tmp_path, DATA_TYPE_DEDOPING)
    folder = tmp_path / "20260929_run"
    names = sorted(p.name for p in folder.glob("*.h5"))
    assert names == ["20260929_run_dedoping.h5", "20260929_run_doping.h5"]
    assert h5_path(folder, DATA_TYPE_DOPING) == folder / "20260929_run_doping.h5"


def test_cycles_are_keyed_by_number_never_by_potential(tmp_path):
    """A potential repeats -- every dedoping step shares one -- and a CA scheme need
    not be an ordered ladder. Potential is an attribute of a cycle, never its address."""
    for run in range(3):
        _write(tmp_path, DATA_TYPE_DEDOPING, run_number=run, echem=_echem())
    with h5py.File(h5_path(tmp_path / "20260929_run", DATA_TYPE_DEDOPING)) as f:
        assert sorted(k for k in f if k != "wavelength") == ["0", "1", "2"]


def test_the_schema_names_and_shapes(tmp_path):
    seg = type("S", (), {"label": "Doping 0", "num_points": N_T,
                         "delta_time": 0.1, "trigger": True})()
    path = _write(tmp_path, echem=_echem(), segment=seg,
                  settings={"doping_potential_start": 0.7,
                            "doping_potential_step": 0.1})
    with h5py.File(path) as f:
        assert f.attrs["schema_version"] == H5_SCHEMA_VERSION
        assert f.attrs["data_type_name"] == "Doping"
        assert f["wavelength"].shape == (N_WL,)
        assert f["wavelength"].attrs["units"] == "nm"

        g = f["0"]
        assert g["counts_vs_time"].shape == (N_WL, N_T)
        assert g["absorbance_vs_time"].shape == (N_WL, N_T)
        assert g["absorbance_vs_time"].dtype == np.float32
        assert g["counts_vs_time"].attrs["dims"] == "wavelength x time"
        assert g["time"].shape == (N_T,) and g["time_spectrometer"].shape == (N_T,)
        assert g["dark"].shape == (N_WL,) and g["reference"].shape == (N_WL,)

        # potential_set, not a bare `potential`: the bare name belongs to the
        # MEASURED trace and must mean one thing in one file.
        assert g.attrs["potential_set"] == pytest.approx(0.7)
        assert g.attrs["potential_measured"] == pytest.approx(0.701, abs=1e-3)
        assert "potential" not in g.attrs
        assert g.attrs["label"] == "Doping 0"

        e = g["echem"]
        assert sorted(e) == ["current", "potential", "time"]
        assert e["potential"].shape == (N_ECHEM,)
        assert e["current"].attrs["units"] == "A"
        assert "charge" not in e            # derived; the converter computes it


def test_the_two_time_axes_mean_different_things(tmp_path):
    """`time` is the analysis axis (t=0 at the step). `time_spectrometer` is the
    device's own clock, unrebased -- the only record of how segments relate in time,
    and information the ascii currently throws away."""
    path = _write(tmp_path)
    with h5py.File(path) as f:
        g = f["0"]
        assert g["time"][0] == 0.0
        assert g["time_spectrometer"][0] == pytest.approx(1234.5)
        np.testing.assert_allclose(
            g["time"][:], g["time_spectrometer"][:] - 1234.5, atol=1e-9)
        assert "own clock" in g["time_spectrometer"].attrs["description"]


@pytest.mark.parametrize("integral,expected", [(True, np.uint16), (False, np.float32)])
def test_the_counts_dtype_rule_is_load_bearing(tmp_path, integral, expected):
    """The ADC is 16-bit so a SINGLE scan is integral -- but scan_averages defaults to
    200, and MEASURED 2026-09-29 real counts carry a fractional part of up to exactly
    0.5. A uint16 cast would misstate every half-count."""
    path = _write(tmp_path, integral=integral)
    with h5py.File(path) as f:
        ds = f["0"]["counts_vs_time"]
        assert ds.dtype == expected
        stored = ds[:]
    _, spectra, *_ = _inputs(integral=integral)
    np.testing.assert_allclose(stored, np.asarray(spectra, dtype=float).T, rtol=0, atol=0)


def test_counts_dtype_helper_directly():
    assert counts_dtype([[1.0, 2.0], [3.0, 4.0]]) is np.uint16
    assert counts_dtype([[1.5, 2.0], [3.0, 4.0]]) is np.float32
    assert counts_dtype([[0.0, 70000.0]]) is np.float32      # out of uint16 range
    assert counts_dtype([[-1.0, 2.0]]) is np.float32         # negative
    assert counts_dtype([[np.nan, 2.0]]) is np.uint16        # NaN ignored, rest integral


def test_metadata_json_is_copied_verbatim_not_rebuilt(tmp_path):
    """The anti-divergence measure: one source, copied, so the two cannot drift."""
    folder = tmp_path / "20260929_run"
    folder.mkdir(parents=True)
    meta = {"run_started": "2026-09-29T11:00:00", "sample_name": "µ-test",
            "electrolyte": "0.1 M", "notes": "n", "settings": {"a": 1},
            "instruments": {"spectrometer": "Avantes 123"}}
    (folder / "20260929_run_metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    path = _write(tmp_path)
    with h5py.File(path) as f:
        assert json.loads(f.attrs["metadata_json"]) == meta
        assert f.attrs["sample_name"] == "µ-test"      # non-ASCII survives
        assert f.attrs["spectrometer"] == "Avantes 123"


def test_a_run_with_no_metadata_still_writes(tmp_path):
    path = _write(tmp_path)
    with h5py.File(path) as f:
        assert "metadata_json" not in f.attrs
        assert f.attrs["schema_version"] == H5_SCHEMA_VERSION


def test_external_mode_has_no_echem_group(tmp_path):
    path = _write(tmp_path, echem=None)
    with h5py.File(path) as f:
        assert "echem" not in f["0"]
        assert "potential_measured" not in f["0"].attrs


def test_a_cv_has_no_set_potential(tmp_path):
    path = _write(tmp_path, DATA_TYPE_CV, settings={"cv_limit1_v": -0.5})
    with h5py.File(path) as f:
        assert f.attrs["data_type_name"] == "CV"
        assert "potential_set" not in f["0"].attrs


def test_rewriting_a_cycle_replaces_it(tmp_path):
    _write(tmp_path, run_number=1, scale=1.0)
    path = _write(tmp_path, run_number=1, scale=2.0)
    with h5py.File(path) as f:
        assert f["1"]["counts_vs_time"][:].max() > 5000     # the second write won


def test_nan_and_inf_survive(tmp_path):
    """The 2026-09-04 shutter-closed run produced 8982 NaN and 513 inf, and those are
    CORRECT outputs of compute_absorbance -- not errors to be cleaned away."""
    wl = np.linspace(400.0, 900.0, N_WL)
    dark = np.full(N_WL, 100.0)
    ref = np.full(N_WL, 100.0)                  # ref == dark -> 0/0 -> NaN, log10(0) -> inf
    spectra = [np.full(N_WL, 100.0), np.full(N_WL, 50.0)]
    stamps = [0.0, 0.1]
    import warnings
    with warnings.catch_warnings():       # the divide/log10 warnings ARE the point
        warnings.simplefilter("ignore")
        absorb = compute_absorbance(spectra, dark, ref, wl, stamps)
    assert np.isnan(absorb.values).any() or np.isinf(absorb.values).any()

    path = write_segment_h5(absorb, spectra, dark, ref, wl, stamps, None,
                            DATA_TYPE_DOPING, 0, tmp_path, "20260929_run")
    with h5py.File(path) as f:
        stored = f["0"]["absorbance_vs_time"][:]
    np.testing.assert_array_equal(np.isnan(stored), np.isnan(absorb.values))
    np.testing.assert_array_equal(np.isinf(stored), np.isinf(absorb.values))


def test_compression_is_off_by_default_and_optional(tmp_path):
    """MEASURED 2026-09-29: gzip buys 21%, not the 2-4x first estimated, for ~120 ms
    a segment -- so it is an archiving option, not the acquisition default."""
    plain = _write(tmp_path)
    with h5py.File(plain) as f:
        assert f["0"]["counts_vs_time"].compression is None

    absorb, spectra, dark, ref, wl, stamps = _inputs()
    gz = write_segment_h5(absorb, spectra, dark, ref, wl, stamps, None,
                          DATA_TYPE_DEDOPING, 0, tmp_path, "20260929_run",
                          compression=4)
    with h5py.File(gz) as f:
        assert f["0"]["counts_vs_time"].compression == "gzip"


def test_the_writer_is_a_no_op_without_h5py(tmp_path, monkeypatch):
    """SpecEchem32 may not have h5py at all -- the ascii path must be untouched."""
    import spec_echem.data as d
    monkeypatch.setattr(d, "H5PY_AVAILABLE", False)
    assert _write(tmp_path) is None
    assert not list(tmp_path.glob("**/*.h5"))


def test_a_missing_h5py_says_so_once(monkeypatch, tmp_path):
    """Silence is the failure this project keeps paying for: on 2026-09-29 a run
    wrote four ascii files and no .h5, with nothing in any log, because the GUI's
    environment had no h5py."""
    import logging

    import spec_echem.data as d
    monkeypatch.setattr(d, "H5PY_AVAILABLE", False)
    monkeypatch.setattr(d, "H5PY_IMPORT_ERROR", "No module named 'h5py'")
    monkeypatch.setattr(d, "_h5py_warning_said", False)

    records = []
    logger = logging.getLogger("spec_echem.run")
    h = type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r)})()
    logger.addHandler(h)
    try:
        assert _write(tmp_path) is None
        assert _write(tmp_path, run_number=1) is None     # second call stays quiet
    finally:
        logger.removeHandler(h)

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, "said once per process, not once per segment"
    msg = warnings[0].getMessage()
    assert "h5py is not importable" in msg and "ascii files are unaffected" in msg


# --- the ascii -> H5 backfill (examples/ascii_to_h5.py) ---

def _ascii_run(tmp_path, with_echem=True):
    """A real run folder, written by the real ascii writers."""
    from spec_echem.data import write_spectra_file, write_echem_file
    absorb, spectra, dark, ref, wl, stamps = _inputs()
    write_spectra_file(absorb, spectra, dark, ref, wl, stamps,
                       DATA_TYPE_DOPING, 0, tmp_path, "20260929_run")
    if with_echem:
        write_echem_file(_echem(), DATA_TYPE_DOPING, 0, tmp_path, "20260929_run")
    return tmp_path / "20260929_run", absorb


def test_backfill_rebuilds_a_run_from_its_ascii(tmp_path):
    """Every run already on disk stays useful when the H5 becomes primary."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ascii_to_h5", "examples/ascii_to_h5.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    folder, absorb = _ascii_run(tmp_path)
    assert mod.convert_run(str(folder)) == 1

    with h5py.File(folder / "20260929_run_doping.h5") as f:
        g = f["0"]
        np.testing.assert_allclose(g["absorbance_vs_time"][:],
                                   absorb.values.astype(np.float32), rtol=1e-6)
        assert g["echem"]["current"].shape == (N_ECHEM,)
        assert f.attrs["backfilled_from_ascii"]

        # The counts, dark and reference the ascii kept are all recovered.
        assert g["counts_vs_time"].shape == absorb.shape
        assert g["dark"].shape == (N_WL,) and g["reference"].shape == (N_WL,)

        # ...but the spectrometer clock is ABSENT, not faked. write_spectra_file
        # writes 'Time (s)' and 'Corrected time (s)' identically, so it never
        # survived. Absent is checkable; a copy of `time` under that name is not.
        assert "time_spectrometer" not in g
        assert f.attrs["time_spectrometer_recovered"] is np.False_ or \
               f.attrs["time_spectrometer_recovered"] == False        # noqa: E712
        assert g["time"][0] == 0.0
