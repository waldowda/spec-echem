"""
Hardware acquisition loop for Avantes spectrometer.
No Qt imports. No vendor SDK imports — spec is injected.
"""
import time


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
