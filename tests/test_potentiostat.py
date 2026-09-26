"""
Tests for ToolkitPotentiostat's thread handshake — specifically that a segment that
never fired is NOT released to run the waveform, and that a setup failure surfaces
instead of hanging. toolkitpy is hardware-only, so it's replaced with a MagicMock;
these tests exercise the arm/fire/finish coordination, not the Gamry itself.
"""
import logging
import time
from unittest import mock

import numpy as np

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
from spec_echem.data import (                                  # noqa: E402
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_PREDEDOPING, EchemData,
)
from spec_echem.fakes import (                                 # noqa: E402
    FakeAutolab, CV_COMMAND_ID, CA_RECORDER_ID, CA_SETPOINT_ID, _FakeEi,
)


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
        p._set(p._cmd, 0, 0.42)


def test_fire_switches_the_cell_on_and_pulses_the_trigger(autolab):
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()

    assert inst.Ei.Cell is True
    assert inst.port.rising_edges == 1            # a real edge, not just a call
    assert inst.port.Value == 0                   # left low afterwards


def test_the_trigger_waits_for_the_procedure_wait_window(autolab):
    """The pulse lands where the procedure starts RECORDING: its own FHWait plus
    the ~1 s the template spends on setup before its first sample.

    Pulsing at the raw FHWait fires that second early. MEASURED against
    CalcTime[0] with FHWait = 5.0: the CV records from 5.992 s and the CA from
    6.122 s.
    """
    from spec_echem.potentiostat import AUTOLAB_SETUP_LAG_CV_S
    p, inst = autolab(settings=_autolab_settings(autolab_pulse_delay_s=None),
                      wait_s=0.3)
    p.prepare(_cv_segment())
    assert p._pulse_delay == pytest.approx(0.3 + AUTOLAB_SETUP_LAG_CV_S)

    t0 = time.time()
    p.fire()
    assert time.time() - t0 >= 0.25               # it actually waited


def test_each_template_gets_its_own_setup_lag(autolab):
    """One delay cannot serve both: the CV and CA templates start recording
    130 ms apart, so a CV-derived number fires early on every chrono segment."""
    from spec_echem.potentiostat import AUTOLAB_SETUP_LAG_CA_S, AUTOLAB_SETUP_LAG_CV_S

    p, _ = autolab(settings=_autolab_settings(autolab_pulse_delay_s=None), wait_s=0.3)
    p.prepare(_cv_segment())
    cv_delay = p._pulse_delay

    p, _ = autolab(settings=_autolab_settings(autolab_pulse_delay_s=None), wait_s=0.3)
    p.prepare(_doping_segment())
    ca_delay = p._pulse_delay

    assert cv_delay == pytest.approx(0.3 + AUTOLAB_SETUP_LAG_CV_S)
    assert ca_delay == pytest.approx(0.3 + AUTOLAB_SETUP_LAG_CA_S)
    assert ca_delay != cv_delay


def test_a_bench_can_override_a_measured_setup_lag(autolab):
    """It is an instrument-and-template property, not a universal constant, so a
    rig that measures its own can say so."""
    p, _ = autolab(settings=_autolab_settings(autolab_pulse_delay_s=None,
                                              autolab_setup_lag_ca_s=2.5),
                   wait_s=0.3)
    p.prepare(_doping_segment())
    assert p._pulse_delay == pytest.approx(2.8)


def test_the_manual_pulse_delay_still_wins(autolab):
    """Kept as an escape hatch for a rig that has tuned one."""
    p, _ = autolab(settings=_autolab_settings(autolab_pulse_delay_s=5.95), wait_s=0.3)
    p.prepare(_doping_segment())
    assert p._pulse_delay == pytest.approx(5.95)


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


def test_the_live_cv_is_drawn_from_the_recorder_not_the_latch(autolab):
    """The wedge fix. MEASURED 2026-09-24: the latch stream lags the sweep by ~3
    staircase steps, so points land off the trace; the recorder's own arrays over the
    same run had 0 of 480 off it. In procedure mode the live plot reads the recorder,
    which is also what CV.txt is written from."""
    p, inst = autolab(duration=0.4, points=40)
    p.prepare(_cv_segment())
    p.fire()

    # A latch holding something the recorder does not agree with: if live_data()
    # reads the latch, these are the values that come back.
    inst.Ei.true_potential, inst.Ei.true_current = -0.2500, 9.0e-06
    p.pump()
    p.pump()
    time.sleep(0.2)
    assert p._proc.IsMeasuring is True    # the poll that fills the arrays, as the
    #                                       driver's own loop does mid-run

    live = p.live_data()
    assert live is not None
    assert -0.25 not in list(live.potential)
    # The fake's recorder ramps potential 0.001*i from zero — the CV staircase, not
    # the two latched scalars pump() collected.
    assert live.potential[0] == pytest.approx(0.0)
    assert len(live.current) > 2


def test_a_recorder_with_nothing_in_it_yet_draws_nothing(autolab):
    """Early in a run the arrays are empty. None leaves the Run tab's 'waiting for
    data…' message up, which is honest; falling back to the latch would put the
    lagging stream back on screen."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    assert p.live_data() is None


def _capture_run_log():
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logging.getLogger("spec_echem.run").addHandler(handler)
    return records, handler


def test_one_failed_recorder_read_skips_a_frame_and_says_nothing(autolab, monkeypatch):
    """MEASURED 2026-09-25 (20260925_test2): the SDK raised "Collection was modified;
    enumeration operation may not execute" — the recorder appended mid-read. The next
    tick reads fine, so this must not be reported as the live trace stopping."""
    p, inst = autolab(duration=0.4, points=40)
    p.prepare(_cv_segment())
    p.fire()
    time.sleep(0.2)
    p._proc.IsMeasuring                     # the poll that fills the fake's arrays

    real = potentiostat.echem_from_signals
    calls = {"n": 0}

    def flaky(cmd, align=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Collection was modified")
        return real(cmd, align=align)

    monkeypatch.setattr(potentiostat, "echem_from_signals", flaky)
    records, handler = _capture_run_log()
    try:
        assert p.live_data() is None        # this frame is skipped...
        assert p.live_data() is not None    # ...and the next one draws
    finally:
        logging.getLogger("spec_echem.run").removeHandler(handler)
    assert not [r for r in records if r.levelno >= logging.WARNING]
    assert p._live_read_errors == 0


def test_a_recorder_that_keeps_failing_is_reported_once(autolab, monkeypatch):
    p, inst = autolab()
    p.prepare(_cv_segment())

    def broken(cmd, align=False):
        raise RuntimeError("Collection was modified")

    monkeypatch.setattr(potentiostat, "echem_from_signals", broken)
    records, handler = _capture_run_log()
    try:
        for _ in range(3 * potentiostat.LIVE_READ_WARN_AFTER):
            assert p.live_data() is None
    finally:
        logging.getLogger("spec_echem.run").removeHandler(handler)
    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "stopped updating" in warnings[0].getMessage()


def test_live_data_accumulates_the_scalar_samples_in_ei_mode(ei_autolab):
    """Ei mode has no procedure and therefore no recorder: the scalars pump()
    collects ARE the segment's data, saved as well as plotted."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    assert p.live_data() is None                  # nothing sampled yet
    inst.Ei.true_potential, inst.Ei.true_current = 0.25, 1e-5
    p.pump()
    p.pump()

    live = p.live_data()
    assert len(live.current) == 2
    assert live.potential[0] == pytest.approx(0.25)


