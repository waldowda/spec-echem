"""
bench_autolab_coacquire.py — spectrometer and Autolab in one process, for real.

Closes open item 5 of docs/autolab-run-api.md §4: the full rehearsal of what
`AutolabPotentiostat` will do for every segment.

    arm the Avantes for an external trigger
        -> proc.Measure()            (returns immediately; the driver owns timing)
            -> pulse Dio.DioPortsP1[0] during the procedure's WAIT window
                -> the armed detector fires
                    -> poll IsMeasuring to completion, read the echem trace

The number this exists to produce is the **SKEW**: how far apart the optical t=0
(the trigger pulse) and the echem t=0 (the first staircase point) actually land.
Everything else here is plumbing around that measurement. `CalcTime[0]` says when
the staircase began relative to the procedure starting, and we know when we pulsed,
so the difference is the misalignment — and the script prints the PULSE_DELAY_S
that would drive it to zero.

Why a wait window exists to pulse into: the standard CV runs an FHWait (~5 s)
before the staircase, so there is real room between Measure() and the
electrochemistry starting. Phase 0 reads that value off the procedure rather than
assuming it.

Ordering is the one rule that cannot bend: the pulse must come AFTER the Avantes is
armed. An edge raised before arming is silently missed — proven on the Gamry rig by
examples/diag_trigger_timing.py, and the reason acquisition.py fires on_armed from
INSIDE measure(). RUN_EARLY_PULSE_CONTROL below demonstrates that failure on this
hardware, deliberately.

    >> 10 kOhm dummy resistor, never a real sample. <<
    The spectra content does not matter here — only that scans land, and when.

Needs both stacks in the one 64-bit env: avaspec (query_avantes_setup.md) and
pythonnet + the Autolab SDK (query_autolab_setup.md). Close NOVA first.

Usage:
    python bench_autolab_coacquire.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

# --- what to run -----------------------------------------------------------
AVASPEC_DLL_DIR = r"C:\AvaSpecX64-DLL_9.14.0.0"
NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox"

ENERGIZE_CELL = False       # False = phase 0 only (both instruments opened, nothing run)
DIO_PORT_INDEX = 0          # 0 = P1.A
PULSE_WIDTH_S = 0.002
INTEGRATION_MS = 5.0        # keep above the detector floor (ULS2048L ~1.05 ms)
TRIGGER_TIMEOUT_S = 30.0

# When to pulse, measured from the Measure() call. None = use the procedure's own
# WAIT duration, which is what the driver should do. Set a number to override.
PULSE_DELAY_S = None

# Spectra to collect. 1 = the pure trigger test. More rehearses the real pattern:
# spectrum 0 is hardware-triggered, the rest free-run at DELTA_TIME_S, exactly as
# acquisition.py does (`on_armed if j == 0`).
NUM_SPECTRA = 1
DELTA_TIME_S = 0.1

RUN_EARLY_PULSE_CONTROL = False   # deliberately pulse BEFORE arming; expects a miss

# CV staircase, same indices as bench_autolab_cv.py.
CV_ID = "FHCyclicVoltammetry2"
WAIT_IDS = ("FHWait", "Wait time (s)")
IDX_START, IDX_UPPER, IDX_LOWER = 0, 1, 2
IDX_STEP, IDX_CROSSINGS, IDX_SCANRATE = 3, 4, 6
START_V, UPPER_V, LOWER_V = 0.0, 1.0, -1.0
STEP_V, SCAN_RATE_VS, CROSSINGS = 0.00244, 0.5, 2

# Avantes trigger config (see query_avantes_trigger.py, which proved this line).
TRIGGER_MODE_HARDWARE, TRIGGER_MODE_FREERUN = 1, 0
TRIGGER_SOURCE_EXTERNAL, TRIGGER_SOURCETYPE_EDGE = 0, 0

HERE = os.path.dirname(os.path.abspath(__file__))


# --- Avantes ---------------------------------------------------------------

def load_avaspec():
    if os.path.isdir(AVASPEC_DLL_DIR) and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(AVASPEC_DLL_DIR)
    try:
        import avaspec
        return avaspec
    except Exception as exc:  # noqa: BLE001
        say(f"Could not import 'avaspec': {exc}")
        say("On a fresh box the wrapper itself needs the vendored edits — see "
            "query_avantes_setup.md §2.")
        return None


def open_avantes(avaspec):
    if avaspec.AVS_Init(0) < 0:
        say("AVS_Init failed.")
        return None, None
    if avaspec.AVS_GetNrOfDevices() < 1:
        say("No spectrometer seen. Is AvaSoft or NOVA holding it?")
        return None, None
    ident = avaspec.AVS_GetList(1)[0]
    handle = avaspec.AVS_Activate(ident)
    if handle < 0:
        say(f"AVS_Activate failed (code {handle}).")
        return None, None
    try:
        pixels = avaspec.AVS_GetParameter(handle, 63484).m_Detector_m_NrPixels
    except Exception:  # noqa: BLE001
        pixels = len(avaspec.AVS_GetLambda(handle)) or 2048
    say(f"  Avantes open: {pixels} pixels")
    return handle, pixels


def measconfig(avaspec, pixels, trigger_mode):
    cfg = avaspec.MeasConfigType()
    cfg.m_StartPixel = 0
    cfg.m_StopPixel = pixels - 1
    cfg.m_IntegrationTime = INTEGRATION_MS
    cfg.m_IntegrationDelay = 0
    cfg.m_NrAverages = 1
    cfg.m_CorDynDark_m_Enable = 0
    cfg.m_CorDynDark_m_ForgetPercentage = 0
    cfg.m_Smoothing_m_SmoothPix = 0
    cfg.m_Smoothing_m_SmoothModel = 0
    cfg.m_SaturationDetection = 0
    cfg.m_Trigger_m_Mode = trigger_mode
    cfg.m_Trigger_m_Source = TRIGGER_SOURCE_EXTERNAL
    cfg.m_Trigger_m_SourceType = TRIGGER_SOURCETYPE_EDGE
    cfg.m_Control_m_StrobeControl = 0
    cfg.m_Control_m_LaserDelay = 0
    cfg.m_Control_m_LaserWidth = 0
    cfg.m_Control_m_LaserWaveLength = 0.0
    cfg.m_Control_m_StoreToRam = 0
    return cfg


def arm(avaspec, handle, pixels, trigger_mode):
    """Prepare + AVS_Measure. After this the device is waiting; the edge must come
    AFTER this returns or it is missed."""
    if avaspec.AVS_PrepareMeasure(handle, measconfig(avaspec, pixels, trigger_mode)) < 0:
        say("  AVS_PrepareMeasure failed.")
        return False
    if avaspec.AVS_Measure(handle, 0, 1) < 0:
        say("  AVS_Measure failed — NOT armed. The trigger must not be fired.")
        return False
    return True


def wait_for_scan(avaspec, handle, timeout):
    """-> (seconds waited, spectrum) or (None, None)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = avaspec.AVS_PollScan(handle)
        if r == 1:
            _, spectrum = avaspec.AVS_GetScopeData(handle)
            return time.time() - t0, spectrum
        if r < 0:
            say(f"  AVS_PollScan error (code {r}).")
            return None, None
        time.sleep(0.002)
    return None, None


