"""
Tests for spec_echem.experiment — segment list construction and single-segment
runs through the fake spectrometer. No Qt, no hardware.
"""
import numpy as np
import pytest

from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.experiment import build_segments, n_doping_cycles, run_one_segment, Segment
from spec_echem.data import (
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
)
from spec_echem.fakes import FakeSpectrometer


def settings(**overrides):
    s = DEFAULT_SETTINGS.copy()
    s.update(overrides)
    return s


# --- n_doping_cycles ---

def test_doping_cycle_count_default():
    # start 0.2, end 0.8, step 0.1 -> 7 potentials
    assert n_doping_cycles(DEFAULT_SETTINGS) == 7


def test_doping_cycle_count_single():
    assert n_doping_cycles(settings(doping_potential_start=0.5,
                                    doping_potential_end=0.5,
                                    doping_potential_step=0.1)) == 1


def test_doping_cycle_count_zero_step():
    assert n_doping_cycles(settings(doping_potential_step=0.0)) == 1


# --- build_segments ---

def test_full_sequence_order_and_count():
    s = settings(doping_potential_start=0.2, doping_potential_end=0.4,
                 doping_potential_step=0.1)  # 3 doping cycles
    segs = build_segments(s)
    labels = [seg.label for seg in segs]
    assert labels == [
        "CV", "Pre-dedoping",
        "Doping 0", "Dedoping 0",
        "Doping 1", "Dedoping 1",
        "Doping 2", "Dedoping 2",
    ]


def test_segment_data_types():
    s = settings(doping_potential_start=0.2, doping_potential_end=0.2,
                 doping_potential_step=0.1)  # 1 cycle
    segs = build_segments(s)
    by_label = {seg.label: seg for seg in segs}
    assert by_label["CV"].data_type == DATA_TYPE_CV
    assert by_label["Pre-dedoping"].data_type == DATA_TYPE_PREDEDOPING
    assert by_label["Doping 0"].data_type == DATA_TYPE_DOPING
    assert by_label["Dedoping 0"].data_type == DATA_TYPE_DEDOPING


def test_disabled_steps_omitted():
    s = settings(cv_enabled=False, prededoping_enabled=False, doping_enabled=True,
                 doping_potential_start=0.2, doping_potential_end=0.2, doping_potential_step=0.1)
    labels = [seg.label for seg in build_segments(s)]
    assert labels == ["Doping 0", "Dedoping 0"]


def test_nothing_enabled_is_empty():
    s = settings(cv_enabled=False, prededoping_enabled=False, doping_enabled=False)
    assert build_segments(s) == []


def test_cv_points_and_delta_match_notebook_formula():
    s = settings(cv_initial_v=0.0, cv_limit1_v=-0.5, cv_limit2_v=0.7, cv_final_v=0.0,
                 cv_step_size=10.0, cv_scan_rate=100.0, cv_cycles=3)
    cv = build_segments(s)[0]
    # sweep path = |0-(-0.5)| + |-0.5-0.7| + |0.7-0| = 2.4 V
    # int(2.4 / 10 * 1000 * 3 + 1) = int(721) ; delta = 10/100 = 0.1
    assert cv.num_points == 721
    assert cv.delta_time == pytest.approx(0.1)


def test_chrono_points_formula():
    s = settings(cv_enabled=False, prededoping_enabled=True, doping_enabled=False,
                 chrono_time=30.0, chrono_delta_time=0.1)
    pre = build_segments(s)[0]
    assert pre.num_points == 301  # int(30/0.1 + 1)


# --- run_one_segment with the fake ---

def test_run_one_segment_writes_file(tmp_path):
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()
    seg = Segment("Doping 0", DATA_TYPE_DOPING, 0, num_points=5, delta_time=0.01, trigger=False)

    result = run_one_segment(spec, seg, dark, ref, wl, tmp_path, "20250715_Test")
    assert result is not None
    absorb_df, path = result
    assert absorb_df.shape == (len(wl), 5)
    assert path.name == "spectra(0).txt"
    assert path.exists()


class FakePotentiostat:
    """Minimal potentiostat for run_one_segment: no-op lifecycle, canned echem data."""
    def __init__(self, data=None):
        self._data = data
        self.fired = False
        self.pumps = 0

    def prepare(self, segment):
        self._segment = segment

    def fire(self):
        self.fired = True

    def pump(self):
        self.pumps += 1

    def finish(self, aborted=False):
        pass

    def last_data(self):
        return self._data


def _chrono_acq(n=5):
    dt = np.dtype([('time', 'f8'), ('vf', 'f8'), ('im', 'f8')])
    arr = np.zeros(n, dtype=dt)
    arr['time'] = np.arange(n) * 0.1
    arr['vf'] = 0.2
    arr['im'] = np.linspace(1e-6, 5e-6, n)
    return arr


def test_run_one_segment_writes_echem_next_to_spectra(tmp_path):
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()
    seg = Segment("Doping 0", DATA_TYPE_DOPING, 0, num_points=5, delta_time=0.01, trigger=False)
    pstat = FakePotentiostat(_chrono_acq(5))

    result = run_one_segment(spec, seg, dark, ref, wl, tmp_path, "20250715_Test",
                             potentiostat=pstat)
    assert result is not None
    folder = tmp_path / "20250715_Test"
    assert (folder / "spectra(0).txt").exists()   # optical, as before
    assert (folder / "steps(0).txt").exists()      # echem, written alongside
    assert pstat.fired                             # trigger fired at the armed instant
    assert pstat.pumps == seg.num_points           # curve pumped once per spectrum


def test_run_one_segment_no_potentiostat_writes_no_echem(tmp_path):
    # External mode (potentiostat=None): spectra only, no echem file.
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()
    seg = Segment("Doping 0", DATA_TYPE_DOPING, 0, num_points=5, delta_time=0.01, trigger=False)

    run_one_segment(spec, seg, dark, ref, wl, tmp_path, "20250715_Test")
    folder = tmp_path / "20250715_Test"
    assert (folder / "spectra(0).txt").exists()
    assert not (folder / "steps(0).txt").exists()


def test_run_one_segment_aborts_without_writing(tmp_path):
    import threading
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()
    seg = Segment("Doping 0", DATA_TYPE_DOPING, 0, num_points=5, delta_time=0.01, trigger=False)

    abort = threading.Event()
    abort.set()  # aborted before it starts
    result = run_one_segment(spec, seg, dark, ref, wl, tmp_path, "20250715_Test", abort)
    assert result is None
    assert not (tmp_path / "20250715_Test" / "spectra(0).txt").exists()
