"""
Potentiostat control — Phase 2 (EchemToolkitPy migration).

Two interchangeable implementations sit behind one small interface so the rest
of the pipeline (acquire -> compute -> write) is unchanged regardless of *who
starts the Gamry*:

    ExternalPotentiostat  — the proven Phase-1 path. The human starts a
                            `.GSequence` in Gamry Framework; this object does
                            nothing. Behaviour is byte-identical to before.
    ToolkitPotentiostat   — Phase-2 path. Python drives the Gamry through the
                            `toolkitpy` (EchemToolkitPy) library and fires
                            DIGOUT0 itself, so no manual start is needed.

Both reproduce the SAME experiment recipe encoded in
`gamry/Spec_Echem_20250714.GSequence` (CV -> pre-dedoping -> N doping/dedoping
cycles with the doping potential incrementing each cycle); that recipe lives
once in `experiment.build_segments()`, and ToolkitPotentiostat translates each
Segment into the matching toolkitpy signal.

The hardware import is optional and guarded (same pattern as avaspec in
``spectrometer.py``): on a dev machine without the 32-bit Gamry stack,
``import toolkitpy`` fails and ``TOOLKITPY_AVAILABLE`` is False — the GUI then
disables the Python option and only ExternalPotentiostat is offered.

Hardware finding (SpecEchem32, 2026-07-05): a toolkitpy curve DIES within ~50 ms
if the thread that ran it does anything other than poll it in an uninterrupted
loop — sharing a thread with the spectrometer's acquisition loop kills it. So the
Gamry runs on its OWN dedicated thread (one per segment, owning a fresh toolkitpy
session end to end), synchronized to the spectrometer via an "armed" event:
``prepare()`` launches the thread (it opens the session and builds the signal, then
blocks), ``fire()`` releases it the instant the spectrometer is armed (DIGOUT0
high, then run + clean poll loop), and ``finish()`` joins it and picks up the
captured data. The spectrometer keeps its own thread, so their timing is
independent; t=0 is still synced by the hardware trigger.
"""
import threading
import time

from spec_echem.data import (
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
    _echem_dta_path,
)
from spec_echem.logging_config import get_run_logger

try:
    import toolkitpy as tkp
    TOOLKITPY_AVAILABLE = True
except ImportError:
    tkp = None
    TOOLKITPY_AVAILABLE = False

# Generous curve buffer; the Gamry manual caps a signal at < 262143 points.
MAX_CURVE_SIZE = 200000

# Small margin so AVS_Measure() finishes arming before fire() raises DIGOUT0 — the
# edge must land while the spectrometer is waiting (diag_trigger_timing.py showed an
# edge fired before arming is missed). It delays BOTH instruments together, so it
# does not desync them; tune/remove once the bench confirms the arm is instant.
_FIRE_ARM_MARGIN_S = 0.005


def initialize_pstat(pstat):
    """
    Hardware ranges / modes — the "Advanced Pstat Setup". Lifted verbatim from
    the bundled toolkitpy examples (cyclic_voltammetery.py / chronoamperometry.py)
    so we start from Gamry's known-good defaults. The .GSequence's per-test
    fields (Max Current / Sampling Mode / I-E range mode / IRComp) map onto these
    set_* calls — tune on the bench if a run needs the .GSequence's exact ranges.
    """
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
    pstat.set_ie_range_lower_limit(0)  # none
    pstat.set_pos_feed_enable(False)
    pstat.set_analog_out(0.0)
    pstat.set_voltage(0.0)
    pstat.set_pos_feed_resistance(0.0)


def probe_identity():
    """
    Open the Gamry briefly, read its label (user-assigned custom name) and serial
    number, and close. Returns (label, serial). Backs a GUI "Identify" button so
    the user can confirm the potentiostat is reachable and recognize which unit it
    is before committing to a run. Raises if toolkitpy or the hardware is unavailable.
    """
    if not TOOLKITPY_AVAILABLE:
        raise RuntimeError("toolkitpy is not importable — Python potentiostat control unavailable.")
    tkp.toolkitpy_init("spec-echem-identify")
    try:
        pstat = tkp.Pstat("PSTAT")
        return pstat.label(), pstat.serial_no()
    finally:
        tkp.toolkitpy_close()


