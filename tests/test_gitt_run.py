"""The GITT end to end: its HDF5, reloading it, Start's checks, the worker, the GUI."""
import json

import numpy as np
import pytest

from spec_echem.data import DATA_TYPE_GITT, discover_run_h5, h5_path, read_segment_h5
from spec_echem.experiment import (build_segments, gitt_live, gitt_start_concerns,
                                   gitt_start_problems, gitt_step_segments,
                                   run_gitt_segment)
from spec_echem.fakes import FakeGittPotentiostat, FakeSpectrometer
from spec_echem.gitt import END_E_LIMIT, END_FIXED
from spec_echem.settings import DEFAULT_SETTINGS

h5py = pytest.importorskip("h5py")


def quick(**over):
    """Real time, so kept to about a second."""
    s = dict(DEFAULT_SETTINGS, gitt_enabled=True, cv_enabled=False,
             prededoping_enabled=False, doping_enabled=False, pitt_enabled=False,
             gitt_current_ua=10.0, gitt_pulse_s=0.15, gitt_n_pulses=2,
             gitt_relax_s=0.2, gitt_initial_rest_s=0.1, gitt_e_min_v=-1.0,
             gitt_e_max_v=1.0, gitt_fast_s=0.1, gitt_slow_interval_s=0.05,
             chrono_delta_time=0.02, potentiostat_mode="autolab")
    s.update(over)
    return s


def _run(tmp_path, s, pot=None):
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]
    seg = build_segments(s)[-1]
    return run_gitt_segment(spec, seg, np.full(wl.size, 100.0),
                            np.full(wl.size, 40000.0), wl, tmp_path, "20261029_gitt",
                            s, pot or FakeGittPotentiostat(r_series=1e3, c=1e-3))


def test_the_gitt_runs_after_everything_as_one_segment():
    s = quick(pitt_enabled=True)
    segs = build_segments(s)
    assert [seg.label for seg in segs][-2:] == ["PITT", "GITT"]
    assert segs[-1].data_type == DATA_TYPE_GITT and segs[-1].num_points == 3
    assert [seg.label for seg in build_segments(quick(gitt_enabled=False))] == []


def test_each_gitt_step_is_registered_by_label():
    assert [seg.label for seg in gitt_step_segments(quick())] == [
        "GITT 0", "GITT 1", "GITT 2"]
    assert gitt_step_segments(quick(gitt_enabled=False)) == []


def test_run_gitt_segment_writes_one_group_per_step(tmp_path):
    steps, path, record = _run(tmp_path, quick())
    assert [label for label, _df in steps] == ["GITT 0", "GITT 1", "GITT 2"]
    assert record.completed
    assert path == h5_path(tmp_path / "20261029_gitt", DATA_TYPE_GITT)
    with h5py.File(path, "r") as f:
        assert f.attrs["data_type_name"] == "GITT"
        assert bool(f.attrs["gitt_completed"])
        table = json.loads(f.attrs["gitt_steps_json"])
        assert [st["role"] for st in table] == ["rest", "titration", "titration"]
        for k in (1, 2):
            g = f[str(k)]
            assert g.attrs["label"] == f"GITT {k}"
            assert g.attrs["current_set_A"] == pytest.approx(10e-6)
            assert g.attrs["pulse_s"] == pytest.approx(0.15)
            assert g.attrs["end_reason"] == END_FIXED
            assert "potential_set" not in g.attrs
            assert "time_potentiostat" in g["echem"]
    assert not any(p.suffix == ".txt" for p in path.parent.iterdir())


def test_the_steps_are_found_and_read_like_segments(tmp_path):
    _steps, path, _record = _run(tmp_path, quick())
    found = discover_run_h5(path.parent)
    assert [f[0] for f in found] == ["GITT 0", "GITT 1", "GITT 2"]
    assert read_segment_h5(path, 1) is not None


def test_a_potential_outside_the_window_is_saved_and_says_why(tmp_path, caplog):
    s = quick(gitt_e_max_v=0.001)            # 10 uA into 1 kOhm is 10 mV: outside
    with caplog.at_level("WARNING"):
        _steps, path, record = _run(tmp_path, s)
    assert not record.completed
    assert record.steps[-1]["end_reason"] == END_E_LIMIT
    assert path is not None and path.exists()
    assert any("GITT STOPPED" in r.message for r in caplog.records)


def test_start_refuses_a_gitt_the_settings_or_the_mode_cannot_run():
    assert gitt_start_problems(quick()) == []
    assert gitt_start_problems(quick(gitt_enabled=False, gitt_current_ua=0.0)) == []
    assert any("must not be 0" in p
               for p in gitt_start_problems(quick(gitt_current_ua=0.0)))
    external = gitt_start_problems(quick(potentiostat_mode="external"))
    assert any("cannot run a GITT" in p for p in external)
    assert gitt_start_problems(quick(potentiostat_mode="python")) == []


