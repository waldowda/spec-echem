"""The Gamry runner for a PITT with impedance sweeps, against a fake toolkit whose ReadZ behaves as the Reference
600's did on 2026-10-23: it autoranges and LEAVES ie_range two rungs down and the
I-channel filter changed (docs/bench-2026-10-26.md section 2)."""
import threading
import time

import numpy as np
import pytest

from spec_echem import ereis, potentiostat
from spec_echem.acquisition import acquire_pitt
from spec_echem.fakes import FakeSpectrometer
from spec_echem.pitt import pitt_plan
from spec_echem.settings import DEFAULT_SETTINGS

R_S, R_CT, C_DL = 200.0, 3010.0, 1e-6


def randles(f):
    w = 2 * np.pi * f
    return R_S + R_CT / (1 + 1j * w * R_CT * C_DL)


class _Sig:
    def __init__(self, period, v, repeats):
        self.period, self.v, self.repeats = period, v, repeats


class _Pstat:
    def __init__(self, tk):
        self.tk = tk
        self.signal = self.pending = None
        self.state = dict(ie_range=8, ich_range=3, ich_offset_enable=False,
                          ich_offset=0.0, ich_filter=5, vch_filter=5)
        for name in self.state:
            setattr(self, name, (lambda n=name: self.state[n]))
            setattr(self, "set_" + name, (lambda value, n=name: self._set(n, value)))

    def _set(self, name, value):
        if name == "ie_range" and self.tk.ignore_ie_range:
            return                                   # a write that does not take
        self.state[name] = value

    def set_ctrl_mode(self, m): pass
    def set_voltage(self, v): pass
    def set_cell(self, on): self.tk.events.append("cell on" if on else "cell off")
    def set_digital_out(self, value, mask):
        self.tk.events.append("edge high" if value else "edge low")
    def signal_array2_new(self, bias, cycles, period, values, repeats, bm, cm):
        return _Sig(period, values[0], repeats[0])
    def set_signal_array2(self, sig): self.pending = sig
    def init_signal(self): self.signal = self.pending
    def test_ich_filter(self, f): return 99
    def test_vch_filter(self, f): return 99
    def measure_temp(self): return 25.0


class _Curve:
    def __init__(self, tk, pstat):
        self.tk, self.pstat, self.n_final = tk, pstat, None

    def run(self, auto):
        self.sig = self.pstat.signal
        self.t0 = time.perf_counter()
        self.tk.active = self
        # what the range is as each hold curve starts: it must be the hold's again
        self.tk.events.append(("curve", self.sig.v, self.pstat.state["ie_range"],
                               self.pstat.state["ich_filter"]))

    def _n(self):
        return min(int((time.perf_counter() - self.t0) / self.sig.period),
                   self.sig.repeats)

    def running(self): return self.n_final is None and self._n() < self.sig.repeats
    def stop(self): self.n_final = self._n()

    def acq_data(self):
        n = self.n_final if self.n_final is not None else self._n()
        P = self.sig.period
        rows = [((k + 1) * P, self.sig.v, self.sig.v / (R_S + R_CT), 0) for k in range(n)]
        return np.array(rows, dtype=[("time", float), ("vf", float), ("im", float),
                                     ("overload", int)])


class _ReadZ:
    def __init__(self, tk, pstat):
        self.tk, self.pstat, self.set_calls, self.last = tk, pstat, {}, None
        for name in ("gain", "inoise", "vnoise", "ienoise", "vdc", "speed",
                     "drift_cor", "idc", "zmod"):
            setattr(self, "set_" + name,
                    (lambda value, n=name: self.set_calls.setdefault(n, []).append(value)))

    def measure(self, f, vac, vdc):
        if self.tk.active is not None and self.tk.active.running():
            self.tk.overlaps += 1                    # a hold curve still running
        self.tk.events.append(("z", f))
        time.sleep(self.tk.point_s)
        self.pstat.state["ie_range"] = 6             # left two rungs down
        self.pstat.state["ich_filter"] = 1
        if f in self.tk.fail_f:
            return False
        self.last = (f, randles(f))
        return True


class _ZCurve:
    def __init__(self, tk, n): self.rows = []
    def add_point(self, readz, temp):
        f, z = readz.last
        self.rows.append((f, z.real, z.imag, abs(z), temp))
    def acq_data(self):
        return np.array(self.rows, dtype=[("zfreq", float), ("zreal", float),
                                          ("zimag", float), ("zmod", float),
                                          ("temp", float)])


class _Tk:
    PSTATMODE, BIAS_NONE = 1, 0

    def __init__(self, point_s=0.02):
        self.point_s = point_s
        self.events, self.closed, self.fail_f = [], 0, set()
        self.ignore_ie_range = False
        self.readz = []
        self.active, self.overlaps = None, 0

    def toolkitpy_init(self, name): pass
    def toolkitpy_close(self): self.closed += 1
    def Pstat(self, name): return _Pstat(self)
    def ChronoCurve(self, pstat, n): return _Curve(self, pstat)
    def ReadZ(self, pstat):
        r = _ReadZ(self, pstat)
        self.readz.append(r)
        return r
    def ZCurve(self, n): return _ZCurve(self, n)
    def pstat_is_valid(self, pstat): return True