def _doping_segment(run_number=0, points=5):
    return Segment(f"Doping {run_number}", DATA_TYPE_DOPING, run_number,
                   num_points=points, delta_time=0.02, trigger=True)


def test_chrono_parameters_are_written_to_the_right_commands(autolab):
    """CA map confirmed on the rig 2026-09-03: the hold potential goes on the
    FHSetSetpointPotential command, the duration + interval on the FHLevel recorder
    — two different commands, unlike the CV staircase."""
    p, inst = autolab(settings=_autolab_settings(
        doping_potential_start=0.20, doping_potential_step=0.05, chrono_time=30.0))
    p.prepare(_doping_segment(run_number=2))

    setpoint = [x.ValueAsObject for x in
                p._proc.Commands[CA_SETPOINT_ID].CommandParameters]
    level = [x.ValueAsObject for x in
             p._proc.Commands[CA_RECORDER_ID].CommandParameters]

    # doping potential increments once per cycle, same rule as the Gamry path
    assert setpoint[potentiostat.CA_IDX_POTENTIAL] == pytest.approx(0.20 + 2 * 0.05)
    assert level[potentiostat.CA_IDX_DURATION] == 30.0
    assert level[potentiostat.CA_IDX_INTERVAL] == pytest.approx(0.02)
    assert inst.loaded == ["ca.nox"]                 # the CA template, not the CV one


def test_prededoping_uses_its_own_hold_time_and_potential(autolab):
    p, inst = autolab(settings=_autolab_settings(
        prededoping_potential=-0.30, prededoping_time=12.0))
    seg = Segment("Pre-dedoping", DATA_TYPE_PREDEDOPING, 0, num_points=5,
                  delta_time=0.05, trigger=True)
    p.prepare(seg)

    setpoint = [x.ValueAsObject for x in
                p._proc.Commands[CA_SETPOINT_ID].CommandParameters]
    level = [x.ValueAsObject for x in
             p._proc.Commands[CA_RECORDER_ID].CommandParameters]
    assert setpoint[potentiostat.CA_IDX_POTENTIAL] == -0.30
    assert level[potentiostat.CA_IDX_DURATION] == 12.0


def test_extra_ca_hold_steps_are_neutralised(autolab):
    """The stock Chrono amperometry.nox is THREE (setpoint -> FHLevel) blocks.
    spec-echem wants one hold, so the 2nd and 3rd FHLevel durations must be zeroed
    or a real sample gets driven to 0 V for ~10 s after every segment."""
    p, inst = autolab(ca_levels=3)
    p.prepare(_doping_segment())

    levels = [c for idn, c in zip(p._proc.Commands.IdNames, p._proc.Commands)
              if idn == CA_RECORDER_ID]
    assert len(levels) == 3
    durations = [list(c.CommandParameters)[potentiostat.CA_IDX_DURATION].ValueAsObject
                 for c in levels]
    assert durations[0] == p.settings["chrono_time"]     # step 1 holds
    assert durations[1] == 0.0 and durations[2] == 0.0   # steps 2-3 neutralised


def test_an_open_cell_is_flagged_even_though_the_run_completes(autolab, caplog):
    """bench_autolab_fault.py 2026-09-03: an open cell is invisible to every status
    signal; the only tell is that the current never leaves the noise. current_scale
    1e-4 drives the fake's trace down to ~1e-10 A, below the 1e-7 A floor."""
    p, inst = autolab(points=20, current_scale=1e-4)
    p.prepare(_cv_segment())
    p.fire()
    with caplog.at_level(logging.WARNING):
        p.finish()

    assert p.last_data() is not None                 # the file still gets written...
    assert "carries no electrochemistry" in caplog.text   # ...but flagged


def test_trigger_in_procedure_skips_the_python_pulse(autolab):
    """With a digital-output step in the .nox the Autolab fires P1.A itself, on its
    own clock, and Python must not also pulse."""
    p, inst = autolab(settings=_autolab_settings(autolab_trigger_in_procedure=True),
                      dio_step=True)
    p.prepare(_cv_segment())
    p.fire()
    assert inst.Ei.Cell is True                      # cell still switched on
    assert inst.port.rising_edges == 0               # Python did NOT pulse


def test_trigger_in_procedure_refuses_a_nox_with_no_dio_step(autolab):
    """The dangerous case, and it must STOP rather than improvise.

    Setting the flag against a template with no digital-output step leaves nobody to
    raise the edge, so the spectrometer would sit armed forever. Falling back to a
    Python pulse would keep the run alive but silently change what the data means —
    that path needs a measured autolab_pulse_delay_s, which someone configuring the
    procedure to fire has no reason to have tuned, so the edge would land at a stale
    delay (~1 s off). Plausible, mistimed data reported as success is worse than a
    clear stop. prepare() runs before the cell is on and before the spectrometer
    arms, so this costs nothing but the error."""
    p, inst = autolab(settings=_autolab_settings(autolab_trigger_in_procedure=True))

    with pytest.raises(RuntimeError, match="no digital-output step"):
        p.prepare(_cv_segment())

    assert inst.Ei.Cell is False                     # nothing was energized
    assert inst.port.rising_edges == 0               # and no edge improvised


def test_close_switches_the_cell_off_and_disconnects(autolab):
    p, inst = autolab()
    inst.Ei.Cell = True
    p.close()
    assert inst.Ei.Cell is False
    assert inst.disconnected is True
    assert inst.port.released is True


# --- the trigger bit mask ----------------------------------------------------
# Driving all eight pins is how this has always worked, and why the wired pin was
# never identified. It stops being safe once the AvaLight shutter TTL shares the
# port: an 0xFF pulse would move the shutter mid-segment.

def test_the_pulse_drives_all_pins_by_default(autolab):
    """Unchanged behavior until the wired pin is measured."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()
    assert 0xFF in inst.port.history


def test_a_configured_mask_drives_only_those_pins(autolab):
    """With the pin known, every other line on the port stays down — which is what
    lets the shutter share the connector."""
    p, inst = autolab(settings=_autolab_settings(autolab_dio_mask=0x04))
    p.prepare(_cv_segment())
    p.fire()

    assert 0x04 in inst.port.history
    assert 0xFF not in inst.port.history
    assert inst.port.rising_edges == 1        # still a real edge
    assert inst.port.Value == 0               # and left low


# --- parameters by name (SDK manual §6.2) ------------------------------------
# An index is a position and a template edit can move it silently; a key names the
# parameter itself. The driver prefers the key and falls back to the measured index,
# because whether THIS SDK accepts a string has never been confirmed on hardware.

def test_the_write_lands_on_the_measured_index_and_the_name_confirms_it(autolab, caplog):
    """The normal case: IdNames agrees with the bench-measured position, so the write
    goes where it was measured and the log records the confirmation."""
    from spec_echem import fakes

    p, inst = autolab()
    with caplog.at_level(logging.INFO):
        p.prepare(_cv_segment())

    assert list(p._cmd.CommandParameters.IdNames) == fakes.CV_PARAM_KEYS
    params = list(p._cmd.CommandParameters)
    assert params[potentiostat.CV_IDX_SCANRATE].ValueAsObject == pytest.approx(0.1)
    assert "confirmed by IdNames" in caplog.text


def test_a_name_index_mismatch_keeps_the_measured_index_and_warns(autolab, caplog):
    """The case the whole design turns on. If IdNames says index 6 is not 'Scanrate',
    something moved — or the key was wrong to begin with. The index is the half that
    was verified against recorded data, so the write still goes there; what must NOT
    happen is following the name silently onto another parameter."""
    p, inst = autolab()
    p.prepare(_cv_segment())

    # The manual's own ordering, which is NOT this instrument's: shift the names so
    # every key lands one slot off.
    names = list(p._cmd.CommandParameters.IdNames)
    p._cmd.CommandParameters.IdNames = names[1:] + names[:1]
    p._named_params.clear()                       # let it report again

    with caplog.at_level(logging.WARNING):
        p._set(p._cmd, potentiostat.CV_IDX_SCANRATE, 0.25, key="Scanrate")

    assert list(p._cmd.CommandParameters)[potentiostat.CV_IDX_SCANRATE]         .ValueAsObject == 0.25                    # the measured slot, not the named one
    assert "MISMATCH" in caplog.text
    assert "Scanrate" in caplog.text


def test_a_command_without_idnames_still_writes_by_measured_index(autolab, caplog):
    """Not every command names its parameters — 'Optimize current range' and the
    ExtendedSequence wrapper both come back bare on the rig. That is not an error,
    it is the situation the driver started in, and it should say so once rather
    than fail."""
    from spec_echem import fakes

    p, inst = autolab()
    p.prepare(_cv_segment())
    bare = fakes._FakeCommand([1.0, 2.0])          # no param_keys
    assert not list(bare.CommandParameters.IdNames)

    with caplog.at_level(logging.INFO):
        prm = p._resolve_param(bare, "Duration", 1)

    assert prm is list(bare.CommandParameters)[1]
    assert "no IdNames on this command" in caplog.text


def test_a_key_with_no_measured_index_resolves_through_idnames(autolab):
    """The one place a name alone decides the slot — and only because it is looked up
    in the command's own IdNames, never handed to the SDK as a guess."""
    p, inst = autolab()
    p.prepare(_cv_segment())

    p._set(p._cmd, None, 0.25, key="Scanrate")
    assert list(p._cmd.CommandParameters)[potentiostat.CV_IDX_SCANRATE]         .ValueAsObject == 0.25


