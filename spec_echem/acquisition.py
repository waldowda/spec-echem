"""
Hardware acquisition loop for Avantes spectrometer.
No Qt imports. No vendor SDK imports — spec is injected.
"""
import time


def acquire_segment(spec, num_echem_points, delta_time=0.100, trigger=False,
                    abort_event=None, on_armed=None):
    """
    Collect a segment of spectra from the spectrometer.

    Args:
        spec: AvantesSpectrometer instance
        num_echem_points: Number of spectra to collect
        delta_time: Target seconds between spectrum acquisitions
        trigger: If True, wait for hardware trigger on first measurement
        abort_event: threading.Event — if set, stops acquisition immediately
        on_armed: optional callable, invoked once immediately after the trigger
            is armed and before the first measurement. In Python-controlled mode
            this is where the Gamry is started so its DIGOUT0 edge fires the
            (already-armed) spectrometer trigger. None in external mode → no-op.

    Returns:
        (spectra, timestamps): spectra is list of 1D arrays, timestamps are
        Avantes SDK timestamps converted to seconds
    """
    trigger_mode = 1 if trigger else 0
    spec.set_trigger_mode(trigger_mode)

    if on_armed is not None:
        on_armed()

    spectra = []
    timestamps = []

    for j in range(num_echem_points):
        if abort_event is not None and abort_event.is_set():
            break

        pretime1 = time.time_ns() / 1e9
        result = spec.measure(abort_event)
        if result is None:  # aborted while waiting for the trigger / data
            break
        timestamp_av, data = result
        pretime = timestamp_av / 1e5  # Avantes units to seconds
        spectra.append(data)
        timestamps.append(pretime)

        if j == 0:
            spec.set_trigger_mode(0)  # disable trigger after first measurement fires

        time.sleep(0.002)
        check_time = time.time_ns() / 1e9
        while (check_time - pretime1) <= (delta_time - 0.0012):
            if abort_event is not None and abort_event.is_set():
                break
            check_time = time.time_ns() / 1e9
            time.sleep(0.5e-3)

    return spectra, timestamps
