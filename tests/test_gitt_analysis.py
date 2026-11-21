"""GITT analysis (spec_echem/gitt_analysis.py): synthetic steps whose answers are
known exactly, then end to end on the fake series-RC cell."""
import math

import numpy as np
import pytest

from spec_echem import gitt_analysis as ga
from spec_echem.gitt import END_E_LIMIT, END_FIXED, ROLE_REST, ROLE_TITRATION

I_SET = 5e-6
R = 250.0
C = 3e-3
DT = 0.1


def _rest(e0=0.30, drift=0.0, n=301):
    t = np.arange(n) * DT
    row = dict(index=0, role=ROLE_REST, current_set_A=0.0, pulse_s=0.0,
               end_reason=END_FIXED)
    return row, t, np.zeros(n), e0 + drift * t


def _pulse(e_start, index=1, pulse_s=10.0, relax_s=30.0, k_sqrt=0.0, drift=0.0,
           end=END_FIXED):
    """A pulse on a series R + C film (plus an optional sqrt(t) term), then 0 A with
    an optional linear drift. Samples at 0.05, 0.15, ... as the rigs take them."""
    t = np.arange(0.05, pulse_s + relax_s, DT)
    on = t < pulse_s
    i = np.where(on, I_SET, 0.0)
    q = I_SET * np.minimum(t, pulse_s)
    v = e_start + q / C + np.where(on, I_SET * R + k_sqrt * np.sqrt(t), 0.0)
    v = v + np.where(on, 0.0, drift * (t - pulse_s))
    row = dict(index=index, role=ROLE_TITRATION, current_set_A=I_SET, pulse_s=pulse_s,
               pulse_s_applied=pulse_s, end_reason=end)
    return row, t, i, v


def _prev(e):
    return ga.GittStepResult(e_relaxed=e, q_total=0.0)


def test_a_series_rc_step_gives_c_and_r():
    row, t, i, v = _pulse(0.30)
    r = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30))
    q = I_SET * 10.0
    assert r.q == pytest.approx(q, rel=0.01)
    assert r.q_set == pytest.approx(q)
    assert r.de_s == pytest.approx(q / C, rel=0.01)
    assert r.c == pytest.approx(C, rel=0.02)
    # The switch-off step is I R, with the pulse carried to its applied end.
    assert r.r_off == pytest.approx(R, rel=0.01)
    assert r.drift == pytest.approx(0.0, abs=1e-9)
    assert r.i_leak == pytest.approx(0.0, abs=1e-12)
    # A capacitor charges linearly in t, not in sqrt(t): the one concern is right.
    assert len(r.reasons) == 1 and "sqrt(t)" in r.reasons[0], r.reasons


def test_the_charge_accumulates_and_the_start_is_the_previous_relaxed_e():
    rest = ga.analyse_gitt_step(*_rest(0.30))
    assert rest.c is None and rest.e_relaxed == pytest.approx(0.30)
    row, t, i, v = _pulse(0.30)
    one = ga.analyse_gitt_step(row, t, i, v, prev=rest)
    row, t, i, v = _pulse(one.e_relaxed, index=2)
    two = ga.analyse_gitt_step(row, t, i, v, prev=one)
    assert one.e_start == pytest.approx(0.30)
    assert two.e_start == pytest.approx(one.e_relaxed)
    assert two.q_total == pytest.approx(2 * I_SET * 10.0, rel=0.01)


def test_a_drifting_relaxation_is_a_bound_and_its_leak_is_estimated():
    """2026-11-20 on a film: E fell 55-190 uV/s at 0 A. The relaxed E is a bound, and
    -C dE/dt is the leak, given beside C and never folded into it."""
    drift = -150e-6
    row, t, i, v = _pulse(0.30, drift=drift)
    r = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30))
    assert r.drift == pytest.approx(drift, rel=0.01)
    assert r.i_leak == pytest.approx(-r.c * drift, rel=1e-9)
    assert r.i_leak > 0
    assert r.q_leak == pytest.approx(r.i_leak * r.hold_s, rel=1e-9)
    assert r.c_leak == pytest.approx((r.q - r.q_leak) / r.de_s, rel=1e-9)
    assert r.c_leak < r.c
    assert any("not relaxed" in s for s in r.reasons)


def test_a_steady_relaxation_raises_no_drift_concern():
    row, t, i, v = _pulse(0.30, drift=-1e-6)
    r = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30))
    assert not any("not relaxed" in s for s in r.reasons)


def test_weppner_huggins_d_and_its_validity():
    """D = 4 L^2 / (pi tau) (dE_s / dE_t)^2, flagged when tau is not << L^2/D."""
    row, t, i, v = _pulse(0.30, pulse_s=1.0, relax_s=10.0, k_sqrt=0.02)
    r = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30), thickness_nm=1000.0)
    L = 1000e-7
    expect = 4 * L ** 2 / (math.pi * r.pulse_s) * (r.de_s / r.de_t) ** 2
    assert r.d == pytest.approx(expect, rel=1e-9)
    assert r.tau_limit_s == pytest.approx(L ** 2 / r.d, rel=1e-9)
    assert r.sqrt_r2 > 0.99
    valid = r.pulse_s <= ga.WH_TAU_FRACTION * r.tau_limit_s
    assert valid == (not any("not <<" in s for s in r.reasons))
    # With no thickness there is no D, and nothing is invented.
    r2 = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30))
    assert r2.d is None and r2.tau_limit_s is None


def test_a_long_pulse_on_a_thin_film_says_d_is_not_valid():
    row, t, i, v = _pulse(0.30, k_sqrt=0.005)
    r = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30), thickness_nm=150.0)
    assert r.d is not None
    assert any("D is not valid" in s for s in r.reasons)


def test_the_run_stopping_at_the_window_is_a_concern():
    row, t, i, v = _pulse(0.30, end=END_E_LIMIT)
    r = ga.analyse_gitt_step(row, t, i, v, prev=_prev(0.30))
    assert r.status == "needs_review"
    assert any("window" in s for s in r.reasons)


def test_a_charge_far_from_the_set_one_is_a_concern():
    row, t, i, v = _pulse(0.30)
    r = ga.analyse_gitt_step(row, t, 0.7 * i, v, prev=_prev(0.30))
    assert any("not the set" in s for s in r.reasons)


def test_the_table_has_every_column_for_every_step():
    rest = ga.analyse_gitt_step(*_rest())
    row, t, i, v = _pulse(0.30)
    rows = ga.gitt_table_rows([rest, ga.analyse_gitt_step(row, t, i, v, prev=rest)])
    assert [list(r) for r in rows] == [ga.TABLE_COLUMNS] * 2
    assert rows[0]["C_F"] is None and rows[1]["C_F"] is not None


# --- End to end on the fake cell ----------------------------------------------------

def test_the_fake_rc_cell_reads_back_its_capacitance_and_resistance(tmp_path):
    pytest.importorskip("h5py")
    from tests.test_gitt_run import _run, quick
    _steps, path, _record = _run(tmp_path, quick())
    res = ga.analyse_gitt(path)
    assert [r.role for r in res] == [ROLE_REST, ROLE_TITRATION, ROLE_TITRATION]
    for r in res[1:]:
        assert r.c == pytest.approx(1e-3, rel=0.15), r.reasons   # the fake's C
        assert r.r_off == pytest.approx(1e3, rel=0.15)           # and its R