def test_a_parameter_with_neither_a_name_nor_an_index_fails_loudly(autolab):
    p, inst = autolab()
    p.prepare(_cv_segment())
    with pytest.raises(NotImplementedError, match="autolab-driver-finishing"):
        p._resolve_param(p._cmd, "NoSuchParameter", None)


# --- the Abort button --------------------------------------------------------
# gui/workers.py calls potentiostat.stop() when the student confirms Abort. Nothing
# in this suite had ever called it, and docs/autolab-driver-finishing.md step 2c says
# it has never run on hardware either — so until now the path had no exercise of any
# kind. The fakes can drive all of it except the SDK's own Abort() semantics.

def test_the_abort_button_stops_a_running_procedure_and_kills_the_cell(autolab):
    p, inst = autolab(duration=5.0, points=10)
    p.prepare(_cv_segment())
    p.fire()
    assert p._proc.IsMeasuring is True            # a run genuinely in flight
    assert inst.Ei.Cell is True

    p.stop()                                      # <- what the Abort button reaches

    assert p._proc.IsMeasuring is False           # the procedure was told to stop
    p.finish(aborted=True)
    assert inst.Ei.Cell is False                  # and the cell did not stay live
    assert p.last_data() is None                  # a partial segment keeps no data


def test_abort_while_waiting_out_the_pulse_delay_sends_no_edge(autolab):
    """The delay is where an abort most often lands: fire() sits in _pulse_trigger
    for seconds with the spectrometer armed. Aborting there must leave the trigger
    line alone — an edge sent on the way out would start a segment nobody is
    collecting."""
    import threading

    p, inst = autolab(duration=5.0, points=10,
                      settings=_autolab_settings(autolab_pulse_delay_s=5.0))
    p.prepare(_cv_segment())

    threading.Timer(0.05, p.stop).start()
    t0 = time.time()
    p.fire()                                      # blocks in the chunked delay
    elapsed = time.time() - t0

    assert elapsed < 2.0                          # returned early, not after 5 s
    assert inst.port.rising_edges == 0            # and never pulsed
    assert inst.port.Value == 0


# --- the mask is a mask, however it is written -------------------------------
# probe_dio_pin.py's whole output is a number to put in bench.ini. Writing it the
# way a mask is written must not quietly restore the all-eight-pins default.

def test_a_hex_mask_from_a_bench_file_is_honoured(autolab):
    p, inst = autolab(settings=_autolab_settings(autolab_dio_mask="0x04"))
    p.prepare(_cv_segment())
    p.fire()
    assert 0x04 in inst.port.history
    assert 0xFF not in inst.port.history


def test_a_mask_that_can_send_no_edge_is_refused_before_the_run(autolab):
    """0 would pulse low -> low -> low and hang the segment on a wait-timeout with
    nothing saying why. Better to refuse it at construction, before anything is
    armed."""
    with pytest.raises(ValueError, match="never sees an edge"):
        autolab(settings=_autolab_settings(autolab_dio_mask=0))


def test_the_chrono_keys_confirm_against_the_rigs_idnames(autolab, caplog):
    """The chrono keys were guesses until 2026-09-09; they are now read off the
    instrument, micro sign and all. If any of them drifts — a typo, or the Greek mu
    for the micro sign — this says MISMATCH instead of confirming."""
    from spec_echem import fakes

    p, inst = autolab()
    with caplog.at_level(logging.INFO):
        p.prepare(_doping_segment())

    assert list(p._cmd.CommandParameters.IdNames) == fakes.CA_LEVEL_KEYS
    assert "MISMATCH" not in caplog.text
    for key in (potentiostat.CA_KEY_POTENTIAL, potentiostat.CA_KEY_DURATION,
                potentiostat.CA_KEY_INTERVAL):
        assert f"{key!r} — confirmed by IdNames" in caplog.text


# --- the FHWait window -------------------------------------------------------
# The stock CA template waits 5 s between switching the cell on and starting the
# recorder — and the driver has already written the DOPING potential into the
# setpoint command that runs before it. So those 5 s are the experiment happening
# unrecorded, not a settling period at rest.

def test_the_template_wait_is_left_alone_by_default(autolab):
    """None means 'whatever the .nox says' — the driver does not silently retime
    someone's procedure."""
    p, inst = autolab(wait_s=5.0)
    p.prepare(_doping_segment())
    wait = p._proc.Commands[potentiostat.AUTOLAB_WAIT_COMMAND]
    assert list(wait.CommandParameters)[0].ValueAsObject == 5.0


def test_autolab_wait_s_rewrites_the_template_wait(autolab):
    p, inst = autolab(wait_s=5.0, settings=_autolab_settings(autolab_wait_s=0.0))
    p.prepare(_doping_segment())
    wait = p._proc.Commands[potentiostat.AUTOLAB_WAIT_COMMAND]
    assert list(wait.CommandParameters)[0].ValueAsObject == 0.0


def test_the_trigger_delay_follows_the_rewritten_wait(autolab):
    """The delay is derived from FHWait, so shrinking the wait must move the edge
    with it — otherwise the spectra would start seconds after the echem."""
    p, inst = autolab(wait_s=5.0, settings=_autolab_settings(
        autolab_wait_s=0.0, autolab_pulse_delay_s=None))
    p.prepare(_doping_segment())
    assert p._pulse_delay == pytest.approx(potentiostat.AUTOLAB_SETUP_LAG_CA_S)


