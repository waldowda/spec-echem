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
import importlib
import os
import sys
import warnings

import pytest

import spec_echem.spectrometer as sm
from spec_echem.fakes import FakeSpectrometer


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


# --- AvaSpec DLL preload (SPEC_ECHEM_AVASPEC_DLL_DIR) ------------------------
# Import-time behavior, so each case reloads the module. ctypes.WinDLL exists only
# on Windows; it is faked here so the guard can be tested anywhere.

def _reload_spectrometer(monkeypatch, dll_dir, loader=None):
    """Reimport spec_echem.spectrometer with the env var set, recording what the
    module tried to preload."""
    loaded = []
    fake = loader or loaded.append
    monkeypatch.setattr(sm.ctypes, "WinDLL", fake, raising=False)
    monkeypatch.setenv("SPEC_ECHEM_AVASPEC_DLL_DIR", str(dll_dir))
    monkeypatch.delitem(sys.modules, "spec_echem.spectrometer", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("spec_echem.spectrometer")
    return loaded, caught


def test_dll_is_preloaded_by_absolute_path(monkeypatch, tmp_path):
    """The wrapper loads "./avaspecx64.dll" against the CWD, so the search path can't
    help it — only an already-loaded module of the same base name can."""
    loaded, caught = _reload_spectrometer(monkeypatch, tmp_path)
    assert len(loaded) == 1
    assert os.path.isabs(loaded[0])
    assert os.path.dirname(loaded[0]) == str(tmp_path)
    assert os.path.basename(loaded[0]) in ("avaspecx64.dll", "avaspec.dll")
    assert not caught


def test_preload_failure_warns_but_does_not_raise(monkeypatch, tmp_path):
    """A bad folder must not stop the package importing — the plain import may still
    succeed from the DLL's own directory."""
    def boom(_path):
        raise OSError("[WinError 126] The specified module could not be found")

    _, caught = _reload_spectrometer(monkeypatch, tmp_path, loader=boom)
    assert any("Could not preload" in str(w.message) for w in caught)


def test_unset_dll_dir_preloads_nothing(monkeypatch):
    loaded = []
    monkeypatch.setattr(sm.ctypes, "WinDLL", loaded.append, raising=False)
    monkeypatch.delenv("SPEC_ECHEM_AVASPEC_DLL_DIR", raising=False)
    monkeypatch.delitem(sys.modules, "spec_echem.spectrometer", raising=False)
    importlib.import_module("spec_echem.spectrometer")
    assert loaded == []


def test_import_failure_records_its_reason():
    """A bare "avaspec: no" cannot distinguish a missing wrapper from a missing
    DLL; the reason string is what tells them apart in the launch banner."""
    import spec_echem.spectrometer as sp
    if sp.AVASPEC_AVAILABLE:
        assert sp.AVASPEC_IMPORT_ERROR is None
    else:
        assert sp.AVASPEC_IMPORT_ERROR


# --- measure_timing(): must never hang, since on_timing_test() runs it on the GUI
# thread. A failed arm raises; a scan that never completes times out rather than
# spinning forever (the ULS2048L below its ~1.05 ms floor, or an unfired trigger).

class _FakeMeasConfig:
    def __init__(self, integration_ms=2.0, averages=1):
        self.m_IntegrationTime = integration_ms
        self.m_NrAverages = averages


def _timing_spectrometer(integration_ms=2.0, averages=1):
    spec = _detached_spectrometer()
    spec.measconfig = _FakeMeasConfig(integration_ms, averages)
    return spec


def test_measure_timing_raises_on_arm_failure(monkeypatch):
    monkeypatch.setattr(sm, "AVS_Measure", lambda *a: -1, raising=False)
    with pytest.raises(RuntimeError, match="AVS_Measure failed"):
        _timing_spectrometer().measure_timing()


def test_measure_timing_times_out_instead_of_hanging(monkeypatch):
    """AVS_PollScan never goes ready — the inner loop must break on its deadline,
    not spin. A fake clock jumps past the deadline so the test is instant."""
    monkeypatch.setattr(sm, "AVS_Measure", lambda *a: 0, raising=False)
    monkeypatch.setattr(sm, "AVS_PollScan", lambda *a: False, raising=False)
    ticks = iter([0.0, 0.0] + [1000.0] * 50)
    monkeypatch.setattr(sm.time, "time", lambda: next(ticks))
    monkeypatch.setattr(sm.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="No scan completed"):
        _timing_spectrometer().measure_timing()


def test_measure_timing_success_returns_four_tuple(monkeypatch):
    monkeypatch.setattr(sm, "AVS_Measure", lambda *a: 0, raising=False)
    monkeypatch.setattr(sm, "AVS_PollScan", lambda *a: True, raising=False)
    monkeypatch.setattr(sm, "AVS_GetScopeData",
                        lambda *a: (7.0, list(range(2000))), raising=False)
    ts, data, net_dif, t_dif = _timing_spectrometer().measure_timing()
    assert ts == 7.0
    assert len(data) == 1660 - 395
    assert t_dif >= 0


# --- the detector's minimum integration time ---------------------------------
# monkeypatch, not a bare assignment: `from avaspec import *` puts AVS_GetParameter
# in the module's globals, so patching it without restoring leaks into every test
# that follows -- which is exactly what the first version of these did.

def _spectrometer_with(monkeypatch, devconfig):
    """Take the class AND the patched global from ONE module object.

    _reload_spectrometer above deletes spec_echem.spectrometer from sys.modules and
    re-imports it, so importing the class and patching the module separately can
    reach different module objects -- the method then resolves AVS_GetParameter
    through globals the patch never touched. These tests passed alone and failed in
    the file until this was pinned.
    """
    mod = sys.modules["spec_echem.spectrometer"]
    monkeypatch.setattr(mod, "AVS_GetParameter", lambda handle, size: devconfig,
                        raising=False)
    spec = mod.AvantesSpectrometer.__new__(mod.AvantesSpectrometer)
    spec.dev_handle = "H"
    return spec


def test_the_minimum_is_read_from_the_flattened_field(monkeypatch):
    """The SDK's DeviceConfigType carries m_Detector.m_MinIntegrationTime, but this
    project's avaspec wrapper returns the struct with FLATTENED names -- the working
    code reads devcon.m_Detector_m_NrPixels, not devcon.m_Detector.m_NrPixels."""
    class Flat:
        m_Detector_m_MinIntegrationTime = 1.05

    spec = _spectrometer_with(monkeypatch, Flat())
    assert spec.minimum_integration_time() == pytest.approx(1.05)


def test_the_minimum_is_also_read_from_the_nested_field(monkeypatch):
    """The other spelling, in case a wrapper preserves the nested struct."""
    class Detector:
        m_MinIntegrationTime = 0.002

    class Nested:
        m_Detector = Detector()

    spec = _spectrometer_with(monkeypatch, Nested())
    assert spec.minimum_integration_time() == pytest.approx(0.002)


def test_an_sdk_without_the_field_falls_back_to_probing(monkeypatch):
    """MEASURED: no SDK on this wrapper exposes a minimum, so the fallback is the
    path that actually runs. It bisects what AVS_PrepareMeasure accepts -- trusted
    because on real hardware the accepted 0.009033 ms was verified against the
    detector's own integral (counts = 112 + 26051 t, within 1% from 0.009 to 0.1 ms)."""
    class Bare:
        m_Detector_m_NrPixels = 2048

    class Cfg:
        m_IntegrationTime = 2.0

    mod = sys.modules["spec_echem.spectrometer"]
    spec = _spectrometer_with(monkeypatch, Bare())
    spec.measconfig = Cfg()
    # a detector that refuses anything below 1.05 ms
    monkeypatch.setattr(mod, "AVS_PrepareMeasure",
                        lambda h, cfg: 0 if cfg.m_IntegrationTime >= 1.05 else -1,
                        raising=False)
    assert spec.minimum_integration_time() == pytest.approx(1.05, abs=1e-3)
    assert spec.measconfig.m_IntegrationTime == 2.0, "must restore what it found"


def test_probing_returns_none_when_there_is_no_config_to_probe_with(monkeypatch):
    """None means "keep your own default", not zero -- a zero would be taken as a
    valid minimum and let the ramp start below what the detector can do."""
    class Bare:
        m_Detector_m_NrPixels = 2048

    spec = _spectrometer_with(monkeypatch, Bare())
    assert spec.minimum_integration_time() is None


def test_a_probe_failure_never_breaks_connect(monkeypatch):
    def boom(handle, size):
        raise RuntimeError("device busy")

    mod = sys.modules["spec_echem.spectrometer"]
    monkeypatch.setattr(mod, "AVS_GetParameter", boom, raising=False)
    spec = mod.AvantesSpectrometer.__new__(mod.AvantesSpectrometer)
    spec.dev_handle = "H"
    assert spec.minimum_integration_time() is None


# --- An exposure the detector cannot give must SAY so ----------------------------

def test_an_exposure_below_the_floor_is_refused_with_a_useful_message():
    """Before this, PrepareMeasure rejected the value, nothing noticed, and the
    symptom was a poll loop timing out -- which names nothing and reads like a dead
    instrument rather than a number that is out of range."""
    spec = FakeSpectrometer()
    spec.min_integration_time = 1.05
    spec.init()

    with pytest.raises(ValueError) as excinfo:
        spec.set_integration_time(0.022)

    message = str(excinfo.value)
    assert "0.022" in message            # what was asked for
    assert "1.05" in message             # what the detector will accept
    assert spec.serial_number in message  # which detector said so


def test_the_floor_is_latched_at_init_not_guessed():
    spec = FakeSpectrometer()
    spec.min_integration_time = 1.05
    assert spec.min_integration_ms is None      # nothing known before connecting
    spec.init()
    assert spec.min_integration_ms == 1.05


def test_an_exposure_at_or_above_the_floor_is_accepted():
    spec = FakeSpectrometer()
    spec.min_integration_time = 1.05
    spec.init()
    spec.set_integration_time(1.05)             # exactly at the floor is legal
    spec.set_integration_time(2.6439)


def test_the_real_class_refuses_a_sub_floor_exposure(monkeypatch):
    """The production path, not just the fake's mirror of it."""
    class Bare:
        m_Detector_m_NrPixels = 2048

    class Cfg:
        m_IntegrationTime = 2.0

    spec = _spectrometer_with(monkeypatch, Bare())
    spec.measconfig = Cfg()
    spec.serial_number = "SN-TEST-0001"
    spec.min_integration_ms = 1.05

    with pytest.raises(ValueError) as excinfo:
        spec.set_integration_time(0.022)
    assert "1.05" in str(excinfo.value)
    assert spec.measconfig.m_IntegrationTime == 2.0, "must not touch the device config"


def test_the_real_class_accepts_an_exposure_at_the_floor(monkeypatch):
    class Bare:
        m_Detector_m_NrPixels = 2048

    class Cfg:
        m_IntegrationTime = 2.0

    mod = sys.modules["spec_echem.spectrometer"]
    spec = _spectrometer_with(monkeypatch, Bare())
    spec.measconfig = Cfg()
    spec.min_integration_ms = 1.05
    monkeypatch.setattr(mod, "AVS_PrepareMeasure", lambda h, cfg: 0, raising=False)

    spec.set_integration_time(1.05)
    assert spec.measconfig.m_IntegrationTime == 1.05


def test_the_accepted_boundary_value_itself_is_refused():
    """MEASURED 2026-09-16 on a SensorType 10 part: at EXACTLY the smallest exposure
    AVS_PrepareMeasure accepts (1.04803466796875 ms) the detector takes the request and
    integrates ~2.1 ms -- about double. One step up, at 1.05 ms, counts are linear in
    exposure to within 1% out to 5 ms. So the bisect's boundary value is accepted but
    NOT honored, and rounding up off it is a safety property, not a display choice."""
    spec = FakeSpectrometer()
    spec.min_integration_time = 1.04803466796875
    spec.init()

    assert spec.min_integration_ms == 1.05                       # what we operate on
    assert spec.min_integration_measured_ms == 1.04803466796875  # what was measured

    with pytest.raises(ValueError):
        spec.set_integration_time(1.04803466796875)
    spec.set_integration_time(1.05)
