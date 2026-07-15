"""
Tests for ToolkitPotentiostat's thread handshake — specifically that a segment that
never fired is NOT released to run the waveform, and that a setup failure surfaces
instead of hanging. toolkitpy is hardware-only, so it's replaced with a MagicMock;
these tests exercise the arm/fire/finish coordination, not the Gamry itself.
"""
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