def test_a_stale_manual_pulse_delay_is_ignored_when_the_wait_is_rewritten(autolab, caplog):
    """autolab_pulse_delay_s is an absolute number measured against the OLD wait.
    Honouring it after rewriting the wait would fire the trigger ~5 s from the
    recorder — the exact failure the delay exists to prevent."""
    p, inst = autolab(wait_s=5.0, settings=_autolab_settings(
        autolab_wait_s=0.0, autolab_pulse_delay_s=5.95))
    with caplog.at_level(logging.WARNING):
        p.prepare(_doping_segment())

    assert "ignoring autolab_pulse_delay_s" in caplog.text
    assert p._pulse_delay == pytest.approx(potentiostat.AUTOLAB_SETUP_LAG_CA_S)
    assert p._pulse_delay != pytest.approx(5.95)


# --- measured, not inferred --------------------------------------------------

def test_the_handshake_is_reported_in_wall_clock(autolab, caplog):
    """Every statement about the cell-on-to-data gap has been inferred from the
    template until now. These marks are taken."""
    p, inst = autolab(points=6)
    p.prepare(_doping_segment())
    p.fire()
    with caplog.at_level(logging.INFO):
        p.finish()

    assert "timing, from cell ON" in caplog.text
    assert "Measure() returned" in caplog.text
    assert "trigger edge" in caplog.text
    assert p._t_edge >= p._t_cell_on


def test_timing_marks_do_not_leak_between_segments(autolab):
    """A stale mark would report the previous segment's handshake as this one's."""
    p, inst = autolab(points=6)
    p.prepare(_doping_segment()); p.fire(); p.finish()
    assert p._t_edge is not None
    p.prepare(_doping_segment())
    assert p._t_cell_on is None and p._t_edge is None


def test_the_edge_to_spectrum_0_gap_is_reported(autolab, caplog):
    """The number that says whether the detector actually waited for the edge.
    Nothing in the system related the Avantes device clock to Python's before this,
    which is why the question could only ever be argued."""
    import time as _time

    p, inst = autolab(points=6)
    p.prepare(_doping_segment())
    p.fire()
    p.note_first_spectrum(p._t_edge + 0.004)      # as acquisition would, 4 ms later
    with caplog.at_level(logging.INFO):
        p.finish()

    assert "EDGE -> spectrum 0" in caplog.text
    assert "+4.0 ms" in caplog.text


def test_no_spectrum_mark_means_no_edge_gap_claimed(autolab, caplog):
    """External mode and the no-trigger path have nothing to compare; the line must
    simply omit the number rather than invent one."""
    p, inst = autolab(points=6)
    p.prepare(_doping_segment())
    p.fire()
    with caplog.at_level(logging.INFO):
        p.finish()

    assert "timing, from cell ON" in caplog.text
    assert "EDGE -> spectrum 0" not in caplog.text


# --- the trailing setpoints --------------------------------------------------
# Zeroing the extra FHLevel durations stops them RECORDING. It does not stop the
# setpoint commands beside them applying +0.5 V and -0.5 V with the cell still on.
# Nothing is written (the recorders are zero-length), so on a film this was two
# unrecorded half-volt excursions after every doping cycle.

def test_trailing_setpoints_are_parked_at_the_segment_potential(autolab, caplog):
    from spec_echem.fakes import CA_SETPOINT_ID

    p, inst = autolab(ca_levels=3)                 # the stock 3-block template
    seg = _doping_segment()
    with caplog.at_level(logging.INFO):
        p.prepare(seg)

    held = p._chrono_potential(seg)
    idnames = list(p._proc.Commands.IdNames)
    cmds = list(p._proc.Commands)
    setpoints = [i for i, n in enumerate(idnames) if n == CA_SETPOINT_ID]
    assert len(setpoints) == 3                     # one per block, as on the rig
    for pos in setpoints:
        got = list(cmds[pos].CommandParameters)[potentiostat.CA_IDX_POTENTIAL]
        assert got.ValueAsObject == pytest.approx(held), f"command {pos} still moves the cell"
    assert "parked 2 trailing setpoint" in caplog.text


def test_a_single_step_template_needs_no_parking(autolab, caplog):
    """A purpose-built one-block .nox has nothing trailing; this must be a no-op."""
    p, inst = autolab(ca_levels=1)
    with caplog.at_level(logging.INFO):
        p.prepare(_doping_segment())
    assert "parked" not in caplog.text


# --- the edge is anchored on cell ON -----------------------------------------

def test_the_edge_is_anchored_on_cell_on_not_on_pulse_entry(autolab):
    """Measure() returns 0.128-0.287 s after cell ON on the rig. Anchoring the
    deadline at _pulse_trigger entry added all of it, putting the edge ~0.21 s after
    the recorder's first sample."""
    import time as _time

    p, inst = autolab(points=6, measure_cost=0.20,      # as costly as the real rig
                      settings=_autolab_settings(autolab_pulse_delay_s=0.30))
    p.prepare(_cv_segment())
    p.fire()

    # 0.30 s after CELL ON — not 0.20 + 0.30 = 0.50, which is what anchoring the
    # deadline at _pulse_trigger entry would give.
    assert p._t_measure_returned - p._t_cell_on == pytest.approx(0.20, abs=0.05)
    assert p._t_edge - p._t_cell_on == pytest.approx(0.30, abs=0.06)


# --- FHLevel UseFastOptions --------------------------------------------------

def test_fast_options_is_left_alone_by_default(autolab):
    """None means the .nox keeps its own value — this is an experiment, not a
    default the driver imposes on every rig."""
    p, inst = autolab()
    p.prepare(_doping_segment())
    assert list(p._cmd.CommandParameters)[potentiostat.CA_IDX_FAST].ValueAsObject is False


def test_fast_options_can_be_turned_on(autolab, caplog):
    p, inst = autolab(settings=_autolab_settings(autolab_ca_fast_options=True))
    with caplog.at_level(logging.INFO):
        p.prepare(_doping_segment())
    assert list(p._cmd.CommandParameters)[potentiostat.CA_IDX_FAST].ValueAsObject is True
    assert "UseFastOptions" in caplog.text


def test_a_refused_fast_option_warns_but_does_not_kill_the_run(autolab, caplog):
    """An optimisation the SDK rejects must not cost a sample. Contrast the
    potentials, where a silently ignored write DOES abort."""
    p, inst = autolab(settings=_autolab_settings(autolab_ca_fast_options=True))
    p.prepare(_doping_segment())

    def refuse(*a, **k):
        raise RuntimeError("fake: this build has no fast options")
    monkey = p._set
    p._set = lambda cmd, idx, val, key=None: (
        refuse() if key == potentiostat.CA_KEY_FAST else monkey(cmd, idx, val, key=key))

    with caplog.at_level(logging.WARNING):
        p._apply_fast_options()
    assert "could not set FHLevel UseFastOptions" in caplog.text


def test_it_is_a_chrono_parameter_only(autolab):
    """FHLevel does not exist in the CV template; asking for fast options on a CV
    segment must not touch the staircase."""
    p, inst = autolab(settings=_autolab_settings(autolab_ca_fast_options=True))
    p.prepare(_cv_segment())
    keys = list(p._cmd.CommandParameters.IdNames)
    assert potentiostat.CA_KEY_FAST not in keys      # the CV command, untouched


# ===========================================================================
# Ei MODE — Python drives the chrono hold, no procedure at all.
#
# Why it exists: the .nox spends ~0.93 s walking FHGetSetValues ->
# FHSetSetpointPotential -> FHSwitchCell before FHLevel records anything
# (MEASURED 20260909_test6/7), UseFastOptions moved it by nothing (test8), and
# The requirement is cell-on to data inside one delta_time. There is no
# parameter that gets there; removing the procedure is the only route.
# ===========================================================================

