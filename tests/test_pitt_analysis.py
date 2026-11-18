"""PITT staircase analysis (spec_echem/pitt_analysis.py), end to end on the fake
series-RC cell, whose dQ/dE is known exactly: C at every step."""
import numpy as np
import pytest

from spec_echem import pitt_analysis as pa
from spec_echem.acquisition import acquire_pitt
from spec_echem.data import write_pitt_h5
from spec_echem.fakes import FakeEreisPotentiostat, FakeSpectrometer
from spec_echem.pitt import (END_FIXED, END_MAX_HOLD, END_STOPPED, ROLE_DEDOPE,
                             ROLE_FORWARD, ROLE_RETURN, pitt_plan)
from tests.test_pitt_run import Clock, ClockedSpectrometer, _written, settings

h5py = pytest.importorskip("h5py")

C = 1e-3
R = 1e3                                  # tau = 1 s; the first sample is at 0.05 s


def fixed(**over):
    return settings(**dict(dict(pitt_cutoff_enabled=False, pitt_max_hold_s=10.0), **over))


def test_an_rc_cell_gives_its_capacitance_once_the_head_is_added(tmp_path):
    _rec, path = _written(tmp_path, fixed(), r_series=R, c=C)
    res = pa.analyse_staircase(path)
    assert [r.index for r in res] == [0, 1, 2]
    assert res[0].c is None and "first step" in res[0].notes[0]
    for r in res[1:]:
        assert r.end_reason == END_FIXED and r.status == "ok", r.reasons
        assert r.de == pytest.approx(0.1)
        # The charge before the first sample (t0 = 0.05 s) is not sampled...
        t0 = 0.05
        assert r.c == pytest.approx(C * np.exp(-t0 / (R * C)), rel=0.01)
        # ...and the extrapolated head puts it back.
        assert r.c_head == pytest.approx(C, rel=0.005)
        assert r.q_head == pytest.approx(C * 0.1 * (1 - np.exp(-t0 / (R * C))), rel=0.05)
    assert res[1].e_mid == pytest.approx(0.05) and res[2].e_mid == pytest.approx(0.15)


def test_a_leak_is_taken_off_as_the_steps_own_tail_current(tmp_path):
    r_leak = 1e5
    # 20 s: at 10 s the 2 s tail still holds exp(-8) of the transient, 1.4% of I_ss.
    _rec, path = _written(tmp_path, fixed(pitt_max_hold_s=20.0), r_series=R, c=C,
                          r_leak=r_leak)
    res = pa.analyse_staircase(path)
    for r in res[1:]:
        assert r.i_ss == pytest.approx(r.e / r_leak, rel=1e-3)
        assert r.c_head == pytest.approx(C, rel=0.01)
        # The far bound (a background ramping from the previous step's leak) is
        # what u_background spans, and the truth lies inside it.
        assert abs(r.dq - C * 0.1 * np.exp(-0.05)) < r.q_u + 1e-9
        assert r.r_dc == pytest.approx(r_leak, rel=1e-3)


def test_the_film_size_gives_the_density_of_states(tmp_path):
    _rec, path = _written(tmp_path, fixed(), r_series=R, c=C)
    res = pa.analyse_staircase(path, area_cm2=1.0, thickness_nm=100.0)
    v = 1.0 * 100e-7
    r = res[1]
    assert r.c_chem == pytest.approx(r.c / v)
    assert r.g == pytest.approx(r.c / (pa.E_CHARGE_C * v))
    assert r.g_head == pytest.approx(r.c_head / (pa.E_CHARGE_C * v))
    assert r.energy_ev == pytest.approx(-r.e_mid)


def test_a_reversible_staircase_closes_and_retraces(tmp_path):
    _rec, path = _written(tmp_path, fixed(pitt_return=True), r_series=R, c=C)
    res = pa.analyse_staircase(path)
    assert [r.role for r in res] == [ROLE_FORWARD] * 3 + [ROLE_RETURN] * 2
    for r in res[3:]:
        assert r.de == pytest.approx(-0.1)
        assert r.c_head == pytest.approx(C, rel=0.005)        # positive on the way down
        assert r.status == "ok", r.reasons
    legs = pa.running_charge(res)
    e_f, q_f = legs[ROLE_FORWARD]
    assert q_f[0] == 0 and q_f[-1] == pytest.approx(2 * res[1].dq)
    hyst = pa.staircase_hysteresis(res)
    assert hyst["closure"] == pytest.approx(0.0, abs=1e-6)
    assert hyst["q_gap_fraction"] == pytest.approx(0.0, abs=1e-6)
    assert hyst["centre_shift_v"] is None              # 2 steps per leg: no fit
    assert pa.staircase_hysteresis(res[:3]) is None


def test_the_end_dedope_has_charge_but_no_dqde(tmp_path):
    _rec, path = _written(tmp_path, fixed(pitt_end_dedope=True, pitt_end_dedope_v=-0.1,
                                          pitt_end_dedope_time_s=5.0),
                          r_series=R, c=C)
    res = pa.analyse_staircase(path)
    last = res[-1]
    assert last.role == ROLE_DEDOPE and last.c is None and last.dq < 0
    assert any("end dedope" in n for n in last.notes)


def test_the_hold_window_stops_at_the_sweep_gap():
    t = np.r_[np.arange(99) * 0.1, 9.8 + 14.0, 9.8 + 14.1]
    keep = pa.hold_window(t)
    assert keep.sum() == 99 and not keep[-2:].any()
    assert pa.hold_window(np.arange(10) * 0.1).all()