class Potentiostat:
    """
    No-op base / interface. ExternalPotentiostat is exactly this: the Gamry runs
    standalone from a sequence file, so every hook does nothing.

    Lifecycle, per run:
        open()               once, before the first segment
        for each segment:
            prepare(segment) before the spectrometer is armed — slow setup
                             (build the signal, create the curve)
            fire()           at the exact instant the spectrometer is armed and
                             polling for the trigger (acquire_segment passes this
                             as measure()'s on_armed for spectrum 0) — raise
                             DIGOUT0 + start the waveform
            finish(aborted)  after the segment's spectra are in
        close()              once, after the last segment

    prepare/fire are split so the DIGOUT0 edge is raised ONLY after AVS_Measure()
    has armed the spectrometer — examples/diag_trigger_timing.py proved an edge
    fired before arming is missed. This mirrors the legacy order: spectrometer
    armed and waiting, THEN the trigger.
    """

    def open(self):
        pass

    def prepare(self, segment):
        pass

    def fire(self):
        pass

    def finish(self, aborted=False):
        pass

    def stop(self):
        """Request an in-progress segment to halt early (abort path)."""
        pass

    def pump(self):
        """
        Called once per spectrum during acquisition. In Python-controlled mode
        this services the running Gamry curve so the framework accumulates its
        data; External/no-op does nothing.
        """
        pass

    def last_data(self):
        """
        Echem data (numpy structured array) captured from the just-finished
        segment, or None. External/no-op has none — Python never touches the Gamry.
        """
        return None

    def live_data(self):
        """
        Snapshot of the echem data captured SO FAR in the current segment (numpy
        structured array), or None — lets the GUI draw a live plot mid-run so the
        user can watch a CV/hold and abort early. External/no-op has none.
        """
        return None

    def close(self):
        pass


class ExternalPotentiostat(Potentiostat):
    """Phase-1 behaviour: a human starts the Gamry sequence. Pure no-op."""
    pass