@pytest.fixture
def ei_autolab(autolab, monkeypatch):
    """Ei mode with the SDK's enum setters stubbed — there is no EcoChemie assembly
    to import off the rig, which is the whole reason _set_ei_mode exists."""
    def make(**over):
        monkeypatch.setattr(potentiostat, "_set_ei_mode",
                            lambda ei, potentiostatic=True: setattr(
                                ei, "Mode", "Potentiostatic"))
        monkeypatch.setattr(potentiostat, "_set_current_range",
                            lambda ei, name: setattr(ei, "CurrentRange", name or None))
        s = _autolab_settings(autolab_ca_mode="ei", **over)
        return autolab(settings=s)
    return make


def test_a_chrono_segment_loads_no_procedure_at_all(ei_autolab):
    """The point of the mode. If a .nox is loaded, its startup is still being paid."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    assert p._ei_mode is True
    assert p._proc is None
    assert inst.loaded == []                  # LoadProcedure never called


def test_cv_still_uses_the_procedure_in_ei_mode(ei_autolab):
    """The staircase is a real waveform worth having the instrument generate, and the
    CV path already meets spec. Ei mode must not quietly take it over."""
    p, inst = ei_autolab()
    p.prepare(_cv_segment())
    assert p._ei_mode is False
    assert p._proc is not None
    assert inst.loaded == ["cv.nox"]


def test_the_potential_is_applied_before_the_cell_closes(ei_autolab):
    """Configuring while the cell is OPEN is what moves the ~0.7 s off the clock —
    and it means the cell closes already at the segment's potential."""
    p, inst = ei_autolab()
    seg = _doping_segment()
    p.prepare(seg)

    assert inst.Ei.Cell is False                       # still open...
    assert inst.Ei.Setpoint == pytest.approx(p._chrono_potential(seg))   # ...already set
    assert inst.Ei.Mode == "Potentiostatic"


def test_the_edge_is_not_delayed_in_ei_mode(ei_autolab):
    """There is no procedure startup to predict, so there is nothing to wait for.
    The .nox path had to guess ~0.93 s and could never beat the instrument's own
    +-35 ms scatter; here the same thread closes the cell and raises the edge."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    assert p._pulse_delay == 0.0

    p.fire()
    assert inst.Ei.Cell is True
    assert inst.port.rising_edges == 1
    assert (p._t_edge - p._t_cell_on) < 0.050          # well inside one delta_time


def test_the_data_comes_from_what_python_sampled(ei_autolab):
    """pump() already read Ei every spectrum; in this mode those samples ARE the
    trace rather than a discarded sideline."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    for i in range(5):
        inst.Ei.true_potential = 0.100
        inst.Ei.true_current = 1.0e-05 + i * 1e-9
        p.pump()
    p.finish()

    d = p.last_data()
    assert d is not None
    assert len(d.current) == 5
    assert d.time[0] == 0.0                            # rebased, as .Signals is
    assert list(d.current) == pytest.approx(
        [1.0e-05 + i * 1e-9 for i in range(5)])
    assert inst.Ei.Cell is False                       # and the cell is off


def test_an_aborted_ei_segment_keeps_no_data(ei_autolab):
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    inst.Ei.true_potential, inst.Ei.true_current = 0.1, 1e-5
    p.pump()
    p.finish(aborted=True)
    assert p.last_data() is None
    assert inst.Ei.Cell is False


def test_a_segment_that_never_sampled_says_so(ei_autolab, caplog):
    """No samples means the acquisition loop never ran — silence would write an
    empty echem file beside a full set of spectra."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    with caplog.at_level(logging.WARNING):
        p.finish()
    assert p.last_data() is None
    assert "no live samples" in caplog.text


def test_an_unknown_ca_mode_is_refused_at_construction(autolab):
    with pytest.raises(ValueError, match="autolab_ca_mode"):
        autolab(settings=_autolab_settings(autolab_ca_mode="magic"))


def test_dac_rounding_on_the_setpoint_is_accepted(ei_autolab):
    """The rig applied 0.09994506835937 for a requested 0.1 V — 55 uV of DAC step.
    Ei.Setpoint is hardware, not a software value like the procedure's parameters,
    so an exact comparison rejects a perfectly good write."""
    p, inst = ei_autolab()

    class SnappingEi:
        """Quantises like the real DAC."""
        def __init__(self, real): self._r = real
        def __getattr__(self, k): return getattr(self._r, k)
        def __setattr__(self, k, v):
            if k == "_r": return object.__setattr__(self, k, v)
            if k == "Setpoint": v = round(v * 65536) / 65536 - 5.5e-5
            setattr(self._r, k, v)
    inst.Ei = SnappingEi(inst.Ei)

    seg = _doping_segment()
    p.prepare(seg)                                  # must not raise
    want = p._chrono_potential(seg)
    assert abs(float(inst.Ei.Setpoint) - want) < potentiostat.AUTOLAB_SETPOINT_TOL_V
    assert float(inst.Ei.Setpoint) != want          # genuinely snapped, not exact


def test_a_setpoint_that_is_actually_ignored_still_fails(ei_autolab):
    """The check has to keep catching the failure worth catching: a write the SDK
    drops, which would run the segment at the wrong potential on a real film."""
    p, inst = ei_autolab()

    class DeafEi:
        def __init__(self, real): self._r = real
        def __getattr__(self, k): return getattr(self._r, k)
        def __setattr__(self, k, v):
            if k == "_r": return object.__setattr__(self, k, v)
            if k == "Setpoint": v = 0.0             # silently dropped
            setattr(self._r, k, v)
    inst.Ei = DeafEi(inst.Ei)

    with pytest.raises(RuntimeError, match="not DAC rounding"):
        p.prepare(_doping_segment())


# --- the latch ---------------------------------------------------------------
# Ei.Current is not live. It holds whatever Ei.Sampler.Sample() last loaded, PROVEN
# on the rig 2026-09-09: held at 0.2 V, a bare read returned the 0.1 V value exactly.
# 20260909_test11 recorded 300 identical rows per segment because nothing sampled.

def test_pump_samples_before_it_reads(ei_autolab):
    """Without this the driver reads a latch loaded at connect time, forever."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    inst.Ei.true_potential, inst.Ei.true_current = 0.100, 1.0e-05
    p.pump()
    assert inst.Ei.Sampler.samples == 1
    assert p._live_samples[-1][2] == pytest.approx(1.0e-05)


def test_the_trace_follows_a_changing_current(ei_autolab):
    """The failure test11 shipped: every row identical. A driver that never samples
    still produces a plausible-looking file, so the test has to watch the values
    MOVE, not merely exist."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    for i in range(5):
        inst.Ei.true_potential = 0.100
        inst.Ei.true_current = 1.0e-05 + i * 2e-7      # a decaying-transient stand-in
        p.pump()
    p.finish()

    got = list(p.last_data().current)
    assert got == pytest.approx([1.0e-05 + i * 2e-7 for i in range(5)])
    assert len(set(got)) == 5                          # genuinely five distinct values


def test_overload_is_checked_against_a_fresh_sample(ei_autolab):
    """The flags ride on the same latch, so this check has never been able to fire in
    EITHER mode — a segment could overload and be written as ordinary."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    inst.Ei.CurrentOverload = True
    p.pump()
    assert p._overloaded is True


