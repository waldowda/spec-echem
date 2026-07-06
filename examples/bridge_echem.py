"""
Bridge bench_gamry_thread.py (WORKS, 41 pts) -> the real capture seam (FAILS, 0 pts),
adding ONE real-seam element at a time to isolate what degenerates the curve.

The real ToolkitPotentiostat is used in every stage from 'A' on, so the Gamry
lifecycle is exactly the production one (prepare -> fire -> pump -> finish, dedicated
per-segment thread). What changes between stages is the MAIN-THREAD load that runs
concurrently, and how fire()/pump() are driven:

    stageA : real ToolkitPotentiostat, but the main-thread "spectrometer" is a pure
             busy-wait (NO numpy, NO acquire_segment). fire() before the loop, pump()
             each tick. If this WORKS, the pstat refactor is fine and the culprit is
             in acquire_segment/measure. If it FAILS, the culprit is prepare/fire/
             _run_segment or the handshake.

    stageB : same as A, but add numpy rng.normal(1265) per tick on the main thread
             (the one thing FakeSpectrometer.measure does that the bench busy-wait
             doesn't). If A works and B fails -> numpy on the main thread kills it.

    stageC : the REAL seam: run_one_segment + FakeSpectrometer. Sanity that this
             reproduces the 0-pt failure in-script.

Run:  python examples/bridge_echem.py stageA   (or stageB / stageC)
"""
import sys
import tempfile
import time

import numpy as np

import toolkitpy as tkp
from spec_echem.fakes import FakeSpectrometer
from spec_echem.experiment import Segment, run_one_segment
from spec_echem.settings import DEFAULT_SETTINGS
from spec_echem.data import DATA_TYPE_CV
from spec_echem.potentiostat import ToolkitPotentiostat, TOOLKITPY_AVAILABLE

if not TOOLKITPY_AVAILABLE:
    raise SystemExit("toolkitpy not importable — run this on SpecEchem32.")

CV_VERTICES = [0.0, -0.1, 0.1, 0.0]
N_POINTS = 41
DELTA_S = 0.1


def _cv_settings():
    s = DEFAULT_SETTINGS.copy()
    s.update(dict(
        cv_initial_v=CV_VERTICES[0], cv_limit1_v=CV_VERTICES[1],
        cv_limit2_v=CV_VERTICES[2], cv_final_v=CV_VERTICES[3],
        cv_step_size=10.0, cv_scan_rate=100.0, cv_cycles=1,
        save_dta=False, data_root=tempfile.mkdtemp(), data_folder="bridge",
    ))
    return s


def _busy_wait(delta_s, do_numpy):
    """One 'acquisition' tick: mimic acquire_segment's cadence on the main thread."""
    pretime1 = time.time_ns() / 1e9
    if do_numpy:
        rng = _busy_wait._rng
        _ = rng.normal(0.0, 6.0, 1265)
    time.sleep(0.002)
    check = time.time_ns() / 1e9
    while (check - pretime1) <= (delta_s - 0.0012):
        check = time.time_ns() / 1e9
        time.sleep(0.5e-3)


_busy_wait._rng = np.random.default_rng(seed=1)


