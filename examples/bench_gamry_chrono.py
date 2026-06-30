"""
Bench check: ONE chronoamperometry hold via toolkitpy — no spectrometer, no GUI.

Purpose: verify the Gamry/toolkitpy chrono path in isolation, and ANSWER THE OPEN
QUESTION — does ``curve.run()`` block, or return immediately so you poll
``curve.running()``? This script reports which, by timing the call.

Run on the 32-bit Win11 box, in the SpecEchem32 env, with the Gamry connected:
    python examples/bench_gamry_chrono.py

This is the chrono half of doping/dedoping. It builds the same double-step signal
``spec_echem.potentiostat`` uses (a single hold = double-step with pre/step-2
times zeroed), matching the .GSequence Chronoamperometry element.
"""
import time

try:
    import toolkitpy as tkp
except ImportError:
    raise SystemExit(
        "toolkitpy not importable — run this on the 32-bit Win11 box (SpecEchem32 env)."
    )

from spec_echem.potentiostat import initialize_pstat

# --- edit these for your cell -------------------------------------------------
POTENTIAL_V = 0.1     # hold potential (V) — .GSequence doping uses DopingPotInitial
DURATION_S = 5.0      # hold time (s)      — .GSequence DopingDurationsec
SAMPLE_TIME_S = 0.1   # Gamry sample period (s) — .GSequence SAMPLETIME
# -----------------------------------------------------------------------------


def main():
    tkp.toolkitpy_init("bench_gamry_chrono")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)

    curve = tkp.ChronoCurve(pstat, 100000)
    signal = pstat.signal_d_step_new(
        POTENTIAL_V, 0.0,            # pre-step V, pre-step time
        POTENTIAL_V, DURATION_S,     # step 1 V, step 1 time (the hold)
        POTENTIAL_V, 0.0,            # step 2 V, step 2 time
        SAMPLE_TIME_S, tkp.PSTATMODE,
    )
    pstat.set_signal_d_step(signal)
    pstat.init_signal()

    pstat.set_cell(True)
    time.sleep(0.010)

    # --- the blocking-vs-polling probe ---
    t0 = time.perf_counter()
    curve.run(True)
    dt_run = time.perf_counter() - t0
    running_now = curve.running()
    print(f"\ncurve.run(True) returned after {dt_run*1000:.1f} ms; "
          f"curve.running()=={running_now} immediately after.")
    if running_now:
        print("  => run() is NON-BLOCKING; poll curve.running(). "
              "(This is what spec_echem.potentiostat assumes.)")
    else:
        print(f"  => run() BLOCKED until the segment finished (~{DURATION_S}s). "
              "Threading still works, but note this for timing.")

    polls = 0
    while tkp.pstat_is_valid(pstat) and curve.running():
        polls += 1
        time.sleep(0.1)
    total = time.perf_counter() - t0
    print(f"Polled curve.running() {polls}x; total {total:.2f}s "
          f"(expected ~{DURATION_S}s).")

    if tkp.pstat_is_valid(pstat):
        pstat.set_cell(False)

    data = curve.acq_data()
    print("\nacq_data keys:", list(data.keys()))
    n = len(data["time"]) if "time" in data else 0
    print(f"points: {n}")
    if n:
        print(f"first: time={data['time'][0]:.3f}s  im={data['im'][0]:.3e} A")
        print(f"last : time={data['time'][-1]:.3f}s  im={data['im'][-1]:.3e} A")

    tkp.toolkitpy_close()
    print("\ndone.")


if __name__ == "__main__":
    main()