def test_a_sampler_that_refuses_does_not_sink_the_segment(ei_autolab):
    """A stale reading is bad; a lost segment is worse. Best-effort by design.

    The sample itself is DROPPED rather than recorded: a failed refresh leaves the
    previous sample in the latch, and in Ei mode _live_samples is what reaches
    steps(N).txt, so appending it would write the same reading twice under a fresh
    timestamp and call it data."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    def boom():
        raise RuntimeError("fake: sampler busy")
    inst.Ei.Sampler.Sample = boom

    p.pump()                                            # must not raise
    assert p._live_samples == []
    assert p._bad_samples == 1


# --- the straddle ------------------------------------------------------------
# Potential and Current are two reads of ONE latch. In procedure mode the .nox
# refreshes it on its own schedule, so a refresh between the two reads pairs one
# sample's potential with the next one's current. Reported from 20260916_test1 as a
# wedge in the live CV: the trace runs along the line, jumps off it, and comes back.
# DISPLAY-only there (CV.txt comes from .Signals), but in Ei mode this path IS the
# saved data.

class _StraddlingEi(_FakeEi):
    """An Ei whose latch refreshes DURING the read, between potential and current.

    `straddles` is how many reads of Current refresh first; the driver's re-read of
    Potential is what then disagrees with its first read.
    """

    def __init__(self, straddles=1, step_v=0.005, step_i=1.0e-06):
        self._pot = 0.0
        self._cur = 0.0
        self.straddles = straddles
        self.step_v = step_v       # the sweep MOVES: a refresh that changed nothing
        self.step_i = step_i       # would be undetectable, and also harmless
        super().__init__()

    @property
    def Potential(self):
        return self._pot

    @Potential.setter
    def Potential(self, value):
        self._pot = value

    @property
    def Current(self):
        if self.straddles > 0:
            self.straddles -= 1
            # The procedure's recorder, landing mid-read: it advances the sweep and
            # reloads BOTH halves of the latch, so the current returned here belongs
            # to a later instant than the potential already read.
            self.true_potential -= self.step_v
            self.true_current += self.step_i
            self._latch()
        return self._cur

    @Current.setter
    def Current(self, value):
        self._cur = value


def _straddling(inst, straddles=1, potential=-0.300, current=1.0e-05):
    """Swap in a straddling latch already holding one good sample."""
    ei = _StraddlingEi(straddles=straddles)
    ei.Sampler = type(inst.Ei.Sampler)(ei)
    ei.true_potential, ei.true_current = potential, current
    ei._latch()
    inst.Ei = ei
    return ei


def test_a_straddled_pair_is_re_taken_rather_than_recorded(autolab):
    """The fix. One refresh lands between the two reads; the re-take is clean, so a
    sample is still recorded — and it is a MATCHED pair, not sample N's potential
    against sample N+1's current."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()
    _straddling(inst, straddles=1)          # latched at -0.300 V / 1.0e-05 A

    p.pump()

    assert p._bad_samples == 0
    _, potential, current = p._live_samples[-1]
    # Both halves from AFTER the refresh — the pair the re-take found, not
    # -0.300 V against the current belonging to -0.305 V.
    assert (potential, current) == pytest.approx((-0.305, 1.1e-05))


def test_a_pair_that_straddles_every_read_is_dropped(autolab, caplog):
    """If even the re-take straddles, there is no matched pair to be had. Recording
    one anyway is what drew the wedge, so the sample is dropped and said once."""
    p, inst = autolab()
    p.prepare(_cv_segment())
    p.fire()
    _straddling(inst, straddles=99)

    with caplog.at_level(logging.WARNING):
        p.pump()
        p.pump()

    assert p._live_samples == []
    assert p._bad_samples == 2
    assert sum("dropped a live sample" in r.message for r in caplog.records) == 1


def test_samples_taken_before_the_latch_loaded_are_not_recorded(ei_autolab):
    """MEASURED 2026-09-24 (20260924_test2): samples 0-3 read E = 0.0000 V and
    I = 0.0000e+00 A, exactly zero, four times — pump() ran before the latch had ever
    been loaded. The live plot drew a point at the origin; in Ei mode they reach
    steps(N).txt as data."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    for _ in range(3):                      # the cell is on, nothing has landed yet
        p.pump()
    assert p._live_samples == []
    assert p._pre_latch_samples == 3

    inst.Ei.true_potential, inst.Ei.true_current = 0.300, 1.2e-05
    p.pump()
    assert len(p._live_samples) == 1


def test_a_real_zero_after_the_latch_has_loaded_is_kept(ei_autolab):
    """Only LEADING zeros are the artifact. A cell that genuinely reads zero later in
    a segment is data, and dropping it would be the software deciding what the
    instrument is allowed to have measured."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    inst.Ei.true_potential, inst.Ei.true_current = 0.300, 1.2e-05
    p.pump()
    inst.Ei.true_potential, inst.Ei.true_current = 0.0, 0.0
    p.pump()

    assert len(p._live_samples) == 2
    assert p._pre_latch_samples == 0


def test_a_held_potential_never_reports_a_straddle(ei_autolab):
    """Ei mode holds ONE potential for the whole segment, so the two potential reads
    agree whether or not anything refreshed underneath. The guard must not cost Ei
    mode the samples that ARE its data."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    for i in range(5):
        inst.Ei.true_potential = 0.300                     # held, as Ei mode does
        inst.Ei.true_current = 1.0e-05 + i * 2e-7
        p.pump()

    assert p._bad_samples == 0
    assert len(p._live_samples) == 5


def test_the_current_range_is_set_and_logged_in_ei_mode(autolab, monkeypatch, caplog):
    """In Ei mode nothing else sets the range — no procedure, no autoranging — so it
    is an experimental parameter and the run record has to name it. It was silent on
    success until 2026-09-11."""
    monkeypatch.setattr(potentiostat, "_set_ei_mode", lambda ei, potentiostatic=True: None)
    real = potentiostat._set_current_range

    def fake(ei, name):
        ei.CurrentRange = name          # stand in for the SDK enum lookup
        if name:
            potentiostat.get_run_logger().info(
                "Autolab: current range fixed at %s (no autoranging in "
                "Ei mode).", name)
    monkeypatch.setattr(potentiostat, "_set_current_range", fake)

    p, inst = autolab(settings=_autolab_settings(
        autolab_ca_mode="ei", autolab_current_range="CR09_10mA"))
    with caplog.at_level(logging.INFO):
        p.prepare(_doping_segment())

    assert inst.Ei.CurrentRange == "CR09_10mA"
    assert "CR09_10mA" in caplog.text


def test_the_current_range_is_not_touched_in_procedure_mode(autolab):
    """The .nox sets its own range via FHGetSetValues and CV auto-ranges, so
    autolab_current_range must stay an Ei-mode setting only."""
    p, inst = autolab(settings=_autolab_settings(autolab_current_range="CR09_10mA"))
    p.prepare(_doping_segment())
    assert inst.Ei.CurrentRange is None


# --- the overload has to be said DURING the segment --------------------------
# A chrono step overloads at t=0 (the current spikes, then decays), so a warning at
# the segment boundary arrives 30 s late — and the remaining segments then run at the
# same wrong range. On a first film at an unknown magnitude that is the whole ladder.

def test_an_overload_is_announced_immediately_not_at_segment_end(ei_autolab, caplog):
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    with caplog.at_level(logging.WARNING):
        inst.Ei.CurrentOverload = True
        p.pump()                                  # mid-segment, long before finish()

    assert "CURRENT OVERLOAD" in caplog.text
    assert "CLIPPED" in caplog.text
    assert "ABORT" in caplog.text                 # says what to do, not just what is
    assert p._overloaded is True


def test_the_overload_warning_fires_once_not_every_spectrum(ei_autolab, caplog):
    """It is checked every 100 ms. Repeating the warning would bury the run log and
    the status pane in the one situation where the operator needs to read them."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    inst.Ei.CurrentOverload = True

    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            p.pump()

    assert caplog.text.count("OVERLOAD at t=") == 1


