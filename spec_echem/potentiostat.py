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

# Small margin so AVS_Measure() finishes arming before fire() raises DIGOUT0 — the
# edge must land while the spectrometer is waiting (diag_trigger_timing.py showed an
# edge fired before arming is missed). It delays BOTH instruments together, so it
# does not desync them; tune/remove once the bench confirms the arm is instant.
_FIRE_ARM_MARGIN_S = 0.005
# Upper bound on how long finish() waits for the waveform to end (guards a hung run).
_FINISH_MAX_WAIT_S = 10.0


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

    def prepare(self, segment):
        """
        Slow, non-time-critical setup done BEFORE the spectrometer is armed: build
        the segment's signal and create the curve. No cell, no trigger, no run
        here — those happen in fire(), at the armed instant.
        """
        self._curve = self._build_and_arm_signal(segment)

    def fire(self):
        """
        Called the instant the spectrometer is armed and polling (as measure()'s
        on_armed for spectrum 0). Turn the cell on, raise DIGOUT0 (the edge the
        armed spectrometer catches), and start the waveform. run(True) is
        non-blocking (confirmed on hardware), so the Gamry then runs concurrently
        with spectrum collection — no thread needed. Ordering matches the
        .GSequence: DIGOUT0 high, then the experiment runs.
        """
        time.sleep(_FIRE_ARM_MARGIN_S)      # let AVS_Measure() finish arming before the edge
        self._pstat.set_cell(True)
        self._set_trigger_line(high=True)   # DIGOUT0 HIGH -> armed Avantes catches spectrum 0
        self._curve.run(True)               # non-blocking; Gamry runs the waveform

    def finish(self, aborted=False):
        """Wait out (or stop) the waveform, drop DIGOUT0, open the cell."""
        if aborted:
            self._stop_curve()
        else:
            # The waveform ran concurrently with collection, so it's ~done now.
            # Poll to completion, bounded so a hung run can't wedge the GUI.
            deadline = time.time() + _FINISH_MAX_WAIT_S
            while (self._curve is not None and tkp.pstat_is_valid(self._pstat)
                   and self._curve.running() and time.time() < deadline):
                time.sleep(0.02)
            if self._curve is not None and self._curve.running():
                self._stop_curve()
        self._set_trigger_line(high=False)  # DIGOUT0 LOW
        if self._pstat is not None:
            self._pstat.set_cell(False)
        self._curve = None

    def stop(self):
        self._stop_curve()

    # --- internals ------------------------------------------------------

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
