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
from spec_echem.data import (                                  # noqa: E402
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_PREDEDOPING,
)
from spec_echem.fakes import (                                 # noqa: E402
    FakeAutolab, CV_COMMAND_ID, CA_RECORDER_ID, CA_SETPOINT_ID,
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


def test_live_data_accumulates_the_scalar_samples(autolab):
    """The Autolab gives instantaneous values, not a growing array, so the live
    trace is built from what pump() collected."""
    p, inst = autolab()
    p.prepare(_cv_segment())
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
    """Unchanged behaviour until the wired pin is measured."""
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
# Dean's requirement is cell-on to data inside one delta_time. There is no
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
    """A stale reading is bad; a lost segment is worse. Best-effort by design."""
    p, inst = ei_autolab()
    p.prepare(_doping_segment())
    p.fire()

    def boom():
        raise RuntimeError("fake: sampler busy")
    inst.Ei.Sampler.Sample = boom

    p.pump()                                            # must not raise
    assert len(p._live_samples) == 1


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
