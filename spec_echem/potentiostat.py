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
import os
import threading
import time

import numpy as np

from spec_echem.data import (
    DATA_TYPE_CV, DATA_TYPE_DOPING, DATA_TYPE_DEDOPING, DATA_TYPE_PREDEDOPING,
    EchemData, _echem_dta_path,
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


def echem_from_acq_data(acq):
    """toolkitpy's ``acq_data()`` structured array -> the vendor-neutral EchemData.

    The Gamry field names (`vf`, `im`, `time`) stop HERE. `write_echem_file` used to
    read them straight out of the array, which meant any non-Gamry driver had to
    fabricate Gamry field names just to be writable — the one thing that had to change
    before a second potentiostat could exist.

    Returns None when there is nothing yet (no array, or one with no dtype fields);
    raises when a real structured array is missing a field we need, since that is a
    broken contract rather than an empty one.
    """
    if acq is None:
        return None
    names = getattr(acq.dtype, "names", None) or ()
    if not names:
        return None
    missing = [n for n in ("time", "vf", "im") if n not in names]
    if missing:
        raise ValueError(
            f"acq_data missing field(s) {missing}; got {list(names)}")
    return EchemData(
        time=np.asarray(acq["time"]),
        potential=np.asarray(acq["vf"]),
        current=np.asarray(acq["im"]),
    )


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

    def device_lost(self):
        """
        True if the instrument stopped responding partway through the last segment,
        so its echem data is truncated even though the segment otherwise completed.
        The caller uses this to stop the run at the segment that actually failed
        rather than at the next one. External/no-op can never know — always False.
        """
        return False

    def last_data(self):
        """
        Echem data (data.EchemData) captured from the just-finished segment, or
        None. External/no-op has none — Python never touches the potentiostat.
        """
        return None

    def live_data(self):
        """
        Snapshot of the echem data captured SO FAR in the current segment
        (data.EchemData), or None — lets the GUI draw a live plot mid-run so the
        user can watch a CV/hold and abort early. External/no-op has none.
        """
        return None

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Metrohm Autolab
#
# Everything below follows docs/autolab-run-api.md, which records what the SDK
# actually did on the UW rig (2026-08-31) rather than what its documentation says.
# The unresolved points are named constants or explicit stubs, never guesses buried
# in logic — see docs/autolab-driver-finishing.md for what to fill in after the
# bench scripts run.
#
# The import guard mirrors toolkitpy's: pythonnet's `clr` is the cheap
# yes/no. The SDK assembly itself is referenced in open(), from settings paths,
# because those are per-rig.
try:
    import clr as _clr                     # pythonnet
    AUTOLAB_AVAILABLE = True
except ImportError:
    _clr = None
    AUTOLAB_AVAILABLE = False

# Standard-CV command IdNames (autolab-run-api.md §1).
AUTOLAB_CV_COMMAND = "FHCyclicVoltammetry2"
AUTOLAB_WAIT_COMMAND = "FHWait"

# CV staircase CommandParameters, by index. Parameters have no name property on
# this SDK, so the index IS the address and this table is the only map there is.
CV_IDX_START = 0
CV_IDX_UPPER = 1
CV_IDX_LOWER = 2
CV_IDX_STEP = 3
CV_IDX_CROSSINGS = 4      # Int; 2 per full cycle
CV_IDX_STOP = 5
CV_IDX_SCANRATE = 6       # V/s in the SDK, even though NOVA's UI shows mV/s

# Chronoamperometry (doping / dedoping / pre-dedoping). UNKNOWN until
# examples/bench_autolab_ca.py has been run — deliberately None so that reaching
# this path fails loudly instead of writing a plausible-looking wrong potential.
CA_COMMAND = None
CA_IDX_POTENTIAL = None
CA_IDX_DURATION = None
CA_IDX_INTERVAL = None

# Pulse the trigger this long, and give up on a segment after this.
AUTOLAB_PULSE_WIDTH_S = 0.002
AUTOLAB_MAX_WAIT_MARGIN_S = 30.0


def open_instrument(settings):
    """Connect to the Autolab and return the SDK Instrument.

    A module-level function so tests can substitute a FakeAutolab, the same way
    the toolkitpy tests substitute `tkp`.
    """
    sdk = settings.get("autolab_sdk")
    if not sdk:
        raise RuntimeError(
            "autolab_sdk is not set. The Autolab needs sdk/adx/hdw paths in "
            "config/bench.ini — they are machine-specific, like data_root.")
    import sys
    sdk_dir = os.path.dirname(sdk)
    if sdk_dir and sdk_dir not in sys.path:
        sys.path.append(sdk_dir)
    _clr.AddReference(sdk)
    from EcoChemie.Autolab.Sdk import Instrument

    inst = Instrument()
    inst.AutolabConnection.EmbeddedExeFileToStart = settings.get("autolab_adx")
    inst.set_HardwareSetupFile(settings.get("autolab_hdw"))
    inst.Connect()
    if not inst.AutolabConnection.IsConnected:
        raise RuntimeError(
            "Autolab did not connect. Is NOVA holding the link, or is the "
            "hardware-setup file wrong for this instrument?")
    return inst


def open_trigger_port(inst, index=0):
    """DioPortsP1[index] as an output, driven low. Index 0 is P1.A — the line
    query_avantes_trigger.py proved reaches the Avantes."""
    from EcoChemie.Autolab.Sdk import DIO
    from System import Enum
    dio = inst.Dio
    dir_type = _clr.GetClrType(DIO).GetProperty("DioPortDirection").PropertyType
    output = Enum.Parse(dir_type, "Output")
    port = dio.DioPortsP1[index]
    try:
        port.PortDirection = output
    except Exception:  # noqa: BLE001 — some builds set direction at the DIO level
        dio.DioPortDirection = output
    port.Value = 0
    return port


def _set_cell(inst, on):
    """The cell needs the nested enum member; pythonnet 3.0 rejects a bare bool."""
    from EcoChemie.Autolab.Sdk import EI
    inst.Ei.CellOnOff = EI.EICellOnOff.On if on else EI.EICellOnOff.Off


def echem_from_signals(cmd):
    """command.Signals (read after the run) -> EchemData.

    CalcTime is wall-clock from procedure start and begins at roughly the
    procedure's wait duration, so it is rebased here. CalcPotential is the MEASURED
    potential — SetpointApplied is what was commanded, which is not what the data
    file should carry. Current is already amps.
    """
    sigs = getattr(cmd, "Signals", None)
    if sigs is None:
        return None
    idnames = list(getattr(sigs, "IdNames", []) or [])
    if not idnames:
        return None
    channels = {}
    for i, sg in enumerate(sigs):
        if i < len(idnames):
            channels[idnames[i]] = list(sg.ValueAsObject)

    missing = [n for n in ("CalcTime", "EI_0.CalcPotential", "EI_0.CalcCurrent")
               if not channels.get(n)]
    if missing:
        raise ValueError(
            f"Autolab .Signals missing {missing}; got {list(channels)}")

    t = np.asarray(channels["CalcTime"], dtype=float)
    return EchemData(
        time=t - t[0] if len(t) else t,
        potential=np.asarray(channels["EI_0.CalcPotential"], dtype=float),
        current=np.asarray(channels["EI_0.CalcCurrent"], dtype=float),
    )


def autolab_identity(settings):
    """Connect to the Autolab briefly, report what answered, and disconnect.

    Backs the GUI's "Connect Potentiostat" button in Autolab mode, the same way
    probe_identity() backs it for the Gamry — so the user can confirm WHICH
    instrument is on the other end before committing a sample to a run.

    CELL-SAFE: connecting and switching the cell on are separate operations in this
    SDK, and this only connects. Returns a human-readable description.
    """
    inst = open_instrument(settings)          # raises with a readable message
    try:
        hdw = settings.get("autolab_hdw") or ""
        # The hardware-setup file is model-specific and is the closest thing the SDK
        # offers to "which instrument is this" without touching the cell.
        model = os.path.basename(os.path.dirname(hdw)) or "unknown model"
        setup = os.path.basename(hdw) or "no setup file"
        return f"Autolab {model} ({setup})"
    finally:
        try:
            inst.Disconnect()
        except Exception:  # noqa: BLE001 — never leave the link held by a probe
            pass


class AutolabPotentiostat(Potentiostat):
    """Python drives a Metrohm Autolab through the SDK, firing the Avantes trigger.

    Simpler than ToolkitPotentiostat, and for one reason: Measure() is
    NON-BLOCKING. The Gamry needed a dedicated per-segment thread because a
    toolkitpy curve dies within ~50 ms if its thread does anything else; here the
    caller starts the run and polls, so there is no thread, no arm/fire event pair,
    and no stillborn-curve hazard.

    Per segment: prepare() loads the procedure fresh and writes the parameters,
    fire() switches the cell on, calls Measure(), and pulses the trigger inside the
    procedure's own wait window; finish() polls to completion and reads the trace.

    Ordering is unchanged from the Gamry path: fire() is called from INSIDE the
    spectrometer's measure(), after AVS_Measure has armed it, so the edge always
    lands on an armed detector. Late is safe; early is silently missed.

    WHY THIS DRIVES A NOVA PROCEDURE rather than generating a waveform in Python:
    the same reason ToolkitPotentiostat calls toolkitpy's signal_r_up_dn_new /
    signal_d_step_new instead of stepping potentials itself — the staircase and its
    sampling are firmware-timed, and a Python loop inherits OS jitter on both the
    potential and the time axis. The vendor supplies the waveform; we supply the
    numbers. See docs/autolab-run-api.md §0. It is also why the CV_IDX_* / CA_IDX_*
    tables matter so much: with no name property on a CommandParameter, the index is
    the only handle there is on a potential.
    """

    def __init__(self, settings):
        if not AUTOLAB_AVAILABLE:
            raise RuntimeError(
                "pythonnet (clr) is not importable — Autolab control needs the "
                "Metrohm SDK and `pip install pythonnet`. Use External mode.")
        self.settings = settings
        self._inst = None
        self._port = None
        self._proc = None
        self._cmd = None            # the measurement command for this segment
        self._segment = None
        self._last_data = None
        self._live_samples = []     # (t, E, I) scalars accumulated by pump()
        self._t0 = None
        self._overloaded = False
        self._device_lost = False
        self._aborted = False
        self._pulse_delay = 0.0
        self._max_wait = 60.0

    # --- lifecycle ------------------------------------------------------

    def open(self):
        self._inst = open_instrument(self.settings)
        self._port = open_trigger_port(
            self._inst, int(self.settings.get("autolab_dio_port", 0)))

    def close(self):
        if self._inst is None:
            return
        try:
            _set_cell(self._inst, False)
        except Exception:  # noqa: BLE001 — never raise on the way out
            pass
        if self._port is not None:
            try:
                self._port.Value = 0
                self._port.Release()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._inst.Disconnect()
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning("Autolab disconnect failed: %s", exc)
        self._inst = None

    # --- per-segment ----------------------------------------------------

    def prepare(self, segment):
        """Load the procedure FRESH for every segment and write its parameters.

        Reloading each time is deliberate. Whether a second Measure() reuses the
        first run's .Signals buffer is unresolved (bench_autolab_cv.py phase 2), and
        a reload is correct either way — if the buffer turns out to be replaced per
        run, this costs a load; if it accumulates, this is what stops every segment
        after the first from carrying the one before it.
        """
        self._segment = segment
        self._last_data = None
        self._live_samples = []
        self._t0 = None
        self._overloaded = False
        self._device_lost = False
        self._aborted = False
        self._max_wait = segment.num_points * segment.delta_time * 3.0 + \
            AUTOLAB_MAX_WAIT_MARGIN_S

        self._proc = self._inst.LoadProcedure(self._nox_for(segment))
        self._cmd = self._command_for(segment)
        self._apply_parameters(segment)
        self._pulse_delay = self._wait_window()

    def fire(self):
        """The spectrometer is armed and waiting for the edge right now."""
        _set_cell(self._inst, True)
        self._t0 = time.time()
        self._proc.Measure()          # returns immediately
        self._pulse_trigger()

    def finish(self, aborted=False):
        if aborted or self._aborted:
            self._stop_procedure()
        else:
            self._poll_to_completion()
        try:
            _set_cell(self._inst, False)
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning("Autolab: could not switch the cell off: %s", exc)

        if aborted or self._aborted:
            return                    # a discarded segment keeps no data
        try:
            self._last_data = echem_from_signals(self._cmd)
        except ValueError as exc:
            get_run_logger().warning("Autolab: %s", exc)
            self._last_data = None
        self._report_segment_health()

    def stop(self):
        self._aborted = True
        self._stop_procedure()

    def pump(self):
        """Once per spectrum. Two jobs, and the first one matters most.

        An overloaded or open-cell run finishes looking exactly like a good one —
        IsMeasuring goes False, .Signals fills, nothing complains. The overload
        flags are only readable WHILE the run is going, so if nothing samples them
        here, a meaningless segment is written as though it were fine.

        It also accumulates the live scalars, since the Autolab exposes instantaneous
        values rather than a growing array; the authoritative trace still comes from
        .Signals at the end.
        """
        inst = self._inst
        if inst is None:
            return
        try:
            if inst.Ei.PotentialOverload or inst.Ei.CurrentOverload:
                self._overloaded = True
            if not inst.AutolabConnection.IsConnected:
                self._device_lost = True
                return
            if self._t0 is not None:
                self._live_samples.append(
                    (time.time() - self._t0,
                     float(inst.Ei.Potential), float(inst.Ei.Current)))
        except Exception:  # noqa: BLE001 — a live sample must never sink a segment
            pass

    def device_lost(self):
        return self._device_lost

    def last_data(self):
        return self._last_data

    def live_data(self):
        if not self._live_samples:
            return None
        t, e, i = zip(*self._live_samples)
        return EchemData(time=np.asarray(t), potential=np.asarray(e),
                         current=np.asarray(i))

    # --- internals ------------------------------------------------------

    def _nox_for(self, segment):
        key = ("autolab_nox_cv" if segment.data_type == DATA_TYPE_CV
               else "autolab_nox_ca")
        path = self.settings.get(key)
        if not path:
            raise RuntimeError(
                f"{key} is not set — Autolab mode needs a NOVA procedure template "
                "for this segment type (see config/bench.ini).")
        return path

    def _command_for(self, segment):
        if segment.data_type == DATA_TYPE_CV:
            return self._proc.Commands[AUTOLAB_CV_COMMAND]
        if CA_COMMAND is None:
            raise NotImplementedError(
                "The chronoamperometry command and parameter indices are not known "
                "yet. Run examples/bench_autolab_ca.py and fill in CA_COMMAND / "
                "CA_IDX_* — see docs/autolab-driver-finishing.md.")
        return self._proc.Commands[CA_COMMAND]

    def _apply_parameters(self, segment):
        s = self.settings
        if segment.data_type == DATA_TYPE_CV:
            self._set(CV_IDX_START, s["cv_initial_v"])
            self._set(CV_IDX_UPPER, s["cv_limit1_v"])
            self._set(CV_IDX_LOWER, s["cv_limit2_v"])
            self._set(CV_IDX_STOP, s["cv_final_v"])
            self._set(CV_IDX_STEP, s["cv_step_size"] / 1000.0)     # mV -> V
            self._set(CV_IDX_SCANRATE, s["cv_scan_rate"] / 1000.0)  # mV/s -> V/s
            # 2 crossings per full cycle — bench_autolab_cv.py phase 3 confirms.
            self._set(CV_IDX_CROSSINGS, 2 * int(s["cv_cycles"]))
            return
        # Chrono: same potentials as the Gamry path, once the indices are known.
        self._set(CA_IDX_POTENTIAL, self._chrono_potential(segment))
        hold = (s["prededoping_time"] if segment.data_type == DATA_TYPE_PREDEDOPING
                else s["chrono_time"])
        if CA_IDX_DURATION is not None:
            self._set(CA_IDX_DURATION, hold)
        if CA_IDX_INTERVAL is not None:
            self._set(CA_IDX_INTERVAL, segment.delta_time)

    def _set(self, index, value):
        """Write one parameter by index and verify it stuck — a silently ignored
        potential would run the wrong experiment on a real sample."""
        if index is None:
            raise NotImplementedError(
                "An Autolab parameter index is still unknown — see "
                "docs/autolab-driver-finishing.md.")
        prm = self._cmd.CommandParameters[index]
        prm.ValueAsObject = value
        back = prm.ValueAsObject
        if abs(float(back) - float(value)) > 1e-9:
            raise RuntimeError(
                f"Autolab parameter [{index}] did not take: wrote {value}, "
                f"read back {back}.")

    def _chrono_potential(self, segment):
        s = self.settings
        if segment.data_type == DATA_TYPE_PREDEDOPING:
            return s["prededoping_potential"]
        if segment.data_type == DATA_TYPE_DOPING:
            return s["doping_potential_start"] + segment.run_number * s["doping_potential_step"]
        if segment.data_type == DATA_TYPE_DEDOPING:
            return s["dedoping_potential"]
        raise ValueError(f"No chrono potential for data_type {segment.data_type}")

    def _wait_window(self):
        """The procedure's own wait duration — the room between Measure() and the
        electrochemistry starting, which is where the trigger pulse goes. Read from
        the procedure rather than assumed, since a NOVA edit would change it."""
        override = self.settings.get("autolab_pulse_delay_s")
        if override is not None:
            return float(override)
        try:
            wait = self._proc.Commands[AUTOLAB_WAIT_COMMAND]
            return float(wait.CommandParameters[0].ValueAsObject)
        except Exception:  # noqa: BLE001
            get_run_logger().warning(
                "Autolab: no wait command in this procedure — pulsing the trigger "
                "immediately, so the spectra lead the electrochemistry.")
            return 0.0

    def _pulse_trigger(self):
        """Pulse P1.A after the wait window, so the optical and echem clocks start
        together. Sleeping here is harmless: the spectrometer is already armed and
        doing nothing but waiting for this edge. Chunked so an abort still lands."""
        deadline = time.time() + self._pulse_delay
        while time.time() < deadline and not self._aborted:
            time.sleep(min(0.05, max(0.0, deadline - time.time())))
        if self._aborted:
            return
        port = self._port
        port.Value = 0
        time.sleep(0.001)
        port.Value = 0xFF          # rising edge -> the armed Avantes fires
        time.sleep(AUTOLAB_PULSE_WIDTH_S)
        port.Value = 0

    def _poll_to_completion(self):
        started = self._t0 or time.time()
        deadline = started + self._max_wait
        while time.time() < deadline:
            try:
                if not self._proc.IsMeasuring:
                    return
            except Exception:  # noqa: BLE001 — a vanished instrument reads as lost
                self._device_lost = True
                return
            if not self._inst.AutolabConnection.IsConnected:
                self._device_lost = True
                return
            time.sleep(0.05)
        get_run_logger().warning(
            "%s: the Autolab was still running after %.0f s and was stopped at the "
            "safety limit. Its echem data may be truncated.",
            getattr(self._segment, "label", "?"), self._max_wait)
        self._stop_procedure()

    def _stop_procedure(self):
        try:
            if self._proc is not None:
                self._proc.Abort()
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning("Autolab Abort() failed: %s", exc)

    def _report_segment_health(self):
        """Say so when a segment finished but should not be trusted. The Gamry
        equivalent of this silence wrote a truncated file with nothing to show it."""
        label = getattr(self._segment, "label", "?")
        if self._overloaded:
            get_run_logger().warning(
                "%s: the Autolab reported a potential or current OVERLOAD during "
                "this segment. It completed normally and the data looks ordinary, "
                "but the electrochemistry is not trustworthy — check the cell "
                "connections and the current range.", label)
        if self._device_lost:
            get_run_logger().warning(
                "%s: the Autolab stopped responding during this segment; its echem "
                "data is truncated while the spectra are complete.", label)


def make_potentiostat(settings):
    """Build the driver named by settings["potentiostat_mode"].

    One place decides which potentiostat a run uses. It was a ternary at the GUI's
    construction site; a second vendor is coming (see docs/metrohm-rig-status.md),
    and an unknown mode should fail here with a readable message rather than
    silently falling back to "nobody is driving the cell".
    """
    mode = (settings.get("potentiostat_mode") or "external").lower()
    if mode == "external":
        return ExternalPotentiostat()
    if mode == "python":
        return ToolkitPotentiostat(settings)
    if mode == "autolab":
        return AutolabPotentiostat(settings)
    raise ValueError(
        f"Unknown potentiostat_mode {mode!r}; expected 'external', 'python' "
        "or 'autolab'.")


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
        self._device_lost = False         # instrument vanished partway through a segment
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
        self._device_lost = False
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

    def device_lost(self):
        return self._device_lost

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

    def _note_early_exit(self, pstat, segment, elapsed):
        """Warn if the Gamry poll loop ended for any reason other than the waveform
        finishing, so a truncated echem file is never written silently.

        Runs AFTER the loop, on the Gamry thread — not in the per-spectrum path, so it
        costs the acquisition timing nothing.
        """
        points = 0 if self._last_data is None else len(self._last_data.current)
        if not tkp.pstat_is_valid(pstat):
            self._device_lost = True
            get_run_logger().warning(
                "%s: the Gamry stopped responding %.1f s into the step (cable, power, "
                "or the instrument taken by other software). Its echem data is "
                "TRUNCATED — %d points — while the spectra for this segment are "
                "complete. Treat this segment's electrochemistry as partial.",
                segment.label, elapsed, points)
        elif elapsed >= self._max_wait:
            get_run_logger().warning(
                "%s: the Gamry was still running after %.0f s and was stopped at the "
                "safety limit. Echem data may be truncated — %d points.",
                segment.label, elapsed, points)

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

            started = time.time()
            deadline = started + self._max_wait
            while (tkp.pstat_is_valid(pstat) and curve.running()
                   and not self._abort.is_set() and time.time() < deadline):
                # Poll acq_data() during the run and stash it as the live snapshot so
                # the GUI can draw the curve mid-run. (This poll was already here from
                # the validated path; feeding the live plot now also gives it a clear
                # purpose — still flagged for the two-thread simplification follow-up.)
                self._live_data = echem_from_acq_data(curve.acq_data())
                time.sleep(0.05)
            elapsed = time.time() - started
            if curve.running():
                try:
                    curve.stop()
                except Exception:  # noqa: BLE001
                    pass

            if not self._abort.is_set():
                self._last_data = echem_from_acq_data(curve.acq_data())
                self._write_dta(curve, pstat, segment)
                # WHY the poll loop ended matters, and used to be thrown away: leaving
                # early because the instrument vanished looked exactly like finishing
                # the step. The spectrometer runs its own loop and knows nothing of
                # this, so the segment still completed with a full spectra file next to
                # a truncated echem file, marked done, with nothing saying so. Bench-
                # reproduced 2026-07-27 by pulling the Gamry USB mid-segment: the error
                # only surfaced one segment later, and named the wrong segment.
                self._note_early_exit(pstat, segment, elapsed)
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
