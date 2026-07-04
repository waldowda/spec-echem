"""
Tests for AvantesSpectrometer.measure() — the co-acquisition seam shared by BOTH
CV and chronoamp segments.

measure() is segment-type-agnostic: acquire_segment() calls it for every segment
and passes on_armed (which raises DIGOUT0 / starts the Gamry) for spectrum 0. So
the arm-then-fire ordering and the AVS_Measure failure guard tested here protect
the time-zero sync for CV and chrono identically — there is only one path.

The avaspec SDK is absent off-instrument (`from avaspec import *` is guarded), so
the AVS_* calls are monkeypatched in (raising=False, since they don't exist here).
"""
import pytest

import spec_echem.spectrometer as sm


def _detached_spectrometer():
    # Build an instance without __init__/hardware; measure() only needs dev_handle.
    spec = sm.AvantesSpectrometer.__new__(sm.AvantesSpectrometer)
    spec.dev_handle = 0
    return spec


def test_measure_raises_on_arm_failure_and_does_not_fire(monkeypatch):
    """A failed AVS_Measure must raise and must NOT fire the trigger — otherwise
    the Gamry would run while the spectrometer captures nothing (silent time-zero
    desync). Applies to CV and chrono alike (shared measure() path)."""
    monkeypatch.setattr(sm, "AVS_Measure", lambda *a: -1, raising=False)
    fired = []
    spec = _detached_spectrometer()
    with pytest.raises(RuntimeError, match="not armed"):
        spec.measure(on_armed=lambda: fired.append(True))
    assert fired == [], "trigger must not fire when arming fails"


def test_measure_success_arms_then_fires_and_returns_data(monkeypatch):
    """On success the guard is transparent: AVS_Measure (arm) happens, THEN
    on_armed (fire DIGOUT0), THEN data is collected — the exact ordering the
    time-zero sync depends on. This is the untouched happy path."""
    order = []
    monkeypatch.setattr(sm, "AVS_Measure",
                        lambda *a: order.append("armed") or 0, raising=False)
    monkeypatch.setattr(sm, "AVS_PollScan", lambda *a: True, raising=False)
    fake_spectrum = list(range(2000))       # long enough for the [395:1660] slice
    monkeypatch.setattr(sm, "AVS_GetScopeData",
                        lambda *a: (12.5, fake_spectrum), raising=False)
    spec = _detached_spectrometer()
    ts, data = spec.measure(on_armed=lambda: order.append("fired"))
    assert order == ["armed", "fired"], "must arm before firing the trigger"
    assert ts == 12.5
    assert len(data) == 1660 - 395          # sliced to the 1265 calibrated pixels


def test_measure_success_without_on_armed(monkeypatch):
    """No potentiostat (on_armed=None): still measures and returns data."""
    monkeypatch.setattr(sm, "AVS_Measure", lambda *a: 0, raising=False)
    monkeypatch.setattr(sm, "AVS_PollScan", lambda *a: True, raising=False)
    monkeypatch.setattr(sm, "AVS_GetScopeData",
                        lambda *a: (0.0, list(range(2000))), raising=False)
    spec = _detached_spectrometer()
    ts, data = spec.measure()
    assert len(data) == 1660 - 395
