"""
Tests for ToolkitPotentiostat's thread handshake — specifically that a segment that
never fired is NOT released to run the waveform, and that a setup failure surfaces
instead of hanging. toolkitpy is hardware-only, so it's replaced with a MagicMock;
these tests exercise the arm/fire/finish coordination, not the Gamry itself.
"""
import logging
import time
from unittest import mock

import pytest

from spec_echem import potentiostat
from spec_echem.experiment import Segment
from spec_echem.data import DATA_TYPE_PREDEDOPING
from spec_echem.settings import DEFAULT_SETTINGS


def _settings(tmp_path):
    s = DEFAULT_SETTINGS.copy()
    s["data_root"] = str(tmp_path)
    s["data_folder"] = "run"
    s["save_dta"] = False   # skip native .dta writing in these unit tests
    return s


def _pre_segment():
    return Segment("Pre-dedoping", DATA_TYPE_PREDEDOPING, 0,
                   num_points=5, delta_time=0.01, trigger=True, save=False)


@pytest.fixture
def toolkit(monkeypatch):
    """Swap toolkitpy for a MagicMock and mark it available. Returns (tkp, pstat, curve).
    curve.running() is False so the poll loop exits immediately (no real waveform)."""
    tkp = mock.MagicMock(name="tkp")
    pstat = mock.MagicMock(name="pstat")
    curve = mock.MagicMock(name="curve")
    curve.running.return_value = False
    # No data captured: these tests exercise the arm/fire/finish handshake, not the
    # echem payload. A MagicMock here would masquerade as a structured array.
    curve.acq_data.return_value = None
    tkp.Pstat.return_value = pstat
    tkp.ChronoCurve.return_value = curve
    tkp.RcvCurve.return_value = curve
    tkp.pstat_is_valid.return_value = True
    monkeypatch.setattr(potentiostat, "tkp", tkp)
    monkeypatch.setattr(potentiostat, "TOOLKITPY_AVAILABLE", True)
    return tkp, pstat, curve


def test_finish_without_fire_does_not_run_the_waveform(toolkit, tmp_path):
    """#1: if the spectrometer never armed (fire() never called), finishing must
    cancel the Gamry thread — NOT release it to run the waveform blind on the sample."""
    tkp, pstat, curve = toolkit
    p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))
    p.prepare(_pre_segment())
    p.finish(aborted=False)   # note: fire() was never called

    assert not curve.run.called                                   # waveform never ran
    assert mock.call(True) not in pstat.set_cell.call_args_list   # cell never turned ON
    assert mock.call(0x1, 0x1) not in pstat.set_digital_out.call_args_list  # no HIGH edge


def test_cancelling_an_unfired_segment_is_logged(toolkit, tmp_path):
    """The #1 safety net must leave a trace: a bench log that shows only the upstream
    spectrometer error can't otherwise prove the waveform was withheld.

    Captured with a handler on the run logger rather than caplog — the run logger sets
    propagate=False (so run records don't leak to the root logger), which is exactly
    the bug that made potentiostat errors silent in the first place.
    """
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    run_logger = logging.getLogger("spec_echem.run")
    run_logger.addHandler(handler)
    try:
        p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))
        p.prepare(_pre_segment())
        p.finish(aborted=False)
    finally:
        run_logger.removeHandler(handler)

    assert any("NOT applied" in r.getMessage() for r in records)


def _run_records(toolkit, tmp_path):
    """Run a segment through to completion, capturing what reached the run logger."""
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    run_logger = logging.getLogger("spec_echem.run")
    run_logger.addHandler(handler)
    try:
        p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))
        p.prepare(_pre_segment())
        p.fire()
        p.finish(aborted=False)
    finally:
        run_logger.removeHandler(handler)
    return [r.getMessage() for r in records]


def test_a_gamry_that_vanishes_mid_segment_is_reported(toolkit, tmp_path):
    """Bench-reproduced 2026-07-27: pulling the Gamry USB mid-segment ended the poll
    loop early, and that was indistinguishable from the step finishing — so a
    TRUNCATED echem file was written beside complete spectra, the segment was marked
    done, and the error only surfaced one segment later naming the wrong segment."""
    tkp, pstat, curve = toolkit
    tkp.pstat_is_valid.return_value = False      # the instrument went away

    messages = _run_records(toolkit, tmp_path)

    assert any("stopped responding" in m and "TRUNCATED" in m for m in messages), messages