@pytest.fixture
def tk(monkeypatch):
    t = _Tk()
    monkeypatch.setattr(potentiostat, "tkp", t)
    monkeypatch.setattr(potentiostat, "TOOLKITPY_AVAILABLE", True)
    monkeypatch.setattr(potentiostat, "GAMRY_PITT_MIN_PERIOD_S", 0.01)
    monkeypatch.setattr(potentiostat, "initialize_pstat",
                        lambda pstat, r=6e-3, **kw: ("6 mA (fixed)", 6e-3))
    return t


def _settings(**over):
    s = dict(DEFAULT_SETTINGS, pitt_enabled=True, pitt_eis_enabled=True,
             pitt_start_v=0.0, pitt_stop_v=0.1, pitt_step_mv=100.0,
             pitt_cutoff_enabled=False, pitt_max_hold_s=0.1,
             pitt_eis_f_high_hz=1000.0, pitt_eis_f_low_hz=10.0,
             pitt_eis_points_per_decade=1, pitt_fast_s=0.05,
             pitt_slow_interval_s=0.05, chrono_delta_time=0.01)
    s.update(over)
    return s


def _run(s, **kw):
    p = potentiostat.ToolkitPotentiostat(s)
    record = acquire_pitt(FakeSpectrometer(), p, pitt_plan(s), s, **kw)
    return p, record


def test_the_gamry_can_run_one_and_the_autolab_cannot_yet():
    assert potentiostat.ereis_supported("python")
    assert not potentiostat.ereis_supported("autolab")
    assert not potentiostat.ereis_supported("external")


def test_each_sweep_runs_between_two_holds_with_the_cell_left_on(tk):
    _p, record = _run(_settings())
    assert record.completed and len(record.steps) == 2
    assert tk.overlaps == 0                      # no curve runs during a sweep
    flat = [e for e in tk.events if isinstance(e, str)]
    assert flat == ["cell on", "edge high", "edge low", "cell off"]
    zs = [e[1] for e in tk.events if isinstance(e, tuple) and e[0] == "z"]
    assert zs == pytest.approx([1000.0, 100.0, 10.0] * 2)
    for st in record.steps:
        assert st["sweep_end"] == "complete" and st["n_freq"] == 3
    rows = [p for p in record.impedance if p["step"] == 1]
    assert [r["zreal"] for r in rows] == pytest.approx(
        [randles(f).real for f in (1000.0, 100.0, 10.0)])
    assert all(r["temp"] == 25.0 and r["ok"] for r in rows)


def test_the_range_and_filters_are_put_back_before_the_hold_resumes(tk):
    """The hazard: ReadZ leaves ie_range two rungs down and a later hold clips with
    no error. Every hold curve must start on the hold's own range and filter."""
    _run(_settings())
    curves = [e for e in tk.events if isinstance(e, tuple) and e[0] == "curve"]
    assert len(curves) >= 4                     # each step: hold, then resumed hold
    assert {(rng, filt) for _c, _v, rng, filt in curves} == {(8, 5)}
    # and ReadZ really did move them: the fake is not hiding the hazard
    after_z = tk.events.index(("z", 10.0))
    assert tk.events[after_z + 1][0] == "curve"


def test_readz_is_set_up_as_the_probe_ran_it(tk):
    _run(_settings(pitt_eis_vac_mv=10.0))
    r = tk.readz[0]
    assert r.set_calls["gain"] == [1.0] and r.set_calls["speed"] == [1]
    assert r.set_calls["vdc"] == [0.0] and r.set_calls["drift_cor"] == [False]
    # |Z| guess: the default first, then the previous point's |Z|
    assert r.set_calls["zmod"][0] == potentiostat.GAMRY_ZMOD_GUESS_OHM
    assert r.set_calls["zmod"][1] == pytest.approx(abs(randles(1000.0)))
    # the second sweep starts from the first sweep's |Z| at the top frequency
    assert tk.readz[1].set_calls["zmod"][0] == pytest.approx(abs(randles(1000.0)))
    assert tk.readz[1].set_calls["vdc"] == [0.1]


def test_a_range_that_will_not_go_back_stops_the_run_with_the_cell_off(tk):
    tk.ignore_ie_range = True
    with pytest.raises(RuntimeError, match="could not be put back"):
        _run(_settings())
    assert tk.events[-1] == "cell off" and tk.closed == 1
    assert not any(isinstance(e, tuple) and e[0] == "curve" and e[2] != 8
                   for e in tk.events)          # no hold ever ran on the wrong range


def test_a_frequency_without_a_value_is_recorded_not_dropped(tk):
    tk.fail_f = {100.0}
    _p, record = _run(_settings())
    rows = [p for p in record.impedance if p["step"] == 0]
    assert [r["ok"] for r in rows] == [True, False, True]
    assert "zreal" not in rows[1]
    assert record.steps[0]["n_freq"] == 2


def test_stop_mid_sweep_lets_the_frequency_in_hand_finish(tk):
    tk.point_s = 0.08
    stop = threading.Event()
    s = _settings()
    threading.Timer(s["pitt_max_hold_s"] + 0.12, stop.set).start()
    _p, record = _run(s, stop_event=stop)
    assert not record.completed and len(record.steps) == 1
    assert record.steps[0]["sweep_end"] == "stopped"
    assert 1 <= record.steps[0]["n_freq"] < 3
    assert tk.events[-1] == "cell off"
