"""
probe_dio_pin.py — WHICH pin of the Autolab DIO port fires the Avantes trigger?

Nobody has ever had to know. `_pulse_trigger()` drives `port.Value = 0xFF`, taking all
eight pins of the port high at once, so whichever one carries the trigger gets its
edge. That works, and it is why the question never came up.

It stops being safe the moment anything else shares the port. The **AvaLight-Mini2
shutter is TTL-controlled**, and NOVA's own procedures use the Autolab DIO for lamp
and shutter control (see docs/metrohm-rig-status.md). Once that line is wired, an
0xFF pulse opens or closes the shutter on every segment — in the middle of a
measurement, on every doping cycle, corrupting the optics in a way that would look
like sample behaviour.

So: find the bit, drive only that bit.

    for each of the 8 bits:
        arm the Avantes for a hardware trigger
        pulse ONLY that bit
        did a scan land?  -> that bit is the trigger line

CELL-SAFE. The potentiostat cell is never switched on and no procedure is run — only
the DIO line is driven and the spectrometer is read. Even so, run it with the working
electrode disconnected or on the dummy, as with any new instrument-control code.

    >> Wire the AvaLight shutter AFTER this, not before. <<
    If the shutter is already connected, expect it to click during the sweep — that
    is itself informative: the bit that moves it is one to avoid.

DIO48 (PGSTAT302N): port A is pins 1-8, B is 17-24, C is 9-16, pin 25 is ground
(NOVA §16.3.1.3.1, p.927). This probe walks the eight bits of ONE port; set
DIO_PORT_INDEX to try another.

Usage:
    python probe_dio_pin.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule   # noqa: E402

# --- what to try -----------------------------------------------------------
AVASPEC_DLL_DIR = r"C:\AvaSpecX64-DLL_9.14.0.0"
DIO_PORT_INDEX = 0          # 0 = P1.A (pins 1-8)
PULSE_WIDTH_S = 0.002
INTEGRATION_MS = 5.0        # keep above the detector floor (ULS2048L ~1.05 ms)
SCAN_TIMEOUT_S = 4.0        # per bit; a miss should fail fast, not stall the probe
CONFIRM_REPEATS = 3         # re-test a hit this many times, to rule out a fluke
FAST_HIT_MS = 100.0         # slower than this is the device misbehaving, not a pin
SETTLE_S = 0.05             # let the detector go idle between arms

TRIGGER_MODE_HARDWARE = 1
TRIGGER_SOURCE_EXTERNAL, TRIGGER_SOURCETYPE_EDGE = 0, 0

HERE = os.path.dirname(os.path.abspath(__file__))


def load_avaspec():
    if os.path.isdir(AVASPEC_DLL_DIR) and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(AVASPEC_DLL_DIR)
    try:
        import avaspec
        return avaspec
    except Exception as exc:  # noqa: BLE001
        say(f"Could not import 'avaspec': {exc}")
        say("On a fresh box the wrapper needs its vendored edits — "
            "see query_avantes_setup.md §2.")
        return None


def open_avantes(avaspec):
    if avaspec.AVS_Init(0) < 0:
        say("AVS_Init failed.")
        return None, None
    if avaspec.AVS_GetNrOfDevices() < 1:
        say("No spectrometer seen. Is AvaSoft or NOVA holding it?")
        return None, None
    handle = avaspec.AVS_Activate(avaspec.AVS_GetList(1)[0])
    if handle < 0:
        say(f"AVS_Activate failed (code {handle}).")
        return None, None
    try:
        pixels = avaspec.AVS_GetParameter(handle, 63484).m_Detector_m_NrPixels
    except Exception:  # noqa: BLE001
        pixels = len(avaspec.AVS_GetLambda(handle)) or 2048
    say(f"  Avantes open: {pixels} pixels")
    return handle, pixels


def stop(avaspec, handle):
    """Put the detector back in a known idle state.

    The 2026-09-09 run wedged here: three arm/measure/read cycles worked, then every
    remaining AVS_PrepareMeasure failed and six of eight bits were never tested.
    AVS_StopMeasure exists in the SDK and was called nowhere in this repo — an armed
    device that never saw its edge just stays armed, and the next PrepareMeasure
    lands on a busy device.
    """
    try:
        avaspec.AVS_StopMeasure(handle)
    except Exception as exc:  # noqa: BLE001
        say(f"    (AVS_StopMeasure raised: {exc})")
    time.sleep(SETTLE_S)


def arm(avaspec, handle, pixels):
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
    cfg.m_Trigger_m_Mode = TRIGGER_MODE_HARDWARE
    cfg.m_Trigger_m_Source = TRIGGER_SOURCE_EXTERNAL
    cfg.m_Trigger_m_SourceType = TRIGGER_SOURCETYPE_EDGE
    cfg.m_Control_m_StrobeControl = 0
    cfg.m_Control_m_LaserDelay = 0
    cfg.m_Control_m_LaserWidth = 0
    cfg.m_Control_m_LaserWaveLength = 0.0
    cfg.m_Control_m_StoreToRam = 0
    # Always from idle, and one retry: a single stuck arm should not silently convert
    # the six bits after it into "skipped" and get reported as a result.
    for attempt in (1, 2):
        stop(avaspec, handle)
        if avaspec.AVS_PrepareMeasure(handle, cfg) < 0:
            continue
        if avaspec.AVS_Measure(handle, 0, 1) >= 0:
            return True
        if attempt == 1:
            say("    (arm failed; stopping the detector and retrying once)")
    return False


def scan_landed(avaspec, handle, timeout):
    """-> seconds waited, or None if the edge never arrived."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = avaspec.AVS_PollScan(handle)
        if r == 1:
            avaspec.AVS_GetScopeData(handle)
            return time.time() - t0
        if r < 0:
            say(f"    AVS_PollScan error (code {r}).")
            return None
        time.sleep(0.002)
    return None


