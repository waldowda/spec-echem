"""
Tests for ToolkitPotentiostat's thread handshake — specifically that a segment that
never fired is NOT released to run the waveform, and that a setup failure surfaces
instead of hanging. toolkitpy is hardware-only, so it's replaced with a MagicMock;
these tests exercise the arm/fire/finish coordination, not the Gamry itself.
"""
import logging
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