def test_device_lost_is_reported_so_the_run_can_stop_at_this_segment(toolkit, tmp_path):
    """The run must stop at the segment that actually failed, not at the next one's
    setup — which is what named the wrong segment on the bench."""
    tkp, pstat, curve = toolkit
    tkp.pstat_is_valid.return_value = False

    p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))
    p.prepare(_pre_segment())
    p.fire()
    p.finish(aborted=False)

    assert p.device_lost()


def test_device_lost_resets_between_segments(toolkit, tmp_path):
    """A stale flag would abort the run on a later, healthy segment."""
    tkp, pstat, curve = toolkit
    p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))

    tkp.pstat_is_valid.return_value = False
    p.prepare(_pre_segment()); p.fire(); p.finish(aborted=False)
    assert p.device_lost()

    tkp.pstat_is_valid.return_value = True          # instrument back for the next segment
    p.prepare(_pre_segment()); p.fire(); p.finish(aborted=False)
    assert not p.device_lost()


def test_base_potentiostat_never_claims_a_lost_device(tmp_path):
    """External mode can't know — it must not stop runs on a guess."""
    assert not potentiostat.ExternalPotentiostat().device_lost()


def test_a_normal_segment_warns_about_nothing(toolkit, tmp_path):
    """Guard the above: a healthy segment must stay quiet, or the warning is noise."""
    messages = _run_records(toolkit, tmp_path)
    assert not any("TRUNCATED" in m or "stopped responding" in m for m in messages), messages


def test_normal_fire_runs_the_waveform(toolkit, tmp_path):
    """Guard the happy path: after fire(), the segment does run — cell on, DIGOUT0
    high, curve.run — so the #1 fix didn't break normal operation."""
    tkp, pstat, curve = toolkit
    p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))
    p.prepare(_pre_segment())
    p.fire()
    p.finish(aborted=False)

    assert curve.run.called
    assert mock.call(True) in pstat.set_cell.call_args_list
    assert mock.call(0x1, 0x1) in pstat.set_digital_out.call_args_list   # trigger edge


def test_prepare_raises_when_gamry_setup_fails(toolkit, tmp_path):
    """#3a: a failure while opening/building the Gamry must surface from prepare()
    rather than letting the caller arm the spectrometer into a trigger that never comes."""
    tkp, pstat, curve = toolkit
    tkp.Pstat.side_effect = RuntimeError("device busy")
    p = potentiostat.ToolkitPotentiostat(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="Gamry setup"):
        p.prepare(_pre_segment())


# --- acq_data -> EchemData -------------------------------------------------
# The Gamry field names stop at this converter; everything downstream sees
# EchemData. A wrong-shaped array must fail loudly here rather than three files
# later in the writer.

def _acq(names=("time", "vf", "im"), n=4):
    import numpy as np
    arr = np.zeros(n, dtype=np.dtype([(nm, "f8") for nm in names]))
    for i, nm in enumerate(names):
        arr[nm] = np.arange(n, dtype=float) + i
    return arr


def test_acq_data_maps_gamry_fields_onto_echem_data():
    e = potentiostat.echem_from_acq_data(_acq())
    assert list(e.time) == [0.0, 1.0, 2.0, 3.0]        # 'time'
    assert list(e.potential) == [1.0, 2.0, 3.0, 4.0]   # 'vf'
    assert list(e.current) == [2.0, 3.0, 4.0, 5.0]     # 'im'


def test_acq_data_missing_a_field_raises():
    with pytest.raises(ValueError, match="im"):
        potentiostat.echem_from_acq_data(_acq(names=("time", "vf")))


def test_no_data_yet_is_none_not_an_error():
    """Early in a poll loop there may be nothing at all — that is not a broken
    contract, and must not raise inside the segment thread."""
    assert potentiostat.echem_from_acq_data(None) is None


# --- make_potentiostat: one place decides who drives the cell ---------------

def test_factory_defaults_to_external():
    """An absent or empty mode must give the PROVEN path, never a Python driver
    nobody asked for."""
    assert isinstance(potentiostat.make_potentiostat({}),
                      potentiostat.ExternalPotentiostat)


