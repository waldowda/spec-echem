"""
Bench check: ONE cyclic voltammetry scan via toolkitpy — no spectrometer, no GUI.

Purpose: verify the Gamry/toolkitpy CV path in isolation and confirm the vertex /
scan-rate / sample-time mapping before wiring CV into the GUI. The default
parameters below are the EXACT effective values from the .GSequence CV element
(after its bound variables resolve):

    VINIT   = 0.0     (Initial E)
    VLIMIT1 = -0.5    (Scan Limit 1  <- CVScanLimit1)
    VLIMIT2 =  0.7    (Scan Limit 2  <- CVScanLimit2)
    VFINAL  = 0.0     (Final E)
    SCANRATE = 1000 mV/s = 1.0 V/s
    STEPSIZE = 100 mV  = 0.1 V    -> sample_time = step/rate = 0.1 s
    CYCLES   = 1

Run on the 32-bit Win11 box, in SpecEchem32, with the Gamry connected:
    python examples/bench_gamry_cv.py
"""
import time

try:
    import toolkitpy as tkp
except ImportError:
    raise SystemExit(
        "toolkitpy not importable — run this on the 32-bit Win11 box (SpecEchem32 env)."
    )

from spec_echem.potentiostat import initialize_pstat

# --- edit these for your cell (defaults = .GSequence CV) ----------------------
INITIAL_V = 0.0
LIMIT1_V = -0.5
LIMIT2_V = 0.7
FINAL_V = 0.0
SCAN_RATE_VPS = 1.0     # V/s  (1000 mV/s)
STEP_SIZE_V = 0.1       # V    (100 mV)
CYCLES = 1
# -----------------------------------------------------------------------------


def main():
    tkp.toolkitpy_init("bench_gamry_cv")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)

    sample_time = STEP_SIZE_V / SCAN_RATE_VPS
    print(f"sample_time = step/rate = {STEP_SIZE_V}/{SCAN_RATE_VPS} = {sample_time:.4f} s")

    curve = tkp.RcvCurve(pstat, 200000)
    signal = pstat.signal_r_up_dn_new(
        [INITIAL_V, LIMIT1_V, LIMIT2_V, FINAL_V],   # vertices
        [SCAN_RATE_VPS, SCAN_RATE_VPS, SCAN_RATE_VPS],  # one rate per leg
        [0.0, 0.0, 0.0],                             # apex1 / apex2 / final holds
        sample_time, CYCLES, tkp.PSTATMODE,
    )
    pstat.set_signal_r_up_dn(signal)
    pstat.init_signal()

    pstat.set_cell(True)
    time.sleep(0.010)   # brief settle; the bundled example precharges 10s — add if your cell needs it

    t0 = time.perf_counter()
    curve.run(True)
    print(f"curve.running() immediately after run(True): {curve.running()}")
    polls = 0
    while tkp.pstat_is_valid(pstat) and curve.running():
        polls += 1
        time.sleep(0.1)
    total = time.perf_counter() - t0
    print(f"polled {polls}x; total {total:.2f}s")

    if tkp.pstat_is_valid(pstat):
        pstat.set_cell(False)

    data = curve.acq_data()   # numpy structured array (fields via .dtype.names)
    names = getattr(getattr(data, "dtype", None), "names", None)
    print("\nacq_data type:", type(data).__name__, "| fields:", names)
    n = len(data)
    print(f"points: {n}")
    if n:
        print("first row:", data[0])
        print("last row :", data[-1])
        if names and "vf" in names:
            print(f"vf range: {data['vf'].min():.3f} .. {data['vf'].max():.3f} V")
        if names and "im" in names:
            print(f"im range: {data['im'].min():.3e} .. {data['im'].max():.3e} A")

    tkp.toolkitpy_close()
    print("\ndone.")


if __name__ == "__main__":
    main()