def try_bit(avaspec, handle, pixels, port, mask):
    """Arm, pulse only `mask`, and classify what came back.

    -> "fired" | "slow" | "no" | "unarmable"

    "slow" is its own answer, not a hit. A hardware trigger lands in about one
    integration time plus the poll interval — 0xFF and 0x01 both came back inside
    0.5 ms on 2026-09-09, while the bogus 0x02 "hit" took 1101.9 ms, three orders of
    magnitude out, immediately before the detector wedged. Anything that slow is the
    device misbehaving, not a pin firing.
    """
    label = f"bit 0x{mask:02X} (pin {mask.bit_length()})"
    if not arm(avaspec, handle, pixels):
        say(f"    {label}: COULD NOT ARM — untested")
        return "unarmable"
    ac.pulse(port, PULSE_WIDTH_S, mask=mask)
    waited = scan_landed(avaspec, handle, SCAN_TIMEOUT_S)
    if waited is None:
        say(f"    {label}: no scan")
        stop(avaspec, handle)          # it is still armed; do not leave it that way
        return "no"
    if waited * 1000.0 > FAST_HIT_MS:
        say(f"    {label}: scan after {waited * 1000:.1f} ms — TOO SLOW to be the "
            f"trigger (>{FAST_HIT_MS:.0f} ms); treating as suspect, not a hit")
        stop(avaspec, handle)
        return "slow"
    say(f"    {label}: FIRED after {waited * 1000:.1f} ms")
    stop(avaspec, handle)
    return "fired"


