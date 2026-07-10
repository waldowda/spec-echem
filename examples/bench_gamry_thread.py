"""
Test the two-thread design: run the Gamry curve's ENTIRE lifecycle on its own
dedicated thread (build -> init -> run -> clean poll -> acq_data), while a FAKE
spectrometer loop runs concurrently on the main thread. No spec_echem seam.

WHY: every same-thread variant (seam/nodig/digafter/reinit/allinfire/onlyfire)
kills the curve within ~50 ms — the curve dies the moment it shares a thread with
the spectrometer's measure/busy-wait loop. Only the clean bench pattern
(run, then an uninterrupted `while running(): sleep(0.1)` loop) survives. So the
fix is almost certainly: give the Gamry its own thread with its own clean loop.

This proves (or disproves) that. The Gamry thread owns toolkitpy entirely (init,
Pstat, curve, run, poll, acq_data, close all on that one thread — a clean COM
apartment). The main thread mimics the spectrometer with a busy-wait cadence like
the real acquire loop, to make sure that load doesn't disturb the Gamry thread.

Run on SpecEchem32 with the Gamry connected:
    python examples/bench_gamry_thread.py

Expect: the Gamry thread reports ~40 points and running() stays True on its
timeline, EVEN while the main-thread spectrometer loop is busy. That greenlights
the real refactor.
"""
import threading
import time

import toolkitpy as tkp
from spec_echem.potentiostat import initialize_pstat, MAX_CURVE_SIZE

CV_VERTICES = [0.0, -0.1, 0.1, 0.0]
SCAN_RATE_VPS = 0.1
STEP_V = 0.01
CYCLES = 1
N_SPECTRA = 41
DELTA_S = 0.1

result = {}
armed = threading.Event()   # main sets this once the "spectrometer" is armed


def gamry_thread():
    """Full curve lifecycle on THIS thread, with a clean poll loop."""
    tkp.toolkitpy_init("bench_gamry_thread")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)

    sample_time = STEP_V / SCAN_RATE_VPS
    curve = tkp.RcvCurve(pstat, MAX_CURVE_SIZE)
    signal = pstat.signal_r_up_dn_new(
        CV_VERTICES, [SCAN_RATE_VPS] * 3, [0.0, 0.0, 0.0], sample_time, CYCLES, tkp.PSTATMODE)
    pstat.set_signal_r_up_dn(signal)
    pstat.init_signal()

    armed.wait()  # wait until the "spectrometer" is armed (mirrors the handshake)
    pstat.set_cell(True)
    pstat.set_digital_out(0x1, 0x1)   # DIGOUT0 high (would trigger the Avantes)
    t0 = time.perf_counter()
    curve.run(True)

    timeline = []
    while tkp.pstat_is_valid(pstat) and curve.running():
        timeline.append((time.perf_counter() - t0, len(curve.acq_data())))
        time.sleep(0.1)
    elapsed = time.perf_counter() - t0

    pstat.set_digital_out(0x0, 0x1)   # DIGOUT0 low
    pstat.set_cell(False)
    data = curve.acq_data()
    result["n"] = len(data)
    result["elapsed"] = elapsed
    result["timeline"] = timeline
    tkp.toolkitpy_close()


def fake_spectrometer_load():
    """Mimic the real acquire loop's busy-wait cadence on the main thread."""
    for _ in range(N_SPECTRA):
        pretime1 = time.time_ns() / 1e9
        # (a real measure() would run here)
        time.sleep(0.002)
        check = time.time_ns() / 1e9
        while (check - pretime1) <= (DELTA_S - 0.0012):
            check = time.time_ns() / 1e9
            time.sleep(0.5e-3)


def main():
    t = threading.Thread(target=gamry_thread, name="gamry")
    t.start()
    time.sleep(0.2)          # let the Gamry thread build+init the signal
    armed.set()              # "spectrometer armed" -> Gamry fires
    fake_spectrometer_load()  # main thread busy, like the spectrometer
    t.join()

    print(f"gamry thread: {result.get('n')} points over {result.get('elapsed', 0):.2f}s")
    tl = result.get("timeline", [])
    if tl:
        print("  sample timeline (elapsed s -> acq points):")
        for el, n in tl[::max(1, len(tl) // 10)]:
            print(f"    {el:6.2f}s  {n}")
    print("Expect ~40 points and points climbing => two-thread design works.")


if __name__ == "__main__":
    main()
