"""
Qt-free experiment orchestration.

Builds the segment list from a settings dict and runs a single segment through
the acquire -> compute -> write pipeline. No Qt, no vendor SDK — the spectrometer
is injected, so this is fully testable with FakeSpectrometer.
"""
from dataclasses import dataclass

from spec_echem.acquisition import acquire_segment
from spec_echem.data import (
    compute_absorbance, write_spectra_file, write_echem_file,
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
)


@dataclass
class Segment:
    label: str          # e.g. "CV", "Pre-dedoping", "Doping 0"
    data_type: int      # DATA_TYPE_* constant
    run_number: int     # cycle index for the filename
    num_points: int     # number of spectra to collect
    delta_time: float   # seconds between spectra
    trigger: bool       # wait for Gamry trigger on the first spectrum


def n_doping_cycles(settings):
    """
    Number of doping/dedoping cycles, derived from the doping potential
    start/end/step. The count is meaningful (it must match the Gamry sequence);
    the potential values themselves are documentation-only in this phase.
    """
    start = settings["doping_potential_start"]
    end = settings["doping_potential_end"]
    step = settings["doping_potential_step"]
    if step == 0:
        return 1
    return max(1, int(round((end - start) / step)) + 1)


def build_segments(settings):
    """Translate a settings dict into the ordered list of segments to run."""
    trigger = settings["trigger"]
    segments = []

    if settings["cv_enabled"]:
        # Sweep path length from the vertices (init→limit1→limit2→final), so the
        # spectrum count is exact — same value cv_total_voltage used to hold.
        cv_path = (abs(settings["cv_initial_v"] - settings["cv_limit1_v"])
                   + abs(settings["cv_limit1_v"] - settings["cv_limit2_v"])
                   + abs(settings["cv_limit2_v"] - settings["cv_final_v"]))
        cv_points = int(cv_path / settings["cv_step_size"]
                        * 1000 * settings["cv_cycles"] + 1)
        cv_delta = settings["cv_step_size"] / settings["cv_scan_rate"]
        segments.append(Segment("CV", DATA_TYPE_CV, 0, cv_points, cv_delta, trigger))

    chrono_points = int(settings["chrono_time"] / settings["chrono_delta_time"] + 1)
    chrono_delta = settings["chrono_delta_time"]

    if settings["prededoping_enabled"]:
        segments.append(Segment("Pre-dedoping", DATA_TYPE_PREDEDOPING, 0,
                                 chrono_points, chrono_delta, trigger))

    if settings["doping_enabled"]:
        for run in range(n_doping_cycles(settings)):
            segments.append(Segment(f"Doping {run}", DATA_TYPE_DOPING, run,
                                    chrono_points, chrono_delta, trigger))
            segments.append(Segment(f"Dedoping {run}", DATA_TYPE_DEDOPING, run,
                                    chrono_points, chrono_delta, trigger))

    return segments


def run_one_segment(spec, segment, dark, ref, wavelengths,
                    data_root, added_path, abort_event=None, potentiostat=None):
    """
    Acquire one segment, compute absorbance, and write the data file.

    If a potentiostat is given (Python-controlled mode), it is started the
    instant the spectrometer trigger is armed and stopped once collection ends —
    so the Gamry runs concurrently with spectrum acquisition. An ExternalPotentiostat
    (or None) makes this a no-op, preserving the manual two-step behaviour exactly.

    Returns (absorbance_df, path), or None if aborted (no file is written for a
    partial/aborted segment).
    """
    on_armed = None
    on_tick = None
    if potentiostat is not None:
        potentiostat.prepare(segment)   # slow setup, before the spectrometer is armed
        on_armed = potentiostat.fire    # fired from inside measure(), once armed
        on_tick = potentiostat.pump     # per-spectrum: cook the Gamry curve's data
    try:
        spectra, timestamps = acquire_segment(
            spec, segment.num_points, segment.delta_time, segment.trigger,
            abort_event, on_armed, on_tick,
        )
    finally:
        if potentiostat is not None:
            aborted = abort_event is not None and abort_event.is_set()
            potentiostat.finish(aborted=aborted)
    if abort_event is not None and abort_event.is_set():
        return None
    if not spectra:
        return None

    absorb_df = compute_absorbance(spectra, dark, ref, wavelengths, timestamps)
    path = write_spectra_file(
        absorb_df, spectra, dark, ref, wavelengths, timestamps,
        segment.data_type, segment.run_number, data_root, added_path,
    )

    # Python mode: write the echem data (current/potential) captured during the
    # segment next to the spectra. External/None has no data — this is a no-op.
    echem = potentiostat.last_data() if potentiostat is not None else None
    if echem is not None:
        write_echem_file(echem, segment.data_type, segment.run_number,
                         data_root, added_path)

    return absorb_df, path
