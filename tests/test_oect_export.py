"""Export to the downstream OECT_processing layout.

The real test is the last one: round-tripping through THEIR actual convert_h5().
Everything above it pins the four constraints read off their code, so a failure
says which rule broke rather than just "their reader raised".
"""
import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from spec_echem.data import (                                        # noqa: E402
    DATA_TYPE_DEDOPING, DATA_TYPE_DOPING, EchemData, compute_absorbance,
    write_segment_h5,
)
from spec_echem.oect_export import (                                 # noqa: E402
    OECT_FILENAMES, export_oect_h5, export_run, read_cycles,
)

N_WL, N_T, N_ECHEM = 5, 4, 7
DOPING_V = [0.3, 0.5, 0.7]
DEDOPE_V = -0.5                      # every dedoping step shares ONE potential


def _absorb(scale=1.0):
    wl = np.linspace(400.0, 900.0, N_WL)
    dark, ref = np.full(N_WL, 100.0), np.full(N_WL, 5000.0)
    spectra = [np.linspace(500.0, 4000.0, N_WL) * scale + i for i in range(N_T)]
    stamps = [0.0, 0.1, 0.2, 0.3]
    return compute_absorbance(spectra, dark, ref, wl, stamps), spectra, dark, ref, wl, stamps


def _echem_at(volts):
    t = np.linspace(0.0, 0.6, N_ECHEM)
    return EchemData(time=t, potential=np.full(N_ECHEM, volts),
                     current=np.linspace(2e-5, 5e-6, N_ECHEM))


def _run(tmp_path, dedoping=True):
    """A ladder: three doping steps at 0.3/0.5/0.7 V, three dedopes all at -0.5 V."""
    for cycle, volts in enumerate(DOPING_V):
        absorb, spectra, dark, ref, wl, stamps = _absorb(1.0 + cycle)
        write_segment_h5(absorb, spectra, dark, ref, wl, stamps, _echem_at(volts),
                         DATA_TYPE_DOPING, cycle, tmp_path, "20260929_run")
        if dedoping:
            write_segment_h5(absorb, spectra, dark, ref, wl, stamps,
                             _echem_at(DEDOPE_V), DATA_TYPE_DEDOPING, cycle,
                             tmp_path, "20260929_run")
    return tmp_path / "20260929_run"


def test_the_doping_export_is_keyed_by_potential(tmp_path):
    out = export_oect_h5(_run(tmp_path), DATA_TYPE_DOPING)
    assert out.name == OECT_FILENAMES[DATA_TYPE_DOPING]
    assert out.parent.name == "oect"          # the subfolder marks it as derived
    with h5py.File(out) as f:
        assert sorted(k for k in f if k not in ("current", "charge", "potentials")) \
            == ["0.3", "0.5", "0.7"]
        np.testing.assert_allclose(f["potentials"][:], DOPING_V)


def test_the_dedoping_export_is_keyed_by_the_DOPING_potentials(tmp_path):
    """Rule 2, and the one a naive converter gets wrong. Their notebook passes the
    same `volts` to both UVVis objects, so /0.7/ in dedopingdata.h5 means "the dedope
    that FOLLOWED the 0.7 V dope". Keying by the actual -0.5 V would put all three
    cycles on ONE key and time_dep_spectra would overwrite silently."""
    written = export_run(_run(tmp_path))
    dedope = next(p for p in written if p.name == OECT_FILENAMES[DATA_TYPE_DEDOPING])
    with h5py.File(dedope) as f:
        keys = sorted(k for k in f if k not in ("current", "charge", "potentials"))
        assert keys == ["0.3", "0.5", "0.7"]          # NOT ["-0.5"]
        np.testing.assert_allclose(f["potentials"][:], DOPING_V)


def test_nothing_extra_sits_at_the_top_level(tmp_path):
    """Rule 1: convert_h5 calls float() on every top-level key that is not
    current/charge/potentials, so an extra group or dataset there breaks it."""
    out = export_oect_h5(_run(tmp_path), DATA_TYPE_DOPING)
    with h5py.File(out) as f:
        for key in f:
            if key in ("current", "charge", "potentials"):
                continue
            float(key)                    # must not raise


def test_current_and_charge_are_always_written(tmp_path):
    """Rule 4: save_h5 makes them optional, convert_h5 reads them unguarded."""
    out = export_oect_h5(_run(tmp_path), DATA_TYPE_DOPING)
    with h5py.File(out) as f:
        assert sorted(f["current"]) == ["columns", "data", "index"]
        assert f["charge"].shape == (len(DOPING_V),)
        # charge is DERIVED here, not stored in the archive: trapezoid over current.
        assert np.all(f["charge"][:] > 0)