def test_start_refuses_without_h5py_because_nothing_would_be_saved(monkeypatch):
    import spec_echem.data as data
    monkeypatch.setattr(data, "H5PY_AVAILABLE", False)
    assert any("h5py" in p for p in gitt_start_problems(quick()))


def test_a_pulse_beyond_every_autolab_range_is_refused():
    s = quick(gitt_current_ua=5e9, gitt_max_charge_mc=1e9)        # 5 kA
    assert any("No Autolab current range" in p for p in gitt_start_problems(s))


def test_the_autolab_offset_is_a_concern_not_a_refusal():
    s = quick(gitt_current_ua=10.0, gitt_pulse_s=1.0, gitt_relax_s=200.0)
    concerns = gitt_start_concerns(s)
    assert concerns and "0 A setpoint" in concerns[0]
    assert gitt_start_problems(s) == []
    assert gitt_start_concerns(quick()) == []
    assert gitt_start_concerns(quick(potentiostat_mode="python",
                                     gitt_relax_s=200.0)) == []


def test_the_live_readout_shows_the_running_steps_potential(tmp_path):
    _steps, _path, record = _run(tmp_path, quick())
    t, v, status = gitt_live(record, 3)
    assert status.startswith("Step 3 of 3")
    assert "E = " in status and len(t) == len(v) > 0
    assert gitt_live(None, 3) is None


def test_the_worker_emits_each_step_then_finishes(tmp_path):
    pytest.importorskip("qtpy")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from gui.workers import AcquisitionWorker

    s = quick()
    spec = FakeSpectrometer()
    wl = spec.wavelengths()[1]

    class Pot(FakeGittPotentiostat):
        python_paced = True
        settings = s
        def open(self): pass
        def close(self): pass
        def stop(self): pass
        def device_lost(self): return False

    w = AcquisitionWorker(spec, build_segments(s), np.full(wl.size, 100.0),
                          np.full(wl.size, 40000.0), wl, tmp_path, "20261029_gitt",
                          potentiostat=Pot(r_series=1e3, c=1e-3), settings=s)
    done, finished = [], []
    w.segment_done.connect(lambda label, df: done.append(label))
    w.finished.connect(finished.append)
    w.gui_idle.set()
    w.run()
    assert done == ["GITT 0", "GITT 1", "GITT 2"]
    assert finished == ["done"]


# --- the Parameters tab ------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("qtpy")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def params_tab(app):
    # Isolated from this machine's config/bench.ini, as test_gui_layout's window is.
    from unittest.mock import patch
    import gui.main_window as _mw
    with patch.object(_mw, "load_bench_defaults", lambda *a, **k: ({}, [])):
        win = _mw.MainWindow()
    yield win.parameters_tab
    win.close()


def test_the_gitt_group_round_trips_its_settings(params_tab):
    s = dict(DEFAULT_SETTINGS, gitt_enabled=True, gitt_current_ua=-2.5,
             gitt_n_pulses=7, gitt_e_max_v=0.65)
    params_tab.populate_from(s)
    out = {}
    params_tab.collect_into(out)
    assert out["gitt_enabled"] is True
    assert out["gitt_current_ua"] == pytest.approx(-2.5)
    assert out["gitt_n_pulses"] == 7 and out["gitt_e_max_v"] == pytest.approx(0.65)


def test_the_estimate_gives_steps_time_and_charge_per_film_volume(params_tab):
    params_tab.populate_from(dict(DEFAULT_SETTINGS))
    text = params_tab.gitt_estimate.text()
    # 1 uA x 10 s x 20 = 0.2 mC; 150 nm x 1.6 cm^2 = 2.4e-5 cm^3 -> 8.33 C/cm^3
    assert "21 steps" in text and "0.2 mC" in text and "8.33 C/cm" in text


def test_a_units_slip_shows_in_red_before_start(params_tab):
    params_tab.populate_from(dict(DEFAULT_SETTINGS, gitt_current_ua=10000.0))
    assert "cap" in params_tab.gitt_problems_label.text()
    assert params_tab.gitt_estimate.text() == ""


def test_the_dedt_fields_follow_their_checkbox(params_tab):
    params_tab.populate_from(dict(DEFAULT_SETTINGS, gitt_relax_settle_enabled=False))
    assert not params_tab._widgets["gitt_relax_dvdt_uv_s"].isEnabled()
    params_tab._widgets["gitt_relax_settle_enabled"].setChecked(True)
    assert params_tab._widgets["gitt_relax_dvdt_uv_s"].isEnabled()
    assert params_tab._gitt_relax_label.text() == "Maximum relaxation:"