def test_factory_builds_the_toolkit_driver_for_python_mode(toolkit, tmp_path):
    p = potentiostat.make_potentiostat(dict(_settings(tmp_path),
                                            potentiostat_mode="python"))
    assert isinstance(p, potentiostat.ToolkitPotentiostat)


def test_factory_rejects_an_unknown_mode():
    """A typo in bench.ini must say so, not quietly run with nobody driving the
    cell — which would look like a successful External run producing no echem."""
    with pytest.raises(ValueError, match="gamry"):
        potentiostat.make_potentiostat({"potentiostat_mode": "gamry"})


# ===========================================================================
# AutolabPotentiostat
#
# Driven against fakes.FakeAutolab, which mimics what the SDK actually did on the
# rig (docs/autolab-run-api.md). NOTE the limit of these tests: the fake encodes the
# same understanding of the SDK as the driver does, so a green suite proves internal
# consistency and catches regressions — it cannot catch a misreading of the SDK.
# Only the bench settles that.
# ===========================================================================
from spec_echem.data import DATA_TYPE_CV, DATA_TYPE_DOPING     # noqa: E402
from spec_echem.fakes import FakeAutolab, CV_COMMAND_ID        # noqa: E402


@pytest.fixture
def autolab(monkeypatch):
    """Return a factory: make(**kwargs) -> (driver, fake_instrument), opened."""
    def make(settings=None, **kwargs):
        inst = FakeAutolab(**kwargs)
        monkeypatch.setattr(potentiostat, "AUTOLAB_AVAILABLE", True)
        monkeypatch.setattr(potentiostat, "open_instrument", lambda s: inst)
        monkeypatch.setattr(potentiostat, "open_trigger_port",
                            lambda i, index=0: i.port)
        monkeypatch.setattr(potentiostat, "_set_cell",
                            lambda i, on: setattr(i.Ei, "Cell", on))
        p = potentiostat.AutolabPotentiostat(settings or _autolab_settings())
        p.open()
        return p, inst
    return make


def _autolab_settings(**over):
    s = DEFAULT_SETTINGS.copy()
    s.update({
        "autolab_sdk": "sdk", "autolab_adx": "adx", "autolab_hdw": "hdw",
        "autolab_nox_cv": "cv.nox", "autolab_nox_ca": "ca.nox",
        "autolab_pulse_delay_s": 0.0,      # keep the tests fast
        "cv_initial_v": 0.1, "cv_limit1_v": 0.8, "cv_limit2_v": -0.7,
        "cv_final_v": 0.05, "cv_step_size": 2.44, "cv_scan_rate": 100.0,
        "cv_cycles": 3,
    })
    s.update(over)
    return s


def _cv_segment(points=5):
    return Segment("CV", DATA_TYPE_CV, 0, num_points=points, delta_time=0.01,
                   trigger=True)


def test_factory_builds_the_autolab_driver(autolab, monkeypatch):
    monkeypatch.setattr(potentiostat, "AUTOLAB_AVAILABLE", True)
    p = potentiostat.make_potentiostat(
        dict(_autolab_settings(), potentiostat_mode="autolab"))
    assert isinstance(p, potentiostat.AutolabPotentiostat)


def test_every_segment_reloads_the_procedure(autolab):
    """The conservative answer to the unresolved buffer question: whether a second
    Measure() reuses the first run's .Signals is unknown, and reloading is correct
    either way. If this stops happening, segment 2 may silently carry segment 1."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.prepare(_cv_segment())
    assert inst.loaded == ["cv.nox", "cv.nox"]


def test_cv_parameters_are_written_from_settings(autolab):
    """Including the unit conversions, which are the easy thing to get wrong: the
    SDK stores scan rate in V/s while NOVA's UI shows mV/s."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    prm = [x.ValueAsObject for x in p._cmd.CommandParameters]

    assert prm[potentiostat.CV_IDX_START] == 0.1
    assert prm[potentiostat.CV_IDX_UPPER] == 0.8
    assert prm[potentiostat.CV_IDX_LOWER] == -0.7
    assert prm[potentiostat.CV_IDX_STOP] == 0.05
    assert prm[potentiostat.CV_IDX_STEP] == pytest.approx(0.00244)   # mV -> V
    assert prm[potentiostat.CV_IDX_SCANRATE] == pytest.approx(0.1)   # mV/s -> V/s
    assert prm[potentiostat.CV_IDX_CROSSINGS] == 6                   # 2 per cycle