def run_stage_ab(do_numpy, pre_fire_delay=0.0):
    """Real ToolkitPotentiostat, main-thread load = busy-wait (+/- numpy)."""
    settings = _cv_settings()
    seg = Segment("CV", DATA_TYPE_CV, 0, num_points=N_POINTS, delta_time=DELTA_S, trigger=False)

    pstat = ToolkitPotentiostat(settings)
    pstat.open()
    try:
        pstat.prepare(seg)                    # launch Gamry thread; blocks until built
        if pre_fire_delay:
            time.sleep(pre_fire_delay)        # runway between init_signal() and run()
        for j in range(N_POINTS):
            if j == 0:
                pstat.fire()                  # release the Gamry thread (arm edge)
            pstat.pump()                      # no-op in current impl, but call it anyway
            _busy_wait(DELTA_S, do_numpy)
        pstat.finish(aborted=False)
    finally:
        pstat.close()

    data = pstat.last_data()
    n = 0 if data is None else len(data)
    print(f"[stage{'B' if do_numpy else 'A'}] acq_data points: {n}")
    print(f"           ran_ok={pstat._ran_ok}  exit=[{pstat._exit_reason}]")
    tl = pstat._timeline
    if tl:
        step = max(1, len(tl) // 12)
        print("           timeline (s->pts): " + "  ".join(f"{t}:{c}" for t, c in tl[::step])
              + f"   [last {tl[-1][0]}s, {len(tl)} polls]")
    else:
        print("           timeline: EMPTY")
    print("Expect ~41 pts climbing." if n else "FAIL: curve produced no data.")


def run_stage_d(daemon, fire_in_loop, poll_acq, use_events, fire_margin=0.0, drop_signal=False):
    """
    Inline copy of the Gamry-thread body, launched through a prepare/fire-style
    harness I can mutate. Start = EXACT bench, then flip ONE knob at a time:
        daemon      : make the thread a daemon (real code does)
        fire_in_loop: set the arm event from INSIDE the busy loop at j==0
                      (real code fires from measure()) vs before the loop (bench)
        poll_acq    : call curve.acq_data() in the poll loop (real) vs just len (bench)
        use_events  : use two Events (built + armed) like prepare()/fire() (real)
                      vs bench's single module 'armed' + sleep(0.2)
    """
    import threading

    settings = _cv_settings()
    seg = Segment("CV", DATA_TYPE_CV, 0, num_points=N_POINTS, delta_time=DELTA_S, trigger=False)
    from spec_echem.potentiostat import initialize_pstat, MAX_CURVE_SIZE, _FIRE_ARM_MARGIN_S

    result = {}
    built = threading.Event()
    armed = threading.Event()
    STEP_V = 0.01
    SCAN_RATE_VPS = 0.1
    CYCLES = 1

    def gamry_body():
        tkp.toolkitpy_init("bridge_stage_d")
        pstat = tkp.Pstat("PSTAT")
        pstat.set_ctrl_mode(tkp.PSTATMODE)
        initialize_pstat(pstat)
        sample_time = STEP_V / SCAN_RATE_VPS

        def build():
            # Mimic the REAL code: signal is a local here, only the curve is
            # returned -> the signal object loses its last reference on return.
            c = tkp.RcvCurve(pstat, MAX_CURVE_SIZE)
            signal = pstat.signal_r_up_dn_new(
                CV_VERTICES, [SCAN_RATE_VPS] * 3, [0.0, 0.0, 0.0], sample_time, CYCLES, tkp.PSTATMODE)
            pstat.set_signal_r_up_dn(signal)
            pstat.init_signal()
            return c

        if drop_signal:
            curve = build()
            import gc
            gc.collect()   # force the dropped signal object to be finalized now
        else:
            curve = tkp.RcvCurve(pstat, MAX_CURVE_SIZE)
            signal = pstat.signal_r_up_dn_new(
                CV_VERTICES, [SCAN_RATE_VPS] * 3, [0.0, 0.0, 0.0], sample_time, CYCLES, tkp.PSTATMODE)
            pstat.set_signal_r_up_dn(signal)
            pstat.init_signal()
        if use_events:
            built.set()
        armed.wait()
        if fire_margin:
            time.sleep(fire_margin)
        pstat.set_cell(True)
        pstat.set_digital_out(0x1, 0x1)
        t0 = time.perf_counter()
        curve.run(True)
        result["ran_ok"] = curve.running()
        timeline = []
        while tkp.pstat_is_valid(pstat) and curve.running():
            d = curve.acq_data() if poll_acq else None
            timeline.append((round(time.perf_counter() - t0, 2),
                             len(d) if poll_acq else len(curve.acq_data())))
            time.sleep(0.1)
        pstat.set_digital_out(0x0, 0x1)
        pstat.set_cell(False)
        result["n"] = len(curve.acq_data())
        result["timeline"] = timeline
        tkp.toolkitpy_close()

    t = threading.Thread(target=gamry_body, name="gamry", daemon=daemon)
    t.start()
    if use_events:
        built.wait(timeout=30)
    else:
        time.sleep(0.2)
    if not fire_in_loop:
        armed.set()
    for j in range(N_POINTS):
        if fire_in_loop and j == 0:
            armed.set()
        _busy_wait(DELTA_S, do_numpy=False)
    t.join()

    print(f"[stageD daemon={daemon} fire_in_loop={fire_in_loop} poll_acq={poll_acq} "
          f"use_events={use_events}] n={result.get('n')} ran_ok={result.get('ran_ok')}")
    tl = result.get("timeline", [])
    if tl:
        step = max(1, len(tl) // 10)
        print("           timeline: " + "  ".join(f"{t_}:{c}" for t_, c in tl[::step]))
    print("Expect ~41 pts." if result.get("n") else "FAIL: no data.")


def run_stage_c():
    """The real seam, in-script, to confirm the 0-pt failure."""
    settings = _cv_settings()
    spec = FakeSpectrometer()
    spec.init()
    _, wl = spec.wavelengths()
    dark = np.full(len(wl), 100.0)
    _, ref = spec.measure()
    seg = Segment("CV", DATA_TYPE_CV, 0, num_points=N_POINTS, delta_time=DELTA_S, trigger=False)

    pstat = ToolkitPotentiostat(settings)
    pstat.open()
    try:
        run_one_segment(spec, seg, dark, ref, wl,
                        settings["data_root"], settings["data_folder"], potentiostat=pstat)
    finally:
        pstat.close()
    data = pstat.last_data()
    n = 0 if data is None else len(data)
    print(f"[stageC] acq_data points: {n}")
    print(f"           ran_ok={pstat._ran_ok}  exit=[{pstat._exit_reason}]")
    print(f"           timeline: {pstat._timeline}")


def main():
    stage = (sys.argv[1].lower() if len(sys.argv) > 1 else "stagea")
    print(f"== {stage} ==")
    if stage == "stagea":
        run_stage_ab(do_numpy=False)
    elif stage == "stagea_delay":
        run_stage_ab(do_numpy=False, pre_fire_delay=0.3)
    elif stage == "stageb":
        run_stage_ab(do_numpy=True)
    elif stage == "stagec":
        run_stage_c()
    elif stage == "d_bench":       # exact bench, through my harness -> expect 41
        run_stage_d(daemon=False, fire_in_loop=False, poll_acq=False, use_events=False)
    elif stage == "d_daemon":      # + daemon thread
        run_stage_d(daemon=True, fire_in_loop=False, poll_acq=False, use_events=False)
    elif stage == "d_fireloop":    # + fire from inside the loop at j==0
        run_stage_d(daemon=False, fire_in_loop=True, poll_acq=False, use_events=False)
    elif stage == "d_events":      # + built/armed Event handshake (like prepare/fire)
        run_stage_d(daemon=False, fire_in_loop=False, poll_acq=False, use_events=True)
    elif stage == "d_all":         # all real-like knobs together
        run_stage_d(daemon=True, fire_in_loop=True, poll_acq=True, use_events=True)
    elif stage == "d_margin":      # all real knobs + the 5ms fire-arm margin sleep
        run_stage_d(daemon=True, fire_in_loop=True, poll_acq=True, use_events=True,
                    fire_margin=0.005)
    elif stage == "d_dropsig":     # all real knobs + DROP the signal ref (real code's bug)
        run_stage_d(daemon=True, fire_in_loop=True, poll_acq=True, use_events=True,
                    drop_signal=True)
    else:
        raise SystemExit("stage must be stageA / stageB / stageC / d_*")


if __name__ == "__main__":
    main()
