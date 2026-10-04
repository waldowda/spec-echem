"""
Qt-free experiment orchestration.

Builds the segment list from a settings dict and runs a single segment through
the acquire -> compute -> write pipeline. No Qt, no vendor SDK — the spectrometer
is injected, so this is fully testable with FakeSpectrometer.
"""
from dataclasses import dataclass

import math
import numpy as np

from spec_echem.acquisition import acquire_pitt, acquire_segment
from spec_echem.data import (
    compute_absorbance, write_spectra_file, write_echem_file, write_segment_h5,
    write_pitt_h5,
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
    DATA_TYPE_PITT,
)
from spec_echem.pitt import pitt_plan
from spec_echem.logging_config import get_run_logger


@dataclass
class Segment:
    label: str          # e.g. "CV", "Pre-dedoping", "Doping 0"
    data_type: int      # DATA_TYPE_* constant
    run_number: int     # cycle index for the filename
    num_points: int     # number of spectra to collect
    delta_time: float   # seconds between spectra
    trigger: bool       # wait for Gamry trigger on the first spectrum
    save: bool = True   # False: run the segment normally but write no files


def n_doping_cycles(settings):
    """
    Number of doping/dedoping cycles: every potential start, start+step, ... that
    does NOT pass `end`.

    Rounded DOWN, never to nearest. In Python and Autolab modes the driver applies
    start + n*step, so the count decides the highest potential the film sees, and
    the end is a limit the user set -- it must never be exceeded. Rounding to
    nearest did exceed it: start 0.05, end 0.2, step 0.1 gave round(1.5) + 1 = 3
    steps and held the film at +0.25 V (found on the bench, 2026-09-18).

    The small tolerance only absorbs binary float error, so a ladder that lands
    exactly on its end keeps that step: (0.7 - 0.2) / 0.1 is 4.999999999999999.
    """
    start = settings["doping_potential_start"]
    end = settings["doping_potential_end"]
    step = settings["doping_potential_step"]
    if step == 0:
        return 1
    return max(1, int(math.floor((end - start) / step + 1e-9)) + 1)


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
        # round() not truncation: exact ratios land at N-epsilon in binary float
        # (e.g. 1.4/10*1000 = 139.9999…), so int()+1 would drop a spectrum. round
        # keeps the count matching the waveform's duration. (Clean ratios unchanged.)
        cv_points = int(round(cv_path / settings["cv_step_size"]
                              * 1000 * settings["cv_cycles"])) + 1
        cv_delta = settings["cv_step_size"] / settings["cv_scan_rate"]
        segments.append(Segment("CV", DATA_TYPE_CV, 0, cv_points, cv_delta, trigger))

    chrono_points = int(round(settings["chrono_time"] / settings["chrono_delta_time"])) + 1
    chrono_delta = settings["chrono_delta_time"]

    if settings["prededoping_enabled"]:
        # Pre-dedoping has its OWN duration (prededoping_time) — it is NOT the
        # doping/dedoping step time; spectra are just spaced at the same chrono delta.
        # The step conditions the film; its data is often just a baseline you don't
        # want cluttering the folder, so discard = run it, keep nothing.
        pre_points = int(round(settings["prededoping_time"]
                               / settings["chrono_delta_time"])) + 1
        segments.append(Segment("Pre-dedoping", DATA_TYPE_PREDEDOPING, 0,
                                 pre_points, chrono_delta, trigger,
                                 save=not settings.get("prededoping_discard", False)))

    if settings["doping_enabled"]:
        for run in range(n_doping_cycles(settings)):
            segments.append(Segment(f"Doping {run}", DATA_TYPE_DOPING, run,
                                    chrono_points, chrono_delta, trigger))
            segments.append(Segment(f"Dedoping {run}", DATA_TYPE_DEDOPING, run,
                                    chrono_points, chrono_delta, trigger))

    # After the whole ladder, as decided. ONE segment for the whole staircase -- the
    # cell must stay on between steps -- with num_points counting STEPS, not spectra:
    # how many spectra a step takes depends on when its current settles.
    if settings.get("pitt_enabled", False):
        segments.append(Segment("PITT", DATA_TYPE_PITT, 0, len(pitt_plan(settings)),
                                chrono_delta, trigger))

    return segments


