"""
Standalone CV -> .dta smoke test. No spec_echem, no GUI — just toolkitpy.

Pins down the empty-.dta / empty-.txt bug: the toolkit curve only accumulates
data if curve.running() is polled DURING the run. Runs ONE mode per invocation
(fresh pstat each time — do NOT run several curves in one process, the leftover
state wedges the next run's curve.running()). Writes a .dta you can open in
Echem Analyst.

Usage on SpecEchem32 with the Gamry connected (open leads are fine — we only
care that data points EXIST and the .dta loads):

    python examples/standalone_cv_dta.py pumped        # baseline (= bench_gamry_cv): expect DATA
    python examples/standalone_cv_dta.py unpumped      # the GUI bug:               expect 0 points
    python examples/standalone_cv_dta.py interleaved   # the proposed fix:          expect DATA

Modes:
  pumped       start run, then `while running(): sleep(0.1)` — continuous poll.
               This is exactly what the working bench script does; proves the
               script + hardware acquire data at all.
  unpumped     start run, then sleep the whole duration with NO polling — what
               the GUI does today (spectrometer loop keeps the thread busy).
  interleaved  the fix: one thread, pump curve.running() in the GAP between
               simulated spectra (sleep = 'collect a spectrum', then a quick
               poll). Prints the achieved cadence so you can see the pump does
               not disturb the ~delta_time spacing.

File written to the current directory: standalone_CV_<MODE>.dta
"""
import os
import sys
import time
import toolkitpy as tkp

# CV vertices — small + safe on open leads (matches the .GSequence shape)
INITIAL_V, LIMIT1_V, LIMIT2_V, FINAL_V = 0.0, -0.1, 0.1, 0.0
SCAN_RATE_VPS = 0.1      # V/s
STEP_SIZE_V = 0.01       # V
CYCLES = 1
DELTA_S = 0.1            # simulated seconds between spectra (the acquisition cadence)

MODES = ("pumped", "unpumped", "interleaved")


def initialize_pstat(pstat):
    """Advanced Pstat Setup — copied verbatim from the bundled examples."""
    pstat.set_ach_select(tkp.ACHSELECT_GND)
    pstat.set_ie_stability(tkp.STABILITY_NORM)
    pstat.set_ca_speed(tkp.CASPEED_NORM)
    pstat.set_ground(tkp.FLOAT)
    pstat.set_ich_range(3.0)
    pstat.set_ich_range_mode(False)
    pstat.set_ich_offset_enable(False)
    pstat.set_vch_range(10.0)
    pstat.set_vch_range_mode(True)
    pstat.set_vch_offset_enable(False)
    pstat.set_ach_range(3.0)
    pstat.set_ie_range_lower_limit(0)
    pstat.set_pos_feed_enable(False)
    pstat.set_analog_out(0.0)
    pstat.set_voltage(0.0)
    pstat.set_pos_feed_resistance(0.0)


def build_cv_curve(pstat):
    """Curve created FIRST, then the signal built + initialized (vendor order)."""
    sample_time = STEP_SIZE_V / SCAN_RATE_VPS
    curve = tkp.RcvCurve(pstat, 200000)
    signal = pstat.signal_r_up_dn_new(
        [INITIAL_V, LIMIT1_V, LIMIT2_V, FINAL_V],
        [SCAN_RATE_VPS, SCAN_RATE_VPS, SCAN_RATE_VPS],
        [0.0, 0.0, 0.0],
        sample_time, CYCLES, tkp.PSTATMODE,
    )
    pstat.set_signal_r_up_dn(signal)
    pstat.init_signal()
    return curve


def _cv_seconds():
    path = (abs(INITIAL_V - LIMIT1_V) + abs(LIMIT1_V - LIMIT2_V)
            + abs(LIMIT2_V - FINAL_V))
    return path / SCAN_RATE_VPS + 1.0   # + margin


def run(pstat, mode):
    curve = build_cv_curve(pstat)
    pstat.set_cell(True)
    time.sleep(0.010)

    t0 = time.perf_counter()
    curve.run(True)
    print(f"  running() right after run(True): {curve.running()}  "
          f"(waveform should take ~{_cv_seconds() - 1.0:.1f}s)")

    ticks = []
    if mode == "pumped":
        while tkp.pstat_is_valid(pstat) and curve.running():
            time.sleep(0.1)
    elif mode == "unpumped":
        time.sleep(_cv_seconds())
    elif mode == "interleaved":
        last = time.perf_counter()
        while tkp.pstat_is_valid(pstat) and curve.running():
            time.sleep(DELTA_S)         # stand-in for spec.measure() (~one spectrum)
            curve.running()            # <-- the pump: quick poll in the idle gap
            now = time.perf_counter()
            ticks.append(now - last)
            last = now

    elapsed = time.perf_counter() - t0
    if tkp.pstat_is_valid(pstat):
        pstat.set_cell(False)
    print(f"  run window: {elapsed:.2f}s")
    if ticks:
        print(f"  cadence: {len(ticks)} ticks, target {DELTA_S:.3f}s, "
              f"mean {sum(ticks) / len(ticks):.4f}s, max {max(ticks):.4f}s")
    return curve


def report(tag, curve, pstat):
    data = curve.acq_data()
    n = len(data)
    print(f"[{tag}] acq_data points: {n}")
    if n:
        names = data.dtype.names or ()
        if "vf" in names and "im" in names:
            print(f"      vf {data['vf'].min():.3f}..{data['vf'].max():.3f} V | "
                  f"im {data['im'].min():.2e}..{data['im'].max():.2e} A")
    path = os.path.abspath(f"standalone_CV_{tag}.dta")
    try:
        tkp.print_default_dta_file(curve, pstat, path, "CV")
        print(f"      wrote {path}")
    except Exception as exc:
        print(f"      .dta write FAILED: {exc}")


def main():
    mode = (sys.argv[1].lower() if len(sys.argv) > 1 else "pumped")
    if mode not in MODES:
        raise SystemExit(f"mode must be one of {MODES}; got {mode!r}")
    print(f"== mode: {mode} ==")

    tkp.toolkitpy_init("standalone_cv_dta")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)
    try:
        report(mode.upper(), run(pstat, mode), pstat)
    finally:
        tkp.toolkitpy_close()


if __name__ == "__main__":
    main()