def test_a_parameter_that_does_not_stick_raises(autolab):
    """A silently ignored potential would run the wrong experiment on a real
    sample, so the write is verified rather than assumed."""
    p, inst = autolab()
    p.prepare(_cv_segment())

    class _Stubborn:
        ValueAsObject = 0.0
        def __setattr__(self, name, value):
            pass                                  # accepts writes, keeps the old value

    p._cmd.CommandParameters._items[0] = _Stubborn()
    with pytest.raises(RuntimeError, match="did not take"):
        p._set(0, 0.42)


def test_fire_switches_the_cell_on_and_pulses_the_trigger(autolab):
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()

    assert inst.Ei.Cell is True
    assert inst.port.rising_edges == 1            # a real edge, not just a call
    assert inst.port.Value == 0                   # left low afterwards


def test_the_trigger_waits_for_the_procedure_wait_window(autolab):
    """The pulse goes inside the procedure's own wait, so the optical and echem
    clocks start together instead of a wait-length apart."""
    p, inst = autolab(settings=_autolab_settings(autolab_pulse_delay_s=None),
                      wait_s=0.3)
    p.prepare(_cv_segment())
    assert p._pulse_delay == 0.3

    t0 = time.time()
    p.fire()
    assert time.time() - t0 >= 0.25               # it actually waited


def test_finish_builds_echem_data_rebased_to_zero(autolab):
    """CalcTime starts at the wait value, not 0, and the MEASURED potential is what
    belongs in the file — SetpointApplied is only what was commanded."""
    p, inst = autolab(points=10, wait_s=5.0)
    p.prepare(_cv_segment())
    p.fire()
    p.finish()

    data = p.last_data()
    assert data is not None
    assert len(data.current) == 10
    assert data.time[0] == 0.0                    # rebased
    assert data.time[-1] == pytest.approx(9 * 0.024414)
    assert inst.Ei.Cell is False                  # cell off after the segment


def test_an_aborted_segment_keeps_no_data(autolab):
    p, inst = autolab(points=10)
    p.prepare(_cv_segment())
    p.fire()
    p.finish(aborted=True)
    assert p.last_data() is None


def test_an_overload_is_reported_even_though_the_run_completes(autolab, caplog):
    """The whole reason pump() exists. An overloaded run finishes normally and its
    data looks ordinary; if nothing samples the flags, it is written as if fine."""
    p, inst = autolab(points=6)
    p.prepare(_cv_segment())
    p.fire()
    inst.Ei.CurrentOverload = True
    p.pump()
    with caplog.at_level(logging.WARNING):
        p.finish()

    assert "OVERLOAD" in caplog.text
    assert p.last_data() is not None              # the data is still returned...
    assert p._overloaded                          # ...but flagged


def test_a_vanished_instrument_is_noticed_by_pump(autolab):
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()
    inst.lose_connection()
    p.pump()
    assert p.device_lost() is True


def test_device_lost_resets_between_segments(autolab):
    p, inst = autolab()
    p.prepare(_cv_segment())
    p._device_lost = True
    p.prepare(_cv_segment())
    assert p.device_lost() is False


def test_live_data_accumulates_the_scalar_samples(autolab):
    """The Autolab gives instantaneous values, not a growing array, so the live
    trace is built from what pump() collected."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()
    assert p.live_data() is None                  # nothing sampled yet
    inst.Ei.Potential, inst.Ei.Current = 0.25, 1e-5
    p.pump()
    p.pump()

    live = p.live_data()
    assert len(live.current) == 2
    assert live.potential[0] == pytest.approx(0.25)


def test_a_chrono_segment_fails_loudly_until_the_ca_map_is_known(autolab):
    """Three of the four data types are chrono holds and the CA parameter indices
    are still unknown. Reaching this path must name what is missing, not write a
    plausible-looking wrong potential."""
    p, inst = autolab()
    seg = Segment("Doping 0", DATA_TYPE_DOPING, 0, num_points=5, delta_time=0.01,
                  trigger=True)
    with pytest.raises(NotImplementedError, match="autolab-driver-finishing"):
        p.prepare(seg)


def test_close_switches_the_cell_off_and_disconnects(autolab):
    p, inst = autolab()
    inst.Ei.Cell = True
    p.close()
    assert inst.Ei.Cell is False
    assert inst.disconnected is True
    assert inst.port.released is True