def pitt_step_segments(settings):
    """One Segment per planned PITT step, labelled 'PITT 0' ... -- what the GUI
    registers so each step's results can be found, labelled and plotted like any
    other segment. Not run: the staircase runs as the single 'PITT' segment."""
    if not settings.get("pitt_enabled", False):
        return []
    delta = settings["chrono_delta_time"]
    return [Segment(f"PITT {step.index}", DATA_TYPE_PITT, step.index, 0, delta,
                    False) for step in pitt_plan(settings)]


def run_one_segment(spec, segment, dark, ref, wavelengths,
                    data_root, added_path, abort_event=None, potentiostat=None,
                    settings=None):
    """
    Acquire one segment, compute absorbance, and write the data file.

    If a potentiostat is given (Python-controlled mode), it is started the
    instant the spectrometer trigger is armed and stopped once collection ends —
    so the Gamry runs concurrently with spectrum acquisition. An ExternalPotentiostat
    (or None) makes this a no-op, preserving the manual two-step behavior exactly.

    Returns (absorbance_df, path), or None if aborted (no file is written for a
    partial/aborted segment). `path` is None when segment.save is False — the
    segment ran and its absorbance is returned for plotting, but nothing is written.
    """
    on_armed = None
    on_tick = None
    on_first = None
    if potentiostat is not None:
        potentiostat.prepare(segment)   # slow setup, before the spectrometer is armed
        on_armed = potentiostat.fire    # fired from inside measure(), once armed
        on_tick = potentiostat.pump     # per-spectrum: cook the Gamry curve's data
        on_first = potentiostat.note_first_spectrum   # closes the edge->spectrum gap
    try:
        spectra, timestamps = acquire_segment(
            spec, segment.num_points, segment.delta_time, segment.trigger,
            abort_event, on_armed, on_tick, on_first_spectrum=on_first,
        )
    finally:
        if potentiostat is not None:
            aborted = abort_event is not None and abort_event.is_set()
            potentiostat.finish(aborted=aborted)
    if abort_event is not None and abort_event.is_set():
        return None
    if not spectra:
        return None

    # Report the ACTUAL spectra cadence from the hardware timestamps, so any timing
    # impact (e.g. the live-plot redraw contending for the GIL) is visible per segment
    # and can be compared against the target delta_time. Logged at INFO -> status pane.
    if len(timestamps) > 1:
        d = np.diff(timestamps)
        get_run_logger().info(
            "%s spectra cadence: mean %.1f ms (target %.1f), min %.1f, max %.1f, "
            "jitter(sd) %.1f ms, n=%d",
            segment.label, d.mean() * 1000, segment.delta_time * 1000,
            d.min() * 1000, d.max() * 1000, d.std() * 1000, len(timestamps))

    absorb_df = compute_absorbance(spectra, dark, ref, wavelengths, timestamps)

    # A discarded segment still ran, still gets plotted — it just leaves no files
    # behind (no spectra .txt, no echem .txt; the native .dta is skipped in
    # ToolkitPotentiostat._write_dta, which is the only other writer).
    if not segment.save:
        get_run_logger().info("%s: data discarded by request — no files written.",
                              segment.label)
        return absorb_df, None

    # Which formats to write. Taken from the run's settings, falling back to the
    # potentiostat's -- External mode has no potentiostat settings at all, and that
    # is the mode most runs use, so the preference must not hang off it.
    file_settings = settings or getattr(potentiostat, "settings", None) or {}
    fmt = file_settings.get("data_format", "h5+ascii")
    write_ascii = fmt in ("h5+ascii", "ascii")
    write_h5 = fmt in ("h5+ascii", "h5")

    path = None
    if write_ascii:
        path = write_spectra_file(
            absorb_df, spectra, dark, ref, wavelengths, timestamps,
            segment.data_type, segment.run_number, data_root, added_path,
        )

    # Python mode: write the echem data (current/potential) captured during the
    # segment next to the spectra. External/None has no data — this is a no-op.
    echem = potentiostat.last_data() if potentiostat is not None else None
    if echem is not None and write_ascii:
        write_echem_file(echem, segment.data_type, segment.run_number,
                         data_root, added_path)

    # HDF5 beside the ascii -- ADDITIVE, never instead. docs/data-format.md is the
    # authority and the downstream reader depends on those names, so the text files
    # stay the source of truth through the transition.
    #
    # Best-effort by design: a failure here must never abort a run or touch what was
    # already written. The H5 is the new thing; the ascii is what the science
    # currently rests on.
    #
    # Settings come from the potentiostat, the same source and the same getattr the
    # worker's potential log line uses. External mode has none -- the .GSequence sets
    # the potentials there -- so potential_set is simply absent, which is honest.
    pot_settings = getattr(potentiostat, "settings", None) or {}
    if not write_h5:
        return absorb_df, path
    try:
        write_segment_h5(
            absorb_df, spectra, dark, ref, wavelengths, timestamps, echem,
            segment.data_type, segment.run_number, data_root, added_path,
            segment=segment, settings=pot_settings or None,
            compression=pot_settings.get("hdf5_compression", 0),
        )
    except Exception:  # noqa: BLE001 -- an additive file must not sink a run
        get_run_logger().warning(
            "%s: the HDF5 file could not be written. The run and the ascii files "
            "are unaffected.", segment.label, exc_info=True)

    return absorb_df, path