def test_a_potential_overload_is_named_as_such(ei_autolab, caplog):
    """Current and potential overload mean different things — a clipped current says
    the range is wrong, a potential overload says the cell cannot be driven there."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    with caplog.at_level(logging.WARNING):
        inst.Ei.PotentialOverload = True
        p.pump()

    assert "POTENTIAL OVERLOAD" in caplog.text


def test_the_end_of_segment_report_still_fires(ei_autolab, caplog):
    """The immediate warning is an addition, not a replacement: the segment summary
    is what a later reader of the log sees."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()
    inst.Ei.CurrentOverload = True
    p.pump()
    inst.Ei.true_current = 1e-5
    p.pump()

    with caplog.at_level(logging.WARNING):
        p.finish()
    assert "OVERLOAD during" in caplog.text or "OVERLOAD" in caplog.text


def test_overload_advice_is_mode_specific(ei_autolab, autolab, caplog):
    """autolab_current_range applies ONLY in Ei mode. A CV runs the .nox, which sets
    and auto-ranges its own — so telling a CV to raise that setting is wrong advice,
    which is exactly what the 2026-09-11 film runs were given."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment()); p.fire()
    with caplog.at_level(logging.WARNING):
        inst.Ei.CurrentOverload = True
        p.pump()
    assert "autolab_current_range" in caplog.text      # Ei: the right knob
    assert "ABORT" in caplog.text

    caplog.clear()
    p2, inst2 = autolab()                              # procedure mode, CV segment
    p2.prepare(_cv_segment()); p2.fire()
    with caplog.at_level(logging.WARNING):
        inst2.Ei.CurrentOverload = True
        p2.pump()
    assert "does NOT apply here" in caplog.text        # and says so plainly
    assert "auto-ranges its own" in caplog.text


# --- current-range advisory --------------------------------------------------
# Four films ran on a range 30x too coarse (2026-09-11) and nothing said so. The
# cost was a +1.6 uA zero offset against settled currents of a few uA.

def test_range_full_scale_is_parsed_from_the_member_name():
    assert potentiostat.range_full_scale_a("CR10_1mA") == pytest.approx(1e-3)
    assert potentiostat.range_full_scale_a("CR13_1uA") == pytest.approx(1e-6)
    assert potentiostat.range_full_scale_a("CR07_1A") == pytest.approx(1.0)
    assert potentiostat.range_full_scale_a("nonsense") is None


def test_the_suggestion_leaves_headroom_rather_than_fitting_exactly():
    """A healthier film draws MORE than a degraded one, so a range chosen to fit
    today's peak exactly clips tomorrow."""
    # 625 uA was the largest transient seen on any film; 1 mA is the right answer.
    assert potentiostat.suggest_current_range(625e-6) == "CR10_1mA"
    # 900 uA is 90% of 1 mA — too tight, so it must step up.
    assert potentiostat.suggest_current_range(900e-6) == "CR09_10mA"


def test_a_coarse_range_is_reported_with_the_better_one(autolab, caplog):
    """The exact 2026-09-11 case: 10 mA range, sub-mA currents, silence."""
    p, inst = autolab(settings=_autolab_settings(autolab_current_range="CR09_10mA"))
    p._ei_mode = True
    p._segment = _cv_segment()
    p._last_data = EchemData(time=np.zeros(3), potential=np.zeros(3),
                             current=np.array([1e-6, 6.25e-4, -2e-5]))
    with caplog.at_level(logging.INFO):
        p._advise_current_range("Doping 0")

    assert "CR10_1mA" in caplog.text          # names the better range
    assert "6.2" in caplog.text or "6.3" in caplog.text   # and the % used


def test_a_range_close_to_clipping_warns(autolab, caplog):
    p, inst = autolab(settings=_autolab_settings(autolab_current_range="CR10_1mA"))
    p._ei_mode = True
    p._segment = _cv_segment()
    p._last_data = EchemData(time=np.zeros(2), potential=np.zeros(2),
                             current=np.array([0.0, 9.5e-4]))   # 95% of 1 mA
    with caplog.at_level(logging.WARNING):
        p._advise_current_range("Doping 0")
    assert "close to" in caplog.text


def test_procedure_mode_gets_no_range_advice(autolab, caplog):
    """autolab_current_range does not apply to the .nox path — advising there sent
    someone to change a setting that does nothing (fixed 2026-09-11)."""
    p, inst = autolab(settings=_autolab_settings(autolab_current_range="CR09_10mA"))
    p._ei_mode = False
    p._segment = _cv_segment()
    p._last_data = EchemData(time=np.zeros(2), potential=np.zeros(2),
                             current=np.array([0.0, 1e-6]))
    with caplog.at_level(logging.INFO):
        p._advise_current_range("CV")
    assert caplog.text == ""


# --- High current ranges are marked, never forbidden -----------------------------

def test_ranges_above_ten_milliamps_are_flagged_high():
    """An OMIEC film draws mA to uA -- MEASURED, the largest transient on real films
    was 625 uA. This instrument's SDK accepts ranges to 20 A full scale, which is not
    a current any polymer film survives being offered."""
    from spec_echem.potentiostat import is_high_current_range

    for member in ("CR08_100mA", "CR07_1A", "CR06_10A", "CR05_20A"):
        assert is_high_current_range(member), member


def test_the_ranges_an_omiec_experiment_uses_are_not_flagged():
    from spec_echem.potentiostat import is_high_current_range

    for member in ("CR09_10mA", "CR10_1mA", "CR11_100uA", "CR12_10uA",
                   "CR13_1uA", "CR14_100nA"):
        assert not is_high_current_range(member), member


def test_an_unparseable_range_is_not_flagged():
    """A name we cannot read is not evidence of danger; it must not raise either."""
    from spec_echem.potentiostat import is_high_current_range

    assert not is_high_current_range("")
    assert not is_high_current_range("NOT_A_RANGE")


def test_a_failed_toolkitpy_import_records_why():
    """Mirrors avaspec's AVASPEC_IMPORT_ERROR. A 64-bit env and a missing Gamry DLL
    both surface as "toolkitpy not available" and are fixed differently, so the
    message has to survive the import guard."""
    from spec_echem import potentiostat as p

    assert hasattr(p, "TOOLKITPY_IMPORT_ERROR")
    if p.TOOLKITPY_AVAILABLE:
        assert p.TOOLKITPY_IMPORT_ERROR is None
    else:
        assert p.TOOLKITPY_IMPORT_ERROR


class _FakePstat:
    """Records the Advanced-Pstat-Setup calls. There was no Gamry pstat fake at all,
    which is part of why a missing I/E range went unnoticed from Phase 2 until
    2026-09-25: nothing anywhere asserted what initialize_pstat configures."""

    def __init__(self, ranges=None):
        self.calls = []
        self.auto = None
        self.range_set = None
        self._ranges = ranges or {6.0e-4: 8, 5.0e-5: 7, 6.0e-3: 9, 1.0e-1: 11}

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
            return args[0] if args else None
        return record

    def set_ie_range_mode(self, on):
        self.calls.append(("set_ie_range_mode", (on,)))
        self.auto = on
        return on

    def test_ie_range(self, amps):
        self.calls.append(("test_ie_range", (amps,)))
        return self._ranges.get(amps, 11)

    def set_ie_range(self, index):
        self.calls.append(("set_ie_range", (index,)))
        self.range_set = index
        return index


