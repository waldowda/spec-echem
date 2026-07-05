"""
Standalone CV -> .dta smoke test. No spec_echem, no GUI — just toolkitpy.

Pins down the empty-.dta / empty-.txt bug: the toolkit curve only accumulates
data if curve.running() is polled DURING the run. Runs the SAME CV two ways and
writes a .dta for each so you can open them in Echem Analyst:

  A) UNPUMPED    — start the run, then sleep the whole duration WITHOUT polling
                   the curve (this is what the GUI does today, because the
                   spectrometer loop keeps the worker thread busy). Expect
                   acq_data() EMPTY and an unreadable .dta ("No CURVE found").

  B) INTERLEAVED — the proposed fix: one thread, pump curve.running() in the GAP
                   between simulated spectra (sleep = "collect a spectrum", then
                   a quick running() poll). Expect acq_data() FULL, a readable
                   .dta, AND a preserved inter-"spectrum" cadence (printed) — so
                   you can see the pump does NOT disturb the ~delta_time spacing.

Open leads are fine (values will be floating/noise; we only care that data
points EXIST and the .dta loads). Run on SpecEchem32 with the Gamry connected:

    python examples/standalone_cv_dta.py

Files are written to the current directory: standalone_CV_UNPUMPED.dta and
standalone_CV_INTERLEAVED.dta.
"""
import os
import time
import toolkitpy as tkp

# CV vertices — small + safe on open leads (matches the .GSequence shape)
INITIAL_V, LIMIT1_V, LIMIT2_V, FINAL_V = 0.0, -0.1, 0.1, 0.0
SCAN_RATE_VPS = 0.1      # V/s
STEP_SIZE_V = 0.01       # V
CYCLES = 1
DELTA_S = 0.1            # simulated seconds between spectra (the acquisition cadence)


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


def run_unpumped(pstat):
    """Start the run, then sleep the whole duration WITHOUT polling — the bug."""
    curve = build_cv_curve(pstat)
    pstat.set_cell(True)
    time.sleep(0.010)
    curve.run(True)
    time.sleep(_cv_seconds())           # worker 'busy elsewhere', curve unattended
    if tkp.pstat_is_valid(pstat):
        pstat.set_cell(False)
    return curve


def run_interleaved(pstat):
    """The fix: pump curve.running() in the gap between simulated spectra, one
    thread. Also records the achieved inter-'spectrum' cadence."""
    curve = build_cv_curve(pstat)
    pstat.set_cell(True)
    time.sleep(0.010)
    curve.run(True)

    ticks = []
    last = time.perf_counter()
    while tkp.pstat_is_valid(pstat) and curve.running():
        time.sleep(DELTA_S)             # stand-in for spec.measure() (~one spectrum)
        curve.running()                # <-- the pump: quick poll in the idle gap
        now = time.perf_counter()
        ticks.append(now - last)
        last = now
    if tkp.pstat_is_valid(pstat):
        pstat.set_cell(False)

    if ticks:
        mx = max(ticks)
        avg = sum(ticks) / len(ticks)
        print(f"      cadence: {len(ticks)} ticks, target {DELTA_S:.3f}s, "
              f"mean {avg:.4f}s, max {mx:.4f}s")
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
    tkp.toolkitpy_init("standalone_cv_dta")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)
    try:
        print("== A) UNPUMPED (sleep, no polling — reproduces the GUI bug) ==")
        report("UNPUMPED", run_unpumped(pstat), pstat)
        print("\n== B) INTERLEAVED (pump between 'spectra', one thread — the fix) ==")
        report("INTERLEAVED", run_interleaved(pstat), pstat)
    finally:
        tkp.toolkitpy_close()
    print("\nExpected: UNPUMPED = 0 points / empty .dta; INTERLEAVED = many points / "
          "loadable .dta, with cadence ~= target (pump doesn't disturb timing).")


if __name__ == "__main__":
    main()
