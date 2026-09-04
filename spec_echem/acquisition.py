"""
Hardware acquisition loop for Avantes spectrometer.
No Qt imports. No vendor SDK imports — spec is injected.
"""
import logging
import time

logger = logging.getLogger(__name__)


# What one spectrum costs BEYOND integration x scan averages: the USB round trip
# to fetch it, plus the per-spectrum work in the loop below (on_tick pumps the
# potentiostat, which is itself an instrument call).
#
# MEASURED 2026-09-04 on the Metrohm rig (AvaSpec-ULS2048L, 2.6439 ms integration):
# free-running measure() cost 8.5 / 23.1 / 36.2 / 62.9 / 142.7 ms at 1 / 5 / 10 /
# 20 / 50 averages — i.e. a FLAT ~10 ms above integration x averages, not a
# proportional one. In-run, a CV segment asking for 10 ms achieved 29.9 ms, so the
# loop's own per-spectrum work adds roughly another 20 ms. 30 ms is that total,
# rounded to one conservative number.
#
# It exists so the warning below is honest. Nothing paces on it and no timing
# depends on it — the acquisition loop is unchanged.
SPECTRUM_OVERHEAD_S = 0.030


def spectrum_cost_seconds(integration_ms, scan_averages):
    """What one spectrum really costs: integration x averages, plus overhead.

    The number that has to fit inside a segment's delta_time. Pure arithmetic on
    two numbers so the GUI can call it from its spin boxes, before any hardware
    exists.
    """
    return (float(integration_ms) * int(scan_averages)) / 1000.0 + SPECTRUM_OVERHEAD_S


def suggest_scan_averages(integration_ms, delta_time):
    """The largest scan-averages count that still fits inside delta_time.

    Returns 0 when even a single average cannot fit — then the integration time
    or the slot itself has to change, and no averaging choice rescues it.
    """
    room = float(delta_time) - SPECTRUM_OVERHEAD_S
    if float(integration_ms) <= 0 or room <= 0:
        return 0
    return max(0, int(room * 1000.0 // float(integration_ms)))


def _warn_if_cadence_unachievable(spec, delta_time, num_points):
    """Say so when one spectrum takes longer than the gap between spectra.

    The loop below only paces DOWN to delta_time; when a measurement is slower it
    simply runs slower, silently. The result is not just a slow run — the spectra
    land further apart than requested, so the segment outlives the electrochemistry
    and the later spectra record a cell that has already stopped. The file looks
    completely normal.

    Invisible on the original rig (0.088 ms x 200 averages + overhead = ~48 ms,
    well inside a 100 ms delta_time). A detector with a ~1 ms integration floor
    makes the same 200 averages take ~530 ms, and the arithmetic inverts.

    Counts SPECTRUM_OVERHEAD_S, without which this stays silent through exactly the
    case it exists to catch: 2026-09-04, a CV segment asked for 10 ms and got
    29.9 ms, because integration x averages was only 2.6 ms and the rest was
    overhead. Two thirds of that file recorded a cell that had already stopped.
    """
    try:
        per_spectrum = float(spec.per_spectrum_seconds()) + SPECTRUM_OVERHEAD_S
    except Exception:  # noqa: BLE001 — a diagnostic must never stop a run
        return
    if per_spectrum <= 0 or per_spectrum < delta_time:
        return

    advice = "Reduce scan averages or the integration time."
    try:
        integration_ms, averages = spec.integration_and_averages()
        fits = suggest_scan_averages(integration_ms, delta_time)
        if fits >= 1:
            advice = (f"About {fits} scan averages would fit here "
                      f"(currently {averages}).")
        else:
            advice = (f"Even 1 scan average does not fit at {integration_ms:.4g} ms "
                      f"integration — lengthen the step instead (a coarser CV step "
                      f"or a longer delta time).")
    except Exception:  # noqa: BLE001 — advice is optional, the warning is not
        pass

    logger.warning(
        "Spectra cannot keep the requested cadence: one spectrum takes %.0f ms "
        "(integration x scan averages + ~%.0f ms overhead) but delta_time is "
        "%.0f ms. They will be collected every ~%.0f ms instead, so this segment "
        "takes ~%.0f s rather than ~%.0f s and its later spectra may fall after "
        "the electrochemistry has finished. %s",
        per_spectrum * 1000, SPECTRUM_OVERHEAD_S * 1000, delta_time * 1000,
        per_spectrum * 1000, per_spectrum * num_points, delta_time * num_points,
        advice)




def acquire_segment(spec, num_echem_points, delta_time=0.100, trigger=False,
                    abort_event=None, on_armed=None, on_tick=None):
    """
    Collect a segment of spectra from the spectrometer.

    Args:
        spec: AvantesSpectrometer instance
        num_echem_points: Number of spectra to collect
        delta_time: Target seconds between spectrum acquisitions
        trigger: If True, wait for hardware trigger on first measurement
        abort_event: threading.Event — if set, stops acquisition immediately
        on_armed: optional callable passed into measure() for spectrum 0, so it
            fires from INSIDE measure() — right after AVS_Measure() has armed the
            device and before it polls. In Python-controlled mode this raises
            DIGOUT0 + starts the Gamry, so the edge lands while the spectrometer
            is waiting (an edge fired before AVS_Measure() is missed — see
            examples/diag_trigger_timing.py). None in external mode → no-op.
        on_tick: optional callable invoked once per spectrum, in the idle gap
            after each measurement. In Python mode this pumps the Gamry curve
            (curve.running()) so the framework accumulates its data DURING the
            run — without it, acq_data() comes back empty (the curve is only
            serviced on this, the curve-owning, thread). Same-thread by design:
            no second thread, so it can't contend with the spectrometer timing.
            None in external mode → no-op.

    Returns:
        (spectra, timestamps): spectra is list of 1D arrays, timestamps are
        Avantes SDK timestamps converted to seconds
    """
    _warn_if_cadence_unachievable(spec, delta_time, num_echem_points)

    trigger_mode = 1 if trigger else 0
    spec.set_trigger_mode(trigger_mode)

    spectra = []
    timestamps = []

    for j in range(num_echem_points):
        if abort_event is not None and abort_event.is_set():
            break

        pretime1 = time.time_ns() / 1e9
        # Fire the trigger (on_armed) only for spectrum 0, from inside measure()
        # so the DIGOUT0 edge lands after AVS_Measure() has armed the device.
        result = spec.measure(abort_event, on_armed if j == 0 else None)
        if result is None:  # aborted while waiting for the trigger / data
            break
        timestamp_av, data = result
        pretime = timestamp_av / 1e5  # Avantes units to seconds
        spectra.append(data)
        timestamps.append(pretime)

        if j == 0:
            spec.set_trigger_mode(0)  # disable trigger after first measurement fires

        if on_tick is not None:
            on_tick()  # pump the Gamry curve so its data accumulates during the run

        time.sleep(0.002)
        check_time = time.time_ns() / 1e9
        while (check_time - pretime1) <= (delta_time - 0.0012):
            if abort_event is not None and abort_event.is_set():
                break
            check_time = time.time_ns() / 1e9
            time.sleep(0.5e-3)

    return spectra, timestamps