def test_the_note_says_it_is_not_the_record(tmp_path):
    out = export_oect_h5(_run(tmp_path), DATA_TYPE_DOPING)
    with h5py.File(out) as f:
        assert "DERIVED VIEW" in f.attrs["note"]
        assert f.attrs["source_file"] == "20260929_run_doping.h5"


def test_the_potential_key_is_derived_their_way(tmp_path):
    """Rule 3: the FIRST sample rounded to 2 dp, not our median. Their notebook
    looks up spectra_vs_time[0.7], so the key must match their arithmetic."""
    folder = tmp_path / "20260929_run"
    absorb, spectra, dark, ref, wl, stamps = _absorb()
    drifting = EchemData(time=np.linspace(0, 0.6, N_ECHEM),
                         potential=np.linspace(0.700, 0.760, N_ECHEM),  # median 0.73
                         current=np.linspace(2e-5, 5e-6, N_ECHEM))
    write_segment_h5(absorb, spectra, dark, ref, wl, stamps, drifting,
                     DATA_TYPE_DOPING, 0, tmp_path, "20260929_run")
    cycles = read_cycles(folder / "20260929_run_doping.h5")
    assert cycles[0][1] == pytest.approx(0.700)        # first sample, not 0.73
    with h5py.File(export_oect_h5(folder, DATA_TYPE_DOPING)) as f:
        assert "0.7" in f


def test_a_segment_with_no_potential_is_refused_clearly(tmp_path):
    """The layout addresses groups BY potential, so a segment without one cannot be
    exported. Say WHY in terms of the run rather than the schema: this is normal for
    External mode, where the sequence file sets the potentials and the software never
    sees them."""
    absorb, spectra, dark, ref, wl, stamps = _absorb()
    write_segment_h5(absorb, spectra, dark, ref, wl, stamps, None,
                     DATA_TYPE_DOPING, 0, tmp_path, "20260929_run")
    with pytest.raises(ValueError, match="no potential recorded") as exc:
        export_oect_h5(tmp_path / "20260929_run", DATA_TYPE_DOPING)
    assert "External mode" in str(exc.value)


def test_their_own_reader_opens_what_we_write(tmp_path):
    """The one that matters. Everything above pins a rule read off their code; this
    runs their ACTUAL convert_h5 against our output."""
    import sys
    clone = None
    for candidate in (
            "/Users/waldow/dev/SpectroElectroChem/OECT_processing",
            str((__import__("pathlib").Path(__file__).resolve().parents[2]
                 / "OECT_processing"))):
        if (__import__("pathlib").Path(candidate) / "oect_processing").is_dir():
            clone = candidate
            break
    if clone is None:
        pytest.skip("no local OECT_processing clone to read the file back with")
    if clone not in sys.path:
        sys.path.insert(0, clone)
    convert_h5 = pytest.importorskip(
        "oect_processing.specechem.uvvis_h5").convert_h5

    out = export_oect_h5(_run(tmp_path), DATA_TYPE_DOPING)
    data = convert_h5(str(out))

    assert sorted(data.spectra_vs_time) == DOPING_V
    frame = data.spectra_vs_time[0.7]
    assert frame.shape == (N_WL, N_T)
    assert frame.index.name == "Wavelength (nm)"
    assert frame.columns.name == "Time (s)"
    assert data.current.shape[1] == len(DOPING_V)
    assert len(data.charge.columns) == len(DOPING_V) or len(data.charge) == len(DOPING_V)


def test_potentials_and_group_keys_agree(tmp_path):
    """CAUGHT 2026-09-29 against their own file for the same run: /potentials held
    our raw first samples ([0.199703, ...]) while the group keys were rounded
    ('0.2'), so their volt() -> spectra_vs_time[...] lookups would all miss. Both
    carry the rounded value, as read_files.py does."""
    folder = tmp_path / "20260929_run"
    for cycle, volts in enumerate((0.199703, 0.299442, 0.698698)):
        absorb, spectra, dark, ref, wl, stamps = _absorb(1.0 + cycle)
        write_segment_h5(absorb, spectra, dark, ref, wl, stamps, _echem_at(volts),
                         DATA_TYPE_DOPING, cycle, tmp_path, "20260929_run")

    with h5py.File(export_oect_h5(folder, DATA_TYPE_DOPING)) as f:
        keys = sorted((k for k in f if k not in ("current", "charge", "potentials")),
                      key=float)
        np.testing.assert_allclose(f["potentials"][:], [0.2, 0.3, 0.7])
        assert keys == ["0.2", "0.3", "0.7"]
        # every value in /potentials addresses a real group
        for p in f["potentials"][:]:
            assert str(p) in f
        np.testing.assert_allclose(f["current"]["columns"][:], [0.2, 0.3, 0.7])