def main():
    rule("WHICH DIO PIN FIRES THE AVANTES TRIGGER?  (cell-safe)")
    say("The cell is never switched on and no procedure is run.")
    say(f"Port index {DIO_PORT_INDEX} (0 = P1.A, pins 1-8 on a DIO48).")

    avaspec = load_avaspec()
    if avaspec is None:
        return 0
    inst = ac.connect()
    if inst is None:
        say("Stopped: no Autolab.")
        return 0

    handle = port = None
    try:
        handle, pixels = open_avantes(avaspec)
        if handle is None:
            return 1
        ac.describe_dio(inst)          # what ports exist, before touching one
        port = ac.open_dio(inst, DIO_PORT_INDEX)
        if port is None:
            return 1

        rule("SANITY — all eight pins (what the driver does today)")
        if try_bit(avaspec, handle, pixels, port, 0xFF) != "fired":
            say("")
            say("  0xFF did not fire the detector, so nothing below will either.")
            say("  Fix that first: check the cable, DIO_PORT_INDEX, and that")
            say("  query_avantes_trigger.py still passes.")
            return 1

        rule("WALKING THE EIGHT BITS")
        hits, slow, untested = [], [], []
        for bit in range(8):
            mask = 1 << bit
            result = try_bit(avaspec, handle, pixels, port, mask)
            if result == "fired":
                hits.append(mask)
            elif result == "slow":
                slow.append(mask)
            elif result == "unarmable":
                untested.append(mask)
                if len(untested) >= 2:
                    say("")
                    say("  Two bits in a row could not be armed. STOPPING: every")
                    say("  remaining bit would report the same thing, and six")
                    say("  untested bits reported as 'no scan' is how the")
                    say("  2026-09-09 run produced a wrong answer.")
                    break

        # A closing control. If the rig stopped working part-way through the walk,
        # every 'no scan' above is meaningless — and the only way to know is to prove
        # the trigger still works NOW, with the pulse that is known to fire it.
        rule("CLOSING SANITY — does 0xFF still fire it?")
        still_ok = try_bit(avaspec, handle, pixels, port, 0xFF) == "fired"
        if not still_ok:
            say("")
            say("  It does NOT. The detector or the DIO link stopped working during")
            say("  the walk, so the results above are VOID — a bit that showed")
            say("  'no scan' may simply have been pulsed at a dead instrument.")
            say("  Power-cycle both and re-run before believing anything here.")

        rule("RESULT")
        if untested:
            say(f"  UNTESTED (could not arm): {[hex(m) for m in untested]}")
        if slow:
            say(f"  SUSPECT (fired, but too slowly): {[hex(m) for m in slow]}")
        if not still_ok:
            say("  VOID — the closing sanity check failed. See above.")
        elif not hits:
            say("  No single bit fired it, but 0xFF did. The trigger may need more")
            say("  than one line, or the edge may be on a pin this port does not")
            say("  cover — try DIO_PORT_INDEX 1, 2, ... for ports B and C.")
        elif len(hits) == 1 and not untested:
            mask = hits[0]
            say(f"  The trigger line is bit 0x{mask:02X} — pin {mask.bit_length()} "
                f"of port index {DIO_PORT_INDEX}.")
            say("")
            say(f"  Confirming {CONFIRM_REPEATS}x...")
            ok = sum(try_bit(avaspec, handle, pixels, port, mask) == "fired"
                     for _ in range(CONFIRM_REPEATS))
            say(f"  fired {ok}/{CONFIRM_REPEATS} times.")
            if ok == CONFIRM_REPEATS:
                say("")
                say(f"  Put this in config/bench.ini:   autolab_dio_mask = {mask}")
                say(f"  and use Pulse value {mask} / End value 0 for a NOVA counter.")
                say("  Every other pin on the port is then free for the AvaLight")
                say("  shutter without the trigger disturbing it.")
            else:
                say("  NOT confirmed — do not set the mask on this evidence.")
        elif len(hits) == 1:
            say(f"  Only bit 0x{hits[0]:02X} fired, but {len(untested)} bit(s) were")
            say("  never tested, so this is a candidate and not an answer.")
            say("  Re-run once the arming problem is fixed.")
        else:
            say(f"  More than one bit fired it: {[hex(h) for h in hits]}.")
            say("  Either several pins are strapped together, or a previous pulse")
            say("  was still settling. Re-run before trusting it.")
    finally:
        try:
            if handle is not None:
                avaspec.AVS_Done()
        except Exception:  # noqa: BLE001
            pass
        if port is not None:
            ac.release_dio(port)
        ac.disconnect(inst)
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "probe_dio_pin_report.txt"))
    sys.exit(code)