def run_pitt_segment(spec, segment, dark, ref, wavelengths, data_root, added_path,
                     settings, potentiostat, abort_event=None, stop_event=None):
    """Run the whole PITT staircase, write its HDF5, and return each step's data.

    -> (steps, path, record), `steps` being [(label, absorbance DataFrame)] for every
    step that took a spectrum, in order. Unlike run_one_segment this never returns
    None on Abort: a staircase can run for hours, so whatever was collected is written
    and returned, with the interrupted step marked in its end_reason.

    HDF5 is the ONLY copy -- there is no ascii for a PITT -- so a failed write is an
    ERROR, never the quiet warning an additive .h5 gets beside its text files.
    """
    log = get_run_logger()
    plan = pitt_plan(settings)

    def announce(k, step):
        log.info("PITT step %d of %d: %+.3f V (%s)", k + 1, len(plan),
                 step.potential, step.role)

    record = acquire_pitt(spec, potentiostat, plan, settings, trigger=segment.trigger,
                          abort_event=abort_event, stop_event=stop_event,
                          on_step=announce)
    for st in record.steps:
        log.info("PITT %d at %+.3f V: %s after %.1f s, peak %.3g A, %d spectra",
                 st["index"], st["potential_set"], st["end_reason"], st["hold_s"],
                 st["peak_current_A"], st["n_spectra"])
    if not record.steps:
        return [], None, record

    path = None
    try:
        path = write_pitt_h5(record, dark, ref, wavelengths, data_root, added_path,
                             settings=settings,
                             compression=settings.get("hdf5_compression", 0))
    except Exception:  # noqa: BLE001 -- the only copy: say so as loudly as possible
        log.exception("PITT: the HDF5 file could NOT be written, and it is the only "
                      "copy of this staircase's data.")
    if path is None:
        log.error("PITT: no file was written for this staircase (see above).")

    tags = np.asarray(record.spectrum_step, dtype=int)
    steps = []
    for st in record.steps:
        rows = np.flatnonzero(tags == st["index"])
        if rows.size:
            steps.append((f"PITT {st['index']}", compute_absorbance(
                [record.spectra[j] for j in rows], dark, ref, wavelengths,
                [record.timestamps[j] for j in rows])))
    return steps, path, record


def pitt_start_problems(settings):
    """Why a run with the PITT ticked cannot start, or [] when it can.

    Refused at Start rather than discovered mid-staircase: the settings themselves
    (pitt_problems), a potentiostat whose driver cannot hold the cell through a
    staircase yet, and a missing h5py -- HDF5 is the ONLY copy of a PITT, so without
    it the run would collect data and save none of it.
    """
    if not settings.get("pitt_enabled", False):
        return []
    from spec_echem.data import H5PY_AVAILABLE
    from spec_echem.pitt import pitt_problems
    from spec_echem.potentiostat import pitt_supported
    out = list(pitt_problems(settings))
    mode = (settings.get("potentiostat_mode") or "external").lower()
    if not pitt_supported(mode):
        why = {
            "external": "In External mode the sequence file owns the waveform, so "
                        "Python cannot step the potential.",
            "autolab": "The Autolab staircase driver is not written yet: it waits on "
                       "a bench check that the Ei setpoint can change with the cell on.",
            "python": "The Gamry staircase driver is not written yet: it waits on the "
                      "64-bit toolkit.",
        }.get(mode, f"The {mode!r} driver cannot run one.")
        out.append(f"This potentiostat cannot run a PITT yet. {why}")
    if not H5PY_AVAILABLE:
        out.append("A PITT is saved to HDF5 only, and h5py is not installed in this "
                   "environment, so nothing would be saved.")
    return out