# --- the rehearsal ---------------------------------------------------------

def wait_window(proc):
    """The procedure's own WAIT duration — the room the driver has to pulse in."""
    wait, used = ac.command(proc, *WAIT_IDS)
    if wait is None:
        return None
    val = safe(lambda: float(wait.CommandParameters[0].ValueAsObject))
    say(f"  WAIT command '{used}' duration = {val} s")
    return val


def build_cv(inst):
    proc = ac.load(inst, NOX)
    cv, _ = ac.command(proc, CV_ID, "CV staircase")
    if cv is None:
        return None, None, None
    ac.set_param(cv, IDX_START, START_V, "start V")
    ac.set_param(cv, IDX_UPPER, UPPER_V, "upper V")
    ac.set_param(cv, IDX_LOWER, LOWER_V, "lower V")
    ac.set_param(cv, IDX_STEP, STEP_V, "step V")
    ac.set_param(cv, IDX_CROSSINGS, CROSSINGS, "crossings")
    ac.set_param(cv, IDX_SCANRATE, SCAN_RATE_VS, "scan rate V/s")
    return proc, cv, wait_window(proc)


def coacquire(avaspec, handle, pixels, inst, port, proc, cv, delay):
    """The sequence the driver will run for every segment."""
    say("")
    say(f"  arming the Avantes (hardware trigger), then Measure(), "
        f"then pulsing at +{delay:.2f} s")
    if not arm(avaspec, handle, pixels, TRIGGER_MODE_HARDWARE):
        return None

    ac.switch_cell(inst, True)
    t_measure = time.time()
    proc.Measure()
    say(f"    Measure() returned at +{time.time() - t_measure:.3f} s "
        f"(IsMeasuring={safe(lambda: proc.IsMeasuring)})")

    while time.time() - t_measure < delay:
        time.sleep(0.005)
    t_pulse = time.time()
    ac.pulse(port, PULSE_WIDTH_S)
    pulse_at = t_pulse - t_measure
    say(f"    pulsed P1[{DIO_PORT_INDEX}] at +{pulse_at:.3f} s")

    waited, spectrum = wait_for_scan(avaspec, handle, TRIGGER_TIMEOUT_S)
    if waited is None:
        say("    NO SCAN — the edge did not reach the detector, or arrived early.")
    else:
        say(f"    scan landed {waited * 1000:.1f} ms after the pulse, "
            f"max {max(spectrum):.0f} counts")

    # Free-run continuation: spectrum 0 is triggered, the rest are not — the real
    # pattern from acquisition.py.
    extra = 0
    if spectrum is not None and NUM_SPECTRA > 1:
        say(f"    free-running {NUM_SPECTRA - 1} more spectra at {DELTA_TIME_S}s")
        for _ in range(NUM_SPECTRA - 1):
            if not arm(avaspec, handle, pixels, TRIGGER_MODE_FREERUN):
                break
            got, _sp = wait_for_scan(avaspec, handle, 5.0)
            if got is None:
                say("      a free-run scan did not complete.")
                break
            extra += 1
            time.sleep(DELTA_TIME_S)
        say(f"    collected {extra} free-run spectra")

    while safe(lambda: proc.IsMeasuring, False) is True:
        time.sleep(0.05)
    ac.switch_cell(inst, False)
    total = time.time() - t_measure
    say(f"    procedure finished at +{total:.2f} s")

    sig = ac.read_signals(cv)
    t = sig.get("CalcTime", [])
    echem_start = t[0] if t else None
    return {
        "pulse_at": pulse_at,
        "scan_landed": spectrum is not None,
        "scan_wait_ms": None if waited is None else waited * 1000,
        "echem_start": echem_start,
        "points": len(sig.get("EI_0.CalcCurrent", [])),
        "extra_spectra": extra,
        "signals": sig,
    }


