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

NOTE — this module is drafted against the toolkitpy API mapped from the Help
manual + bundled examples but has NOT been exercised on hardware. Every block
that talks to ``toolkitpy`` is marked ``# BENCH:`` where a real run must confirm
behaviour. The single biggest open question is whether ``curve.run()`` blocks or
returns immediately (manual says blocks; examples poll ``curve.running()``); the
threading here is written to be correct *either way* (the Gamry runs on its own
thread, joined when the spectrometer segment finishes).
"""
import threading
import time

from spec_echem.data import (
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
)

try:
    import toolkitpy as tkp
    TOOLKITPY_AVAILABLE = True
except ImportError:
    tkp = None
    TOOLKITPY_AVAILABLE = False

# Generous curve buffer; the Gamry manual caps a signal at < 262143 points.
MAX_CURVE_SIZE = 200000


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
        open()                      once, before the first segment
        for each segment:
            start_segment(segment)  called the instant the spectrometer is armed
                                    and waiting for the trigger (see acquisition
                                    .acquire_segment's on_armed callback)
            finish_segment(aborted) called after the segment's spectra are in
        close()                     once, after the last segment

    start/finish are split because in Python mode the Gamry must start *after*
    the spectrometer trigger is armed (so the DIGOUT0 edge isn't missed) and run
    concurrently with spectrum collection.
    """

    def open(self):
        pass

    def start_segment(self, segment):
        pass

    def finish_segment(self, aborted=False):
        pass

    def stop(self):
        """Request an in-progress segment to halt early (abort path)."""
        pass

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
        self._pstat = None
        self._curve = None
        self._thread = None
        self._gamry_error = None

    # --- lifecycle ------------------------------------------------------

    def open(self):
        tkp.toolkitpy_init("spec-echem")
        self._pstat = tkp.Pstat("PSTAT")
        self._pstat.set_ctrl_mode(tkp.PSTATMODE)
        initialize_pstat(self._pstat)

    def close(self):
        if self._pstat is not None:
            # Belt-and-suspenders: make sure the cell is off and DIGOUT0 is low.
            try:
                self._pstat.set_cell(False)
                self._set_trigger_line(high=False)
            except Exception:  # noqa: BLE001 — closing must not mask the real error
                pass
        tkp.toolkitpy_close()
        self._pstat = None

    # --- per-segment ----------------------------------------------------

    def start_segment(self, segment):
        """
        Build + arm this segment's signal, raise DIGOUT0 (fires the spectrometer
        trigger), then run the Gamry on its own thread so collection proceeds
        concurrently. Called from acquire_segment's on_armed hook, i.e. the
        spectrometer is already armed and waiting.
        """
        self._gamry_error = None
        self._curve = self._build_and_arm_signal(segment)

        self._pstat.set_cell(True)
        self._set_trigger_line(high=True)   # DIGOUT0 HIGH -> Avantes captures spectrum 0

        self._thread = threading.Thread(
            target=self._run_curve, name=f"gamry-{segment.label}", daemon=True
        )
        self._thread.start()

    def finish_segment(self, aborted=False):
        """Wait for (or stop) the Gamry, drop DIGOUT0, open the cell."""
        if aborted:
            self._stop_curve()
        if self._thread is not None:
            # On a normal finish the curve ends ~when collection does; the
            # timeout guards a hung run so the GUI never wedges.
            self._thread.join(timeout=5.0)
            self._thread = None
        self._set_trigger_line(high=False)  # DIGOUT0 LOW
        if self._pstat is not None:
            self._pstat.set_cell(False)
        if self._gamry_error is not None and not aborted:
            err, self._gamry_error = self._gamry_error, None
            raise err

    def stop(self):
        self._stop_curve()

    # --- internals ------------------------------------------------------

    def _run_curve(self):
        try:
            # BENCH: open question — does run() block or return immediately?
            # run(True) = auto-run. The poll loop is a no-op if run() blocked
            # (running() already False), and the real driver if it didn't — so
            # this is correct either way.
            self._curve.run(True)
            while tkp.pstat_is_valid(self._pstat) and self._curve.running():
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001 — surfaced via finish_segment
            self._gamry_error = exc

    def _stop_curve(self):
        if self._curve is not None:
            try:
                self._curve.stop()
            except Exception:  # noqa: BLE001
                pass

    def _set_trigger_line(self, high):
        """DIGOUT0 high/low — the exact line the .GSequence toggles."""
        if self._pstat is None:
            return
        self._pstat.set_digital_out(0x1 if high else 0x0, 0x1)

    def _build_and_arm_signal(self, segment):
        """
        Translate a Segment into a toolkitpy signal + curve, mirroring the
        .GSequence recipe. Returns the armed curve.
        """
        pstat = self._pstat
        s = self.settings

        if segment.data_type == DATA_TYPE_CV:
            signal = self._cv_signal(segment)
            pstat.set_signal_r_up_dn(signal)
            pstat.init_signal()
            return tkp.RcvCurve(pstat, MAX_CURVE_SIZE)

        # Non-CV steps are constant-potential holds, built exactly as the
        # .GSequence does it: a double-step (signal_d_step) with the pre-step and
        # step-2 times zeroed, so it's a single hold at `potential` for
        # chrono_time seconds. Arg order matches the bundled chronoamperometry.py.
        potential = self._chrono_potential(segment)
        signal = pstat.signal_d_step_new(
            potential, 0.0,                 # pre-step voltage, pre-step time
            potential, s["chrono_time"],    # step-1 voltage, step-1 time (the hold)
            potential, 0.0,                 # step-2 voltage, step-2 time
            segment.delta_time, tkp.PSTATMODE,
        )
        pstat.set_signal_d_step(signal)
        pstat.init_signal()
        return tkp.ChronoCurve(pstat, MAX_CURVE_SIZE)

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

    def _cv_signal(self, segment):
        # Vertices map straight onto the .GSequence VINIT/VLIMIT1/VLIMIT2/VFINAL.
        # toolkitpy's extra knobs are derived: one scan rate per leg (the single
        # rate repeated), zero apex/final holds, sample_time = step/rate. Arg
        # order matches the bundled cyclic_voltammetery.py.
        s = self.settings
        scan_rate = s["cv_scan_rate"] / 1000.0   # mV/s -> V/s
        step = s["cv_step_size"] / 1000.0        # mV   -> V
        sample_time = step / scan_rate
        return self._pstat.signal_r_up_dn_new(
            [s["cv_initial_v"], s["cv_limit1_v"], s["cv_limit2_v"], s["cv_final_v"]],
            [scan_rate, scan_rate, scan_rate],   # one rate per leg
            [0.0, 0.0, 0.0],                     # apex1 / apex2 / final holds
            sample_time, int(s["cv_cycles"]), tkp.PSTATMODE,
        )
