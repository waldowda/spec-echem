"""
Diagnostic: does the Avantes catch a DIGOUT0 trigger edge that arrives BEFORE
AVS_Measure() arms the device?

This settles the open question from the co-acquisition review. The new Python-mode
path fires DIGOUT0 (in the potentiostat's start_segment) *before* the acquire loop
calls AVS_Measure(). The legacy notebook / External mode always fired the trigger
*after* measure() was already armed and waiting. Edge triggers normally require the
device to be armed before the edge — so the new order may miss spectrum 0. Rather
than argue it, measure it.

Fully programmatic — prints a truth table, no scope/visual needed. Run in the
32-bit SpecEchem32 env (needs BOTH avaspec + toolkitpy). No cell / no
electrochemistry: it only toggles DIGOUT0 and arms/measures the spectrometer.
Wiring is your existing setup: DIGOUT0 (Gamry pin 7) -> Avantes trigger in, gnd pin 6.
Point the spectrometer at anything; we only care whether a scan COMPLETES.

Three scenarios:
  A  edge trigger, fire DIGOUT0 BEFORE arming   <- the new-code case (the concern)
  B  edge trigger, fire DIGOUT0 AFTER arming    <- your legacy / External order (control)
  C  level trigger, fire DIGOUT0 BEFORE arming  <- the simplest proposed fix

How to read it (printed at the end):
  - B must be ACQUIRED, or the rig/wiring is suspect and A/C mean nothing.
  - A ACQUIRED            -> a pre-arm edge IS caught; current Python order is fine.
  - A MISSED, B ACQUIRED  -> concern CONFIRMED: fire the edge only after arming.
  - C ACQUIRED            -> level trigger fixes the before-arm case (one-line fix).
"""
import threading
import time

try:
    import toolkitpy as tkp
except ImportError:
    raise SystemExit("toolkitpy not importable — run this in the 32-bit SpecEchem32 env.")

from spec_echem.spectrometer import AvantesSpectrometer

try:
    from avaspec import AVS_StopMeasure   # best-effort clean stop between scenarios
except Exception:  # noqa: BLE001
    AVS_StopMeasure = None

TIMEOUT_S = 3.0        # wait this long for a triggered scan before calling it "missed"
INTEGRATION_MS = 1.0   # short so a caught trigger returns quickly
AVERAGES = 1


def digout0(pstat, high):
    pstat.set_digital_out(0x1 if high else 0x0, 0x1)


def measure_with_timeout(spec, timeout_s):
    """Return the spectrum, or None if no trigger arrived within timeout_s."""
    ev = threading.Event()
    timer = threading.Timer(timeout_s, ev.set)
    timer.start()
    try:
        return spec.measure(ev)   # spec.measure returns None if ev is set during the poll
    finally:
        timer.cancel()


def outcome(result):
    return "ACQUIRED" if result is not None else "MISSED (timeout)"


def reset(spec, pstat):
    """Return to a known state between scenarios: drain any pending edge-armed
    measure, stop, disarm, DIGOUT0 low."""
    digout0(pstat, False); time.sleep(0.05)
    digout0(pstat, True);  time.sleep(0.05)   # a real edge, in case a measure is still armed
    digout0(pstat, False); time.sleep(0.05)
    if AVS_StopMeasure is not None:
        try:
            AVS_StopMeasure(spec.dev_handle)
        except Exception:  # noqa: BLE001
            pass
    spec.set_trigger_mode(0)
    time.sleep(0.2)


def fire_before_arm(spec, pstat, source_type, label):
    """Raise DIGOUT0 (the edge) with nothing measuring, THEN arm + measure."""
    digout0(pstat, False)
    time.sleep(0.1)
    spec.set_source_type(source_type)
    spec.set_trigger_mode(1)
    digout0(pstat, True)          # trigger happens NOW, before AVS_Measure()
    time.sleep(0.05)
    result = measure_with_timeout(spec, TIMEOUT_S)
    print(f"  {label}: {outcome(result)}")
    return result is not None


def fire_after_arm(spec, pstat, label):
    """Arm + measure first (edge trigger); raise DIGOUT0 while it's waiting (control)."""
    digout0(pstat, False)
    time.sleep(0.1)
    spec.set_source_type(0)       # edge
    spec.set_trigger_mode(1)
    timer = threading.Timer(0.5, lambda: digout0(pstat, True))  # edge lands mid-wait
    timer.start()
    try:
        result = measure_with_timeout(spec, TIMEOUT_S)
    finally:
        timer.cancel()
    print(f"  {label}: {outcome(result)}")
    return result is not None


def main():
    tkp.toolkitpy_init("diag-trigger-timing")
    pstat = tkp.Pstat("PSTAT")

    spec = AvantesSpectrometer()
    spec.init()
    spec.set_integration_time(INTEGRATION_MS)
    spec.set_scan_averages(AVERAGES)

    print("\nTrigger-timing diagnostic (DIGOUT0 -> Avantes). Each line waits up to "
          f"{TIMEOUT_S:.0f}s.\n")
    b = fire_after_arm(spec, pstat,      "B  edge,  fire AFTER  arm  (legacy/control)")
    reset(spec, pstat)
    a = fire_before_arm(spec, pstat, 0,  "A  edge,  fire BEFORE arm  (new-code case) ")
    reset(spec, pstat)
    c = fire_before_arm(spec, pstat, 1,  "C  level, fire BEFORE arm  (proposed fix)  ")
    reset(spec, pstat)

    print("\nInterpretation:")
    if not b:
        print("  ⚠ CONTROL FAILED: B MISSED — a known-good edge wasn't caught. Check the")
        print("    DIGOUT0->Avantes wiring / trigger input; A and C aren't trustworthy yet.")
    else:
        if a:
            print("  A ACQUIRED -> a pre-arm edge IS caught. Current Python order is fine; no fix needed.")
        else:
            print("  A MISSED, B ACQUIRED -> CONFIRMED: the edge must be fired AFTER the spectrometer is armed.")
            if c:
                print("  C ACQUIRED -> level trigger (source_type=1) fixes the before-arm case (simplest fix).")
            else:
                print("  C MISSED -> level didn't fix it; use the arm-then-fire handshake (keep edge).")

    spec.close()
    tkp.toolkitpy_close()
    print("\ndone.")


if __name__ == "__main__":
    main()
