"""A run that never receives its trigger says so (2026-10-09: the Avantes trigger
cable was on the Reference 600 while an Interface 1010E ran the CV, and the GUI sat
at 'Collecting' with nothing to explain it)."""
import time

import pytest

from spec_echem import acquisition
from spec_echem.fakes import FakeSpectrometer


class _SlowTrigger(FakeSpectrometer):
    """Arms, fires the potentiostat, then waits `wait_s` for an edge."""

    def __init__(self, wait_s):
        super().__init__()
        self.wait_s, self.calls = wait_s, 0

    def set_trigger_mode(self, mode, measconfig=None):
        pass

    def measure(self, abort_event=None, on_armed=None):
        self.calls += 1
        if on_armed is not None:
            on_armed()
            time.sleep(self.wait_s)
        return time.time() * 1e5, self._window(self._synthetic_spectrum())


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(acquisition, "NO_TRIGGER_WARN_S", 0.05)


def _warned(caplog):
    return [r for r in caplog.records if "No trigger after" in r.getMessage()]


def test_a_missing_trigger_is_warned_while_still_waiting(quick, caplog):
    with caplog.at_level("WARNING"):
        acquisition.acquire_segment(_SlowTrigger(0.3), 2, delta_time=0.01,
                                    trigger=True, on_armed=lambda: None)
    assert len(_warned(caplog)) == 1


def test_a_prompt_trigger_says_nothing(quick, caplog):
    with caplog.at_level("WARNING"):
        acquisition.acquire_segment(_SlowTrigger(0.0), 2, delta_time=0.01,
                                    trigger=True, on_armed=lambda: None)
    time.sleep(0.15)                         # past when a stray warning would fire
    assert _warned(caplog) == []


def test_external_mode_waits_without_a_warning(quick, caplog):
    """In External mode a person starts the sequence; minutes are normal."""
    with caplog.at_level("WARNING"):
        acquisition.acquire_segment(_SlowTrigger(0.3), 2, delta_time=0.01,
                                    trigger=True, on_armed=None)
    assert _warned(caplog) == []