def main():
    rule("AUTOLAB + AVANTES — CO-ACQUISITION REHEARSAL (item 5)")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    say(f"NUM_SPECTRA   : {NUM_SPECTRA}")
    if ENERGIZE_CELL:
        say("")
        say("*** The cell WILL be energized. 10 kOhm dummy resistor only. ***")

    avaspec = load_avaspec()
    if avaspec is None:
        return 0
    inst = ac.connect()
    if inst is None:
        say("Stopped: no Autolab. Nothing was energized.")
        return 0

    handle = port = None
    try:
        rule("PHASE 0 — open both instruments, read the wait window")
        handle, pixels = open_avantes(avaspec)
        if handle is None:
            return 1
        port = ac.open_dio(inst, DIO_PORT_INDEX)
        if port is None:
            return 1
        proc, cv, wait_s = build_cv(inst)
        if proc is None:
            say("  Could not find the CV staircase command.")
            return 1
        delay = PULSE_DELAY_S if PULSE_DELAY_S is not None else (wait_s or 5.0)
        say("")
        say(f"  Both instruments are open in ONE process. Pulse delay will be "
            f"{delay:.2f} s.")
        if wait_s is None:
            say("  NOTE: no WAIT command found — there is no built-in window between")
            say("  Measure() and the staircase, so the pulse delay is a guess.")

        if not ENERGIZE_CELL:
            say("")
            say("ENERGIZE_CELL is False — no run. Put the 10 kOhm in and set it True.")
            return 0

        rule("RUN — arm, Measure(), pulse, collect")
        r = coacquire(avaspec, handle, pixels, inst, port, proc, cv, delay)
        if r is None:
            return 1

        rule("RESULT — how well aligned are the two clocks?")
        start = r["echem_start"]
        start_txt = "?" if start is None else f"+{start:.3f} s (CalcTime[0])"
        say(f"  spectrum landed        : {r['scan_landed']}")
        say(f"  pulse sent at          : +{r['pulse_at']:.3f} s after Measure()")
        say(f"  echem first sample at  : {start_txt}")
        say(f"  echem points           : {r['points']}")
        if start is not None:
            skew = start - r["pulse_at"]
            say("")
            say(f"  >> SKEW = {skew * 1000:+.0f} ms "
                "(echem t=0 minus optical t=0)")
            say("     positive: the staircase started AFTER the spectrum")
            say(f"     to align them, set PULSE_DELAY_S = {start:.3f}")
            say("")
            say("     Both clocks are read on this PC, and Measure() itself takes")
            say("     ~0.3 s to return, so treat this as good to roughly a tenth of")
            say("     a second — enough to choose the delay, not a calibration.")
        if not r["scan_landed"]:
            say("")
            say("  The scan did NOT land. Check, in order: the pulse fell inside the")
            say("  wait window; DIO_PORT_INDEX is the wired port; polarity (try the")
            say("  falling edge); and that query_avantes_trigger.py still passes.")

        # The negative control: the ordering rule, demonstrated.
        if RUN_EARLY_PULSE_CONTROL:
            rule("CONTROL — pulse BEFORE arming (this is expected to MISS)")
            say("  Firing the edge first, then arming. If a scan still lands, the")
            say("  arm-then-fire rule does not hold here and acquisition.py's")
            say("  ordering assumption needs revisiting.")
            ac.pulse(port, PULSE_WIDTH_S)
            time.sleep(0.05)
            if arm(avaspec, handle, pixels, TRIGGER_MODE_HARDWARE):
                waited, _sp = wait_for_scan(avaspec, handle, 5.0)
                say(f"  scan after an early edge: "
                    f"{'LANDED — unexpected!' if waited is not None else 'missed, as expected'}")
                if waited is None:
                    ac.pulse(port, PULSE_WIDTH_S)   # release the armed device
                    wait_for_scan(avaspec, handle, 5.0)
    finally:
        try:
            if handle is not None:
                avaspec.AVS_Done()
        except Exception:  # noqa: BLE001
            pass
        if port is not None:
            ac.release_dio(port)
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)

    rule("NEXT")
    say("Record the skew and the chosen PULSE_DELAY_S in docs/autolab-run-api.md")
    say("§4 item 5. That delay is what AutolabPotentiostat.fire() will use.")
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_autolab_coacquire_report.txt"))
    sys.exit(code)