def _row(**over):
    row = {"index": 1, "role": ROLE_FORWARD, "potential_set": 0.1,
           "end_reason": END_FIXED}
    row.update(over)
    return row


def _step(t, i, v, row=None, prev_e=0.0, prev_i=0.0):
    prev = pa.PittStepResult(index=0, e=prev_e, e_u=0.0, i_ss=prev_i)
    return pa.analyse_pitt_step(row or _row(), t, i, v, prev=prev)


T = np.arange(1, 101) * 0.1


def test_every_number_stays_when_a_check_objects():
    i = 1e-4 * np.exp(-T)
    v = np.full(T.size, 0.1)
    for reason, words in ((END_MAX_HOLD, "maximum hold"), (END_STOPPED, "lower bound")):
        r = _step(T, i, v, _row(end_reason=reason))
        assert r.status == "needs_review" and words in r.reasons[0]
        assert r.c is not None and r.dq is not None
    wrong = _step(T, -i, v)
    assert any("opposite sign" in x for x in wrong.reasons) and wrong.c < 0
    off = _step(T, i, v + 0.005)
    assert any("from the setpoint" in x for x in off.reasons)


def test_a_current_still_falling_at_the_end_is_flagged_but_noise_is_not():
    rng = np.random.default_rng(1)
    v = np.full(T.size, 0.1)
    slow = 1e-4 * np.exp(-T / 8.0)
    assert any("still changing" in x for x in _step(T, slow, v).reasons)
    # Settled, with noise of 2% of the step's typical current on the tail: the
    # slope is within its own scatter, so no concern.
    noisy = 1e-4 * np.exp(-T / 0.5) + 2e-7 * rng.normal(size=T.size)
    assert not any("still changing" in x for x in _step(T, noisy, v).reasons)


def test_a_large_head_is_named():
    t = 0.5 + np.arange(100) * 0.1              # first sample half a tau in
    r = _step(t, 1e-4 * np.exp(-t), np.full(t.size, 0.1))
    assert any("before the first sample" in x for x in r.reasons)
    assert r.c_head > r.c


def test_the_table_has_every_column_for_every_step(tmp_path):
    _rec, path = _written(tmp_path, fixed(), r_series=R, c=C)
    rows = pa.pitt_table_rows(pa.analyse_staircase(path))
    names = [n for n, _u in pa.TABLE_COLUMNS]
    assert all(list(row) == names for row in rows)
    assert rows[0]["C_F"] is None and rows[1]["C_F"] is not None
    assert rows[1]["status"] == "ok"


def test_a_swept_step_carries_its_impedance_and_leaves_the_sweep_out_of_dq(tmp_path):
    s = fixed(pitt_eis_enabled=True, pitt_eis_every_n=2, pitt_eis_f_high_hz=1e4,
              pitt_eis_f_low_hz=1.0, pitt_eis_points_per_decade=5)
    clock = Clock()
    pot = FakeEreisPotentiostat(s, point_s=0.5, clock=clock, r_series=R, c=C)
    spec = ClockedSpectrometer(clock)
    record = acquire_pitt(spec, pot, pitt_plan(s), s, clock=clock, sleep=clock.sleep)
    wl = FakeSpectrometer().wavelengths()[1]
    path = write_pitt_h5(record, np.full(wl.size, 100.0), np.full(wl.size, 4e4), wl,
                         tmp_path, "20261117_eis", settings=s)
    res = pa.analyse_staircase(path)
    swept = [r for r in res if r.sweep is not None]
    assert [r.index for r in swept] == [0, 2]
    assert swept[1].sweep["randles"]["params"]["cdl"] == pytest.approx(1e-6, rel=0.01)
    assert res[2].c_head == pytest.approx(C, rel=0.01)
    row = pa.pitt_table_rows(res)[2]
    assert row["Rs_ohm"] == pytest.approx(200.0, rel=0.01)


def test_a_noisy_tail_on_a_small_step_rarely_reads_as_a_trend():
    """On a small dQ, any noise slope carried over the step is 'more than 5%'. The
    2-sd condition is what keeps that from firing on most settled steps."""
    v = np.full(T.size, 0.1)
    flagged = 0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        i = 1e-8 * np.exp(-T / 0.5) + 1e-8 * rng.normal(size=T.size)
        flagged += any("still changing" in x for x in _step(T, i, v).reasons)
    assert flagged <= 4


def test_the_background_bound_runs_from_the_previous_steps_leak():
    v = np.full(T.size, 0.1)
    i = 1e-4 * np.exp(-T) + 2e-6
    r = _step(T, i, v, prev_i=1e-6)
    duration = T[-1] - T[0]
    assert r.u_background == pytest.approx(duration * (r.i_ss - 1e-6) / 4.0)
    assert r.dq_ramp == pytest.approx(r.q_raw - 0.5 * (1e-6 + r.i_ss) * duration)


def test_the_noise_term_matches_a_monte_carlo():
    v = np.full(T.size, 0.1)
    clean = 1e-5 * np.exp(-T)
    rng = np.random.default_rng(7)
    sigma = 1e-8
    draws = [_step(T, clean + sigma * rng.normal(size=T.size), v) for _ in range(400)]
    spread = np.std([r.dq for r in draws])
    assert np.median([r.u_noise for r in draws]) == pytest.approx(2 * spread, rel=0.15)