class ToolkitPotentiostat(Potentiostat):
    """
    Phase-2: drive the Gamry from Python via toolkitpy, firing DIGOUT0 so the
    Avantes hardware trigger fires exactly as it does from the .GSequence.

    Construct with the canonical settings dict — the per-segment potentials come
    from it (a doping cycle's potential is start + run_number * step), keyed off
    the Segment's data_type/run_number.
    """

    def __init__(self, settings):
        if not TOOLKITPY_AVAILABLE:
            raise RuntimeError(
                "toolkitpy is not importable — Python potentiostat control needs "
                "the 32-bit Gamry stack. Use External mode."
            )
        self.settings = settings
        self._last_data = None            # acq_data() captured from the last segment
        self._live_data = None            # acq_data() snapshot mid-run (for the live plot)
        self._thread = None               # the per-segment Gamry thread
        self._armed = threading.Event()   # set by fire() when the spectrometer is armed
        self._fired = threading.Event()   # set by fire() — distinguishes a real start
        self._built = threading.Event()   # set by the thread once the signal is built
        self._abort = threading.Event()   # set to stop a segment early
        self._error = None                # exception from the Gamry thread, if any
        self._max_wait = 60.0             # safety cap on the poll loop (set per segment)

    # --- lifecycle ------------------------------------------------------

    def open(self):
        """No toolkit work on the caller's thread — each segment's Gamry thread
        opens and closes its OWN toolkitpy session (see _run_segment). A curve must
        be created, run, and polled all on one thread that does nothing else."""
        pass

    def close(self):
        """Ensure no Gamry thread is left running."""
        self._join_thread()

    # --- per-segment ----------------------------------------------------

    def prepare(self, segment):
        """
        Launch the Gamry on its OWN thread. That thread opens a fresh toolkitpy
        session, builds + initializes the signal, then BLOCKS until fire() signals
        the spectrometer is armed. A dedicated thread is REQUIRED: a toolkitpy curve
        that shares a thread with the spectrometer's acquisition loop dies within
        ~50 ms (hardware-confirmed 2026-07-05, examples/bench_fake_coacquire.py); it
        survives only on a thread running an uninterrupted poll loop.
        """
        self._armed.clear()
        self._fired.clear()
        self._built.clear()
        self._abort.clear()
        self._last_data = None
        self._live_data = None
        self._error = None
        # Safety cap for the poll loop: comfortably longer than the real segment
        # (num_points * delta_time is ~the segment duration).
        self._max_wait = segment.num_points * segment.delta_time * 3.0 + 30.0
        self._thread = threading.Thread(
            target=self._run_segment, args=(segment,),
            name=f"gamry-{segment.label}", daemon=True)
        self._thread.start()
        # Block until the Gamry thread has opened its session and built the signal,
        # so that slow toolkitpy_init/build runs on a CLEAR thread — before the
        # caller arms the spectrometer and its acquisition loop starts hammering the
        # CPU. Building under that load leaves the curve stillborn (it runs ~50 ms
        # then dies with zero data); a clear runway is what bench_gamry_thread's
        # sleep(0.2) gave it. Bounded so a hung open can't wedge the caller.
        if not self._built.wait(timeout=30.0):
            # Setup never finished. Do NOT return — the caller would arm the
            # spectrometer for a trigger this dead/hung thread will never fire, and
            # the run would hang forever with no error shown.
            self._abort.set()
            raise RuntimeError(
                f"Gamry setup for '{segment.label}' did not complete within 30 s "
                "— aborting before arming the spectrometer.")
        if self._error is not None:
            # The thread raised during open/build and already exited via its error
            # path; surface it now rather than arming into a trigger that won't come.
            err = self._error
            self._join_thread()
            raise RuntimeError(
                f"Gamry setup for '{segment.label}' failed: {err}") from err

    def fire(self):
        """
        Called from inside measure() the instant the spectrometer is armed for
        spectrum 0. Release the Gamry thread — it raises DIGOUT0 (the edge the armed
        Avantes catches) and runs the waveform. Returns immediately; the Gamry runs
        concurrently on its own thread.
        """
        self._fired.set()   # record that the segment genuinely started (see finish)
        self._armed.set()

    def finish(self, aborted=False):
        """Wait for the Gamry thread to finish (or stop it on abort) and pick up the
        data it captured. On abort no data is kept (mirrors the spectra rule).

        If fire() never happened — an early failure (e.g. the spectrometer failed to
        arm), not a normal finish — cancel the thread FIRST so that releasing it below
        makes it return WITHOUT running the waveform blind on the sample."""
        if aborted or not self._fired.is_set():
            if not aborted:
                # Say so: the segment was set up and the Gamry thread was waiting to
                # be released, and we are deliberately NOT releasing it. Without this
                # the safety net leaves no trace, so a log showing only the upstream
                # error can't tell you whether the waveform ran on the sample.
                get_run_logger().warning(
                    "Gamry cancelled before it started — the segment never armed, so "
                    "the waveform was NOT applied and no .dta was written.")
            self._abort.set()
        self._armed.set()   # unblock the thread; it now returns without running
        self._join_thread()
        if self._error is not None:
            get_run_logger().warning(
                "Gamry segment thread reported an error: %s", self._error)

    def stop(self):
        self._abort.set()

    def last_data(self):
        return self._last_data

    def live_data(self):
        # Reading the reference is atomic (GIL); acq_data() returns a fresh array
        # each poll, so the GUI thread always sees a consistent snapshot — no lock.
        return self._live_data

    # --- the Gamry thread ----------------------------------------------

    def _join_thread(self):
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=self._max_wait + 5.0)
        self._thread = None

    def _run_segment(self, segment):
        """
        The ENTIRE toolkitpy lifecycle for one segment, alone on this thread: open a
        fresh session, build + init the signal, wait until the spectrometer is armed,
        then set_cell + DIGOUT0-high + run, poll the curve in a clean UNINTERRUPTED
        loop (the only pattern that keeps a curve alive), capture acq_data, write the
        native .dta, and close. A fresh session per segment sidesteps the
        multi-curve-in-one-session hazard.
        """
        pstat = None
        curve = None
        try:
            tkp.toolkitpy_init("spec-echem")
            pstat = tkp.Pstat("PSTAT")
            pstat.set_ctrl_mode(tkp.PSTATMODE)
            initialize_pstat(pstat)
            # Hold `signal` as a live local for the WHOLE segment. The toolkitpy
            # signal object must outlive curve.run(): if its last Python reference
            # drops, CPython frees it immediately (refcount, no GC needed) and the
            # curve is left with a degenerate waveform — it starts (running()==True)
            # then dies with zero data in ~50 ms. This was THE stillborn-curve bug:
            # _build_signal used to return only the curve, dropping `signal` on
            # return. Bench survived only because it kept `signal` as a local.
            curve, signal = self._build_signal(pstat, segment)
            self._built.set()                # release prepare(): build done on a clear thread

            self._armed.wait()               # block until the spectrometer is armed
            if self._abort.is_set():
                return

            time.sleep(_FIRE_ARM_MARGIN_S)   # let AVS_Measure() finish arming
            pstat.set_cell(True)
            pstat.set_digital_out(0x1, 0x1)  # DIGOUT0 HIGH -> armed Avantes fires
            curve.run(True)

            deadline = time.time() + self._max_wait
            while (tkp.pstat_is_valid(pstat) and curve.running()
                   and not self._abort.is_set() and time.time() < deadline):
                # Poll acq_data() during the run and stash it as the live snapshot so
                # the GUI can draw the curve mid-run. (This poll was already here from
                # the validated path; feeding the live plot now also gives it a clear
                # purpose — still flagged for the two-thread simplification follow-up.)
                self._live_data = curve.acq_data()
                time.sleep(0.05)
            if curve.running():
                try:
                    curve.stop()
                except Exception:  # noqa: BLE001
                    pass

            if not self._abort.is_set():
                self._last_data = curve.acq_data()
                self._write_dta(curve, pstat, segment)
        except Exception as exc:  # noqa: BLE001 — surface via _error, never crash the thread
            self._error = exc
            get_run_logger().exception(
                "Gamry segment '%s' failed", getattr(segment, "label", "?"))
        finally:
            self._built.set()   # never leave prepare() blocked, even on a build error
            if pstat is not None:
                try:
                    pstat.set_digital_out(0x0, 0x1)  # DIGOUT0 LOW
                    pstat.set_cell(False)
                except Exception:  # noqa: BLE001
                    pass
            try:
                tkp.toolkitpy_close()
            except Exception:  # noqa: BLE001
                pass

    def _write_dta(self, curve, pstat, segment):
        """Optionally emit a native Gamry .dta via the toolkit (dta/ subfolder).
        Opt-out via settings['save_dta']; a failure must not sink the run."""
        if not getattr(segment, "save", True):
            return          # discarded segment: it ran, but it leaves nothing behind
        if not self.settings.get("save_dta", True):
            return
        if not hasattr(tkp, "print_default_dta_file"):
            get_run_logger().info(
                "toolkitpy has no print_default_dta_file — skipping native .dta.")
            return
        kind = "CV" if segment.data_type == DATA_TYPE_CV else "CHRONOA"
        path = _echem_dta_path(segment.data_type, segment.run_number,
                               self.settings["data_root"], self.settings["data_folder"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tkp.print_default_dta_file(curve, pstat, str(path), kind)
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning(
                "Native .dta write failed for %s: %s", segment.label, exc)

    # --- signal building (runs on the Gamry thread) --------------------

    def _build_signal(self, pstat, segment):
        """
        Build + arm the signal, returning ``(curve, signal)``. The caller MUST keep
        a live reference to `signal` until curve.run() has finished — the toolkitpy
        signal object owns the waveform, and if it is freed the curve runs empty and
        dies (see _run_segment). The curve is created BEFORE the signal is set (it
        registers as the data sink at construction), and init_signal() must be
        contiguous with the run() that follows on this thread.
        """
        s = self.settings
        if segment.data_type == DATA_TYPE_CV:
            curve = tkp.RcvCurve(pstat, MAX_CURVE_SIZE)
            signal = self._cv_signal(pstat, segment)
            pstat.set_signal_r_up_dn(signal)
            pstat.init_signal()
            return curve, signal

        # Non-CV steps are constant-potential holds: a double-step with the pre-step
        # and step-2 times zeroed, i.e. a single hold at `potential` for chrono_time s.
        curve = tkp.ChronoCurve(pstat, MAX_CURVE_SIZE)
        potential = self._chrono_potential(segment)
        # Pre-dedoping holds for its OWN duration; doping/dedoping use chrono_time.
        hold = (s["prededoping_time"] if segment.data_type == DATA_TYPE_PREDEDOPING
                else s["chrono_time"])
        signal = pstat.signal_d_step_new(
            potential, 0.0,                 # pre-step voltage, pre-step time
            potential, hold,                # step-1 voltage, step-1 time (the hold)
            potential, 0.0,                 # step-2 voltage, step-2 time
            segment.delta_time, tkp.PSTATMODE,
        )
        pstat.set_signal_d_step(signal)
        pstat.init_signal()
        return curve, signal

    def _chrono_potential(self, segment):
        s = self.settings
        if segment.data_type == DATA_TYPE_PREDEDOPING:
            return s["prededoping_potential"]
        if segment.data_type == DATA_TYPE_DOPING:
            # Incrementing doping potential, one step per cycle — matches the
            # Gamry "Loop (Variable)" that bumps DopingPotInitial each cycle.
            return s["doping_potential_start"] + segment.run_number * s["doping_potential_step"]
        if segment.data_type == DATA_TYPE_DEDOPING:
            return s["dedoping_potential"]
        raise ValueError(f"No chrono potential for data_type {segment.data_type}")

    def _cv_signal(self, pstat, segment):
        # Vertices map straight onto the .GSequence VINIT/VLIMIT1/VLIMIT2/VFINAL.
        # toolkitpy's extra knobs are derived: one scan rate per leg (the single
        # rate repeated), zero apex/final holds, sample_time = step/rate. Arg
        # order matches the bundled cyclic_voltammetery.py.
        s = self.settings
        scan_rate = s["cv_scan_rate"] / 1000.0   # mV/s -> V/s
        step = s["cv_step_size"] / 1000.0        # mV   -> V
        sample_time = step / scan_rate
        return pstat.signal_r_up_dn_new(
            [s["cv_initial_v"], s["cv_limit1_v"], s["cv_limit2_v"], s["cv_final_v"]],
            [scan_rate, scan_rate, scan_rate],   # one rate per leg
            [0.0, 0.0, 0.0],                     # apex1 / apex2 / final holds
            sample_time, int(s["cv_cycles"]), tkp.PSTATMODE,
        )