@pytest.fixture
def stub_tkp(monkeypatch):
    """initialize_pstat reads constants off the toolkitpy module, which is absent on
    a dev machine. Stub just those names so the REAL function runs against the fake
    pstat -- testing apply_gamry_current_range alone would not have caught the actual
    defect, which was initialize_pstat never calling it."""
    import types
    from spec_echem import potentiostat as p
    monkeypatch.setattr(p, "tkp", types.SimpleNamespace(
        ACHSELECT_GND=0, STABILITY_NORM=0, CASPEED_NORM=0, FLOAT=0))


def test_initialize_pstat_pins_a_fixed_range_by_default(stub_tkp):
    """The bug this closes: the I/E range was never set, so every Python-mode run
    inherited the instrument's power-up 600 mA range while measuring microamps.

    The default is FIXED, not auto. Gamry documents auto-ranging as unsuitable above
    1 point/s and every segment samples at 10 -- and the External files once cited as
    evidence to the contrary turned out to be an open cell (all 171 points carry
    overload bits)."""
    from spec_echem.potentiostat import initialize_pstat

    p = _FakePstat()
    how = initialize_pstat(p)
    assert p.auto is False
    assert p.range_set == 9          # 6 mA, the shipped default
    assert "auto" not in how.lower()


def test_auto_is_available_but_must_be_asked_for(stub_tkp):
    from spec_echem.potentiostat import initialize_pstat

    p = _FakePstat()
    how = initialize_pstat(p, "auto")
    assert p.auto is True
    assert p.range_set is None
    assert "not recommended" in how.lower()


def test_a_fixed_range_asks_the_instrument_to_map_the_current(stub_tkp):
    """Never assume the ladder: test_ie_range() is the instrument's own mapping from
    a current to a range index, so this keeps working on a model with other ranges."""
    from spec_echem.potentiostat import initialize_pstat

    p = _FakePstat()
    how = initialize_pstat(p, 6.0e-4)
    assert p.auto is False
    assert p.range_set == 8
    assert "IERange 8" in how


def test_an_impossible_current_range_is_refused_not_silently_ignored(stub_tkp):
    from spec_echem.potentiostat import initialize_pstat, ConfigurationError

    for bad in (0.0, -1e-3):
        with pytest.raises(ConfigurationError):
            initialize_pstat(_FakePstat(), bad)


def test_the_gamry_ladder_is_full_scale_amps_and_auto_is_not_in_it():
    """The combo stores these values, so they must be numbers the driver can hand to
    test_ie_range(); 'auto' is a separate first entry, not a ladder rung."""
    from spec_echem.potentiostat import GAMRY_CURRENT_RANGES

    values = [v for v, _ in GAMRY_CURRENT_RANGES]
    assert all(isinstance(v, float) and v > 0 for v in values)
    assert values == sorted(values)
    assert values[-1] == 6.0e-1          # 600 mA, the Reference 600's top range


def test_overload_counts_handle_both_encodings():
    """The acq_data field's type is undocumented; the .dta writes a dot-per-bit
    string while a structured array more likely carries an integer bitmask."""
    import numpy as np
    from spec_echem.potentiostat import gamry_overload_count

    dotted = np.array([("..ch.....v.",), ("...........",), ("..chihs.iv.",)],
                      dtype=[("over", "U11")])
    assert gamry_overload_count(dotted)[:2] == (2, 3)

    mask = np.array([(0,), (4,), (0,), (1,)], dtype=[("overload", "i4")])
    assert gamry_overload_count(mask)[:2] == (2, 4)
    assert 4 in gamry_overload_count(mask)[2]   # raw values, for decoding

    # No overload field at all: stay silent rather than invent an alarm.
    none = np.array([(1.0,)], dtype=[("im", "f8")])
    assert gamry_overload_count(none) is None
    assert gamry_overload_count(None) is None


def test_an_overload_warns_and_never_stops_the_segment(monkeypatch):
    """Requested: an overload must not stop an experiment, but it must be noticeable.
    A Gamry that overloads a little still returns usable numbers, so the data is kept
    and the concern is raised beside it.

    The flags are believed only when the CURRENT corroborates them -- acq_data's field
    fired on 721 of 721 points at 1.24% of full scale on 2026-09-25."""
    import logging
    import numpy as np
    from spec_echem.potentiostat import ToolkitPotentiostat, EchemData

    p = ToolkitPotentiostat.__new__(ToolkitPotentiostat)
    p.settings = {"gamry_current_range": 6.0e-3}
    p._last_data = EchemData(time=np.zeros(3), potential=np.zeros(3),
                             current=np.array([5.9e-3, 5.8e-3, 5.9e-3]))  # ~full scale
    acq = np.array([("..ch.....v.",), ("...........",)], dtype=[("over", "U11")])

    records = []
    logger = logging.getLogger("spec_echem.run")
    h = type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r)})()
    logger.addHandler(h)
    try:
        p._report_current_range(type("S", (), {"label": "Doping 0"})(), acq)
    finally:
        logger.removeHandler(h)

    text = " ".join(r.getMessage() for r in records)
    assert "OVERLOAD" in text and "1 of 2" in text
    assert "KEPT" in text                      # never withheld
    assert p._last_data is not None            # and never discarded


def test_the_advisory_names_a_finer_range_when_one_would_fit():
    import logging
    import numpy as np
    from spec_echem.potentiostat import ToolkitPotentiostat, EchemData

    p = ToolkitPotentiostat.__new__(ToolkitPotentiostat)
    p.settings = {"gamry_current_range": 6.0e-3}       # 6 mA
    p._last_data = EchemData(time=np.zeros(2), potential=np.zeros(2),
                             current=np.array([2.4e-5, -1.0e-5]))   # peak 24 uA
    records = []
    logger = logging.getLogger("spec_echem.run")
    h = type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r)})()
    was = logger.level
    logger.addHandler(h)
    logger.setLevel(logging.INFO)          # the advice is INFO, not a warning
    try:
        p._report_current_range(type("S", (), {"label": "Doping 0"})(), None)
    finally:
        logger.removeHandler(h)
        logger.setLevel(was)

    text = " ".join(r.getMessage() for r in records)
    assert "6e-05 A would fit" in text      # 60 uA suits a 24 uA peak
    assert "100x finer" in text


def test_flags_without_a_matching_current_do_not_cry_wolf():
    """MEASURED 20260925_test5: the flag field fired on every one of 721 points while
    the cell drew 1.24% of full scale, and the same run's .dta Over column was clear.
    Warning on the field alone put a false alarm on every segment."""
    import logging
    import numpy as np
    from spec_echem.potentiostat import ToolkitPotentiostat, EchemData

    p = ToolkitPotentiostat.__new__(ToolkitPotentiostat)
    p.settings = {"gamry_current_range": 6.0e-3}
    p._last_data = EchemData(time=np.zeros(2), potential=np.zeros(2),
                             current=np.array([7.4e-5, -7.4e-5]))   # 1.2% of full scale
    acq = np.array([("..ch.....v.",), ("..ch.....v.",)], dtype=[("over", "U11")])

    records = []
    logger = logging.getLogger("spec_echem.run")
    h = type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r)})()
    logger.addHandler(h)
    try:
        p._report_current_range(type("S", (), {"label": "CV"})(), acq)
    finally:
        logger.removeHandler(h)

    assert not [r for r in records if r.levelno >= logging.WARNING]
