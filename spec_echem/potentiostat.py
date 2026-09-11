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
from spec_echem.settings import parse_dio_mask

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

    def note_first_spectrum(self, t_perf):
        """Called with time.perf_counter() when spectrum 0 LANDED.

        The one fact that ties the optical side to the electrical one. The Avantes
        stamps its spectra on its OWN device clock, which has no known offset to
        Python's, so without this mark the run can never say whether the detector
        waited for the trigger edge or was already holding data. Backends that do
        not raise the edge themselves have nothing to compare it to and ignore it.
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
# actually did on the UW rig — the run-API on 2026-08-31, then all of §4 (CV/CA
# maps, abort, lifecycle, fault behaviour, trigger skew) on the rig 2026-09-03.
# The one remaining bench check is positional command-list indexing, used only by
# _neutralise_extra_ca_steps and guarded there. See docs/autolab-driver-finishing.md.
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

# Standard-CV command IdNames (autolab-run-api.md §1; re-confirmed on the rig
# 2026-09-03, bench_autolab_cv.py phase 0).
AUTOLAB_CV_COMMAND = "FHCyclicVoltammetry2"
AUTOLAB_WAIT_COMMAND = "FHWait"

# CV staircase CommandParameters, by index. Parameters have no name property on
# this SDK, so the index IS the address and this table is the only map there is.
# All six confirmed against recorded data (bench_autolab_cv.py, 2026-09-03).
CV_IDX_START = 0
CV_IDX_UPPER = 1
CV_IDX_LOWER = 2
CV_IDX_STEP = 3
CV_IDX_CROSSINGS = 4      # Int; 2 per full cycle (phase 3: 4 -> 2 cycles, points doubled)
CV_IDX_STOP = 5
CV_IDX_SCANRATE = 6       # V/s in the SDK, even though NOVA's UI shows mV/s

# Parameter KEYS from the Autolab SDK manual §6.2 ("Input command parameter names",
# the copy shipped with SDK 2.1), each paired with the index MEASURED on the rig
# 2026-09-03. The INDEX is what gets written; the key is what checks it against the
# command's own IdNames, so a template edit that moves a parameter is caught instead
# of silently followed. See _resolve_param for why that direction and not the other.
#
# NOTE the manual's list order is NOT the index order — it shows eight parameters
# with "Interval time" fifth, while this instrument reports seven and index 4 is
# demonstrably the crossing count (setting it to 4 doubled the points and drove
# ScanNumber to 2). So the keys are adopted; the ordering is not.
CV_PARAMS = {
    "start":     ("Start value",       CV_IDX_START),
    "upper":     ("Upper vertex",      CV_IDX_UPPER),
    "lower":     ("Lower vertex",      CV_IDX_LOWER),
    "step":      ("Step",              CV_IDX_STEP),
    "crossings": ("NrOfStopCrossings", CV_IDX_CROSSINGS),
    "stop":      ("Stop value",        CV_IDX_STOP),
    "scanrate":  ("Scanrate",          CV_IDX_SCANRATE),
}

# Chronoamperometry (doping / dedoping / pre-dedoping) — CONFIRMED on the rig
# 2026-09-03 (bench_autolab_ca.py, each index verified against recorded data).
# `Chrono amperometry.nox` is a THREE-step template:
#   (FHSetSetpointPotential -> FHLevel "Record signals" -> PlotsIvst) x3
# spec-echem needs ONE hold per segment, so the driver drives step 1 and
# neutralises the extra FHLevel steps (see _neutralise_extra_ca_steps). The hold
# POTENTIAL is on the FHSetSetpointPotential command, NOT the FHLevel recorder —
# same split as the CV template. `Commands["FHLevel"]` returns the FIRST of the
# three (bench-confirmed), which is step 1.
CA_RECORDER_COMMAND = "FHLevel"                  # holds duration + interval; owns .Signals
CA_SETPOINT_COMMAND = "FHSetSetpointPotential"   # holds the potential
CA_IDX_POTENTIAL = 0     # on FHSetSetpointPotential
CA_IDX_DURATION = 1      # on FHLevel  (default 5.0 s)
CA_IDX_INTERVAL = 0      # on FHLevel  (default 0.01 s)
CA_IDX_FAST = 2          # on FHLevel  ('UseFastOptions', a bool, ships False)

# Parameter KEYS — READ OFF THE RIG 2026-09-09 (examples/autolab_api_report.txt and
# autolab_api_report_chrono_amperometry.txt), no longer guesses. Every one agrees
# with the index measured on 2026-09-03, so the cross-check confirms rather than
# corrects. Confirmed on BOTH templates where the command appears in both.
CA_KEY_POTENTIAL = "Setpoint value"     # on FHSetSetpointPotential (CV + CA .nox)
WAIT_KEY_DURATION = "Time"              # on FHWait                 (CV + CA .nox)
CA_KEY_DURATION = "Duration"            # on FHLevel
CA_KEY_FAST = "UseFastOptions"          # on FHLevel; NOVA calls it "Use fast options"
CA_KEY_INTERVAL = "Interval time in µs"   # on FHLevel

# That key is MICRO SIGN (U+00B5), not Greek small mu (U+03BC) — verified from the
# report's bytes. Get it wrong and the cross-check reports a spurious MISMATCH.
#
# And do not believe the name: the IdName says microseconds, the display name
# ('Interval time (s)') says seconds, and the template ships 0.01 — which is 10 ms
# as seconds and an impossible 10 ps as microseconds. The driver writes
# segment.delta_time in SECONDS, which is what the recorded data agrees with
# (2026-09-03/04). The vendor's IdName is simply mislabelled.

# How far Ei.Setpoint may land from what was asked before it counts as a failure.
#
# The procedure path verifies parameter writes at 1e-9 because those are software
# values in a Python-visible object: they round-trip exactly, and any difference at
# all means the SDK ignored the write. Ei.Setpoint is a hardware DAC. It SNAPS to
# its nearest step, so an exact comparison rejects a perfectly good write — on the
# rig 2026-09-09 it refused 0.1 V because the instrument applied 0.09994506835937.
#
# 2 mV is comfortably above any plausible DAC step (a 16-bit converter over +-10 V
# is 305 uV) and far below any potential difference that matters electrochemically,
# so this still catches the failure worth catching: a write that was ignored outright.
AUTOLAB_SETPOINT_TOL_V = 0.002

# Pulse the trigger this long, and give up on a segment after this.
AUTOLAB_PULSE_WIDTH_S = 0.002
AUTOLAB_MAX_WAIT_MARGIN_S = 30.0

# bench_autolab_coacquire.py (2026-09-03): on the standard CV template the
# staircase starts ~1 s AFTER FHWait ends (FHPreCurrentRangingCV + cell settle),
# so pulsing at the raw FHWait leaves the spectra ~1 s ahead of the echem. The gap
# is variable, so the real fix is an FHDIO step inside the .nox
# (autolab_trigger_in_procedure = True); until then set a measured
# autolab_pulse_delay_s in config/bench.ini. This is only a log-time hint.
AUTOLAB_STANDARD_TEMPLATE_EXTRA_LAG_S = 1.0

# How long after FHWait each template actually starts RECORDING. Both stock
# templates spend ~1 s on setup after the wait expires — the CV on
# FHPreCurrentRangingCV plus settle, the CA on cell/setpoint settle and starting
# the sampler — so pulsing at the raw FHWait fires about a second early.
#
# MEASURED on this rig against CalcTime[0] — the procedure clock at the first
# recorded sample — with FHWait = 5.0 s (2026-09-04, 10 kOhm dummy, repeats):
#   stock CA   6.084 / 5.885 / 5.962 s  -> lag 0.977 s, spread 199 ms
#   stock CV   5.859 / 5.795 / 5.745 s  -> lag 0.800 s, spread 115 ms
#   spectro CV 5.684 / 5.579 / 5.608 / 5.534 s -> lag 0.602 s, spread 150 ms
#
# Three things that follow, and they bound what this rig can do:
#   * The templates differ by ~180 ms, so ONE absolute delay cannot serve both —
#     a CV-derived 5.95 s fires ~170 ms early on every chrono segment.
#   * The CV's lag is CONDITION-dependent: 0.80 s sweeping +/-0.05 V here but
#     0.99 s at +/-1 V on 2026-09-03, because FHPreCurrentRangingCV's search
#     depends on the current it finds. The value below is the +/-1 V one, since
#     real experiments look more like that than like a 5 uA dummy sweep.
#   * The RUN-TO-RUN SPREAD (115-199 ms) is as large as the correction. Removing
#     the ranging step (spectro CV) drops the mean to 0.602 s but leaves the
#     spread at 150 ms, so the scatter is in the host->instrument start path, not
#     in ranging, and no .nox edit reaches it.
# Net: these constants remove the BIAS. The residual is +/-0.1-0.2 s of jitter,
# irreducible while Python emits the edge. Only a digital-output step inside the
# procedure would fix it, and 2026-09-04's probe found none available through the
# SDK (examples/probe_nox_dio.py) — NOVA's P1.A pulse lives inside
# ExecCommandSpectroTriggered, which exposes no parameters and drives NOVA's own
# spectrometer, the one thing spec-echem cannot share.
# Procedure start -> the recorder's FIRST SAMPLE, i.e. CalcTime[0]. MEASURED with
# FHWait = 0 on 2026-09-09 (20260909_test6, cell-on-anchored):
#   CA  0.974 0.906 0.901 0.914 0.955  -> mean 0.930
#   CV  1.162
# The earlier 0.98 / 0.99 pair was measured at FHWait = 5.0 and is stale for a zero
# wait; note the two moved in OPPOSITE directions, which is why one number cannot
# serve both templates. The ~0.23 s that CV costs above CA is one extra command,
# FHPreCurrentRangingCV ("Optimize current range") — so this lag is a sum of
# per-command overheads, and deleting commands is what shortens it.
AUTOLAB_SETUP_LAG_CV_S = 1.16      # stock CV, FHWait 0
AUTOLAB_SETUP_LAG_CA_S = 0.93      # stock CA, FHWait 0, mean of five
AUTOLAB_SETUP_LAG_SPECTRO_CV_S = 0.60   # Sung-Joo's CV, if autolab_nox_cv points there


class ConfigurationError(RuntimeError):
    """A run was set up wrongly — a missing path, a template that cannot fire, a
    parameter index nobody has filled in yet.

    Separate from an ordinary failure because the remedy is different and so is the
    presentation: this is a sentence the operator can act on ("set X in bench.ini"),
    not a defect, so the worker logs it as a plain message rather than a traceback.
    Subclasses RuntimeError so existing handlers keep working.
    """


def open_instrument(settings):
    """Connect to the Autolab and return the SDK Instrument.

    A module-level function so tests can substitute a FakeAutolab, the same way
    the toolkitpy tests substitute `tkp`.
    """
    sdk = settings.get("autolab_sdk")
    if not sdk:
        raise ConfigurationError(
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


def echem_from_live_samples(samples):
    """[(t, E, I)] collected by pump() -> EchemData, or None if empty.

    The Ei path's counterpart to echem_from_signals(). Time is rebased to the first
    sample exactly as the .Signals path rebases CalcTime, so both modes produce the
    same file. These are instantaneous scalar reads rather than the recorder's own
    buffer, which is the trade: Python owns t=0 and there is no procedure startup,
    but each point costs a USB round trip and the grid is Python's, not the
    instrument's.
    """
    if not samples:
        return None
    t = np.asarray([s[0] for s in samples], dtype=float)
    return EchemData(
        time=t - t[0] if len(t) else t,
        potential=np.asarray([s[1] for s in samples], dtype=float),
        current=np.asarray([s[2] for s in samples], dtype=float),
    )


def sample_ei(inst):
    """Refresh Ei's readings. WITHOUT THIS, EVERY READ RETURNS A STALE VALUE.

    Ei.Current / Ei.Potential / the overload flags are not live properties — they are
    a latch, and Ei.Sampler.Sample() is what reloads it. PROVEN on the rig 2026-09-09
    (examples/probe_ei_live_report.txt): held at 0.1 V then 0.2 V across a 10 kOhm
    dummy, a bare read at the SECOND potential returned exactly the FIRST one's value
    (9.9304e-06 A, 0.099884 V), while Sample()-then-read gave 2.0035e-05 A, 0.2% off
    Ohm's law at both.

    That is what made 20260909_test11 record 0.000125 V and -4.18 nA for every sample
    of every segment: the latch still held whatever was in it at connect time.

    One Sample() refreshes ALL signals, so potential and current come from the same
    instant — preferred over Instrument.GetSignal(), which also works but would take
    each channel in a separate call and could straddle two samples.

    Best-effort: a failed refresh is a stale reading, not a reason to sink a segment.
    """
    try:
        inst.Ei.Sampler.Sample()
        return True
    except Exception:  # noqa: BLE001
        return False


def _set_ei_mode(ei, potentiostatic=True):
    """Put the potentiostat in potentiostatic mode. Module level and thin, so the
    suite (which has no EcoChemie assembly to import) can replace it, exactly as it
    already replaces _set_cell."""
    from EcoChemie.Autolab.Sdk import EI
    ei.Mode = EI.EIMode.Potentiostatic if potentiostatic else EI.EIMode.Galvanostatic


def _set_current_range(ei, name):
    """Fix the current range, or leave the instrument's own if not configured.

    In Ei mode this is the ONLY thing setting the range — there is no procedure and
    no autoranging, so it is a first-class experimental parameter and gets logged on
    success as well as failure. The member names run BACKWARDS relative to the
    current (CR10_1mA, CR09_10mA, CR08_100mA), which is easy to get wrong by guessing.

    An unknown name is a warning rather than a failed run — the instrument's existing
    range still measures — but the warning names the valid members, because on a real
    sample the difference between 1 mA and 10 mA is a clipped transient.
    """
    if not name:
        return
    try:
        from EcoChemie.Autolab.Sdk import EI
        ei.CurrentRange = getattr(EI.EICurrentRange, str(name))
    except Exception as exc:  # noqa: BLE001
        members = ""
        try:
            from EcoChemie.Autolab.Sdk import EI as _EI
            from System import Enum as _Enum
            members = "; valid: " + ", ".join(
                _Enum.GetNames(type(_EI.EICurrentRange.CR10_1mA)))
        except Exception:  # noqa: BLE001
            pass
        get_run_logger().warning(
            "Autolab: current range %r not accepted (%s); leaving the instrument's "
            "own range in place — which on a real sample may clip%s.",
            name, exc, members)
        return
    get_run_logger().info("Autolab: current range fixed at %s (no autoranging in "
                          "Ei mode).", name)


def raw_first_calctime(cmd):
    """CalcTime[0] as the instrument reported it, BEFORE echem_from_signals rebases
    the trace to start at zero.

    That rebase is right for the data file and wrong for diagnostics: this number is
    the recorder's own account of how long after the procedure started it took its
    first sample, which is the quantity the 5-6 s question turns on. Returns None if
    the signal is not there.
    """
    sigs = getattr(cmd, "Signals", None)
    if sigs is None:
        return None
    idnames = list(getattr(sigs, "IdNames", []) or [])
    for i, sg in enumerate(sigs):
        if i < len(idnames) and idnames[i] == "CalcTime":
            vals = list(sg.ValueAsObject)
            return float(vals[0]) if vals else None
    return None


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
    fire() switches the cell on and calls Measure(), then either pulses P1.A itself
    inside the procedure's wait window (default) or leaves it to an FHDIO step in
    the .nox (autolab_trigger_in_procedure); finish() polls to completion and reads
    the trace.

    Ordering is unchanged from the Gamry path: fire() is called from INSIDE the
    spectrometer's measure(), after AVS_Measure has armed it, so the edge always
    lands on an armed detector. Late is safe; early is silently missed.

    On the trigger: the cable (Autolab P1.A -> Avantes hardware trigger input)
    gives a jitter-free spectrometer start (~0.5 ms after the edge, bench-measured
    2026-09-03). What still has host-timing slop is *when Python fires the edge* —
    bench_autolab_coacquire.py measured +988 ms because Python pulses on a
    wall-clock delay targeting the variable FHPreCurrentRangingCV gap. The fix is
    autolab_trigger_in_procedure: an FHDIO step in the .nox fires P1.A on the
    Autolab's own clock, after ranging, and Python drops out of the timing path
    (see docs/autolab-run-api.md §4.5). The Python-pulse path stays as the fallback
    and for templates without the step.

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
        self._dio_step_present = None
        self._named_params = set()     # keys already reported in the run log
        self._pulse_delay = 0.0
        self._max_wait = 60.0
        # When the .nox carries its own FHDIO step (the eventual design — see
        # docs/autolab-run-api.md §4.5), the Autolab fires P1.A itself on its own
        # clock and Python must NOT pulse. fire() then just starts the procedure.
        self._trigger_in_procedure = bool(
            self.settings.get("autolab_trigger_in_procedure", False))
        # Which pins the pulse drives. Resolved HERE, at construction, and not in
        # _pulse_trigger(): by the time that runs the spectrometer is already armed
        # and waiting for an edge, which is the worst moment to discover the mask
        # cannot produce one. A bad value should stop the run before it starts.
        self._dio_mask = parse_dio_mask(
            self.settings.get("autolab_dio_mask", 0xFF))
        # "procedure" runs the .nox for every segment (the shipped behaviour);
        # "ei" drives chrono holds from Python and leaves CV on the procedure.
        self._ca_mode = str(self.settings.get("autolab_ca_mode") or "procedure").lower()
        if self._ca_mode not in ("procedure", "ei"):
            raise ValueError(
                f"autolab_ca_mode must be 'procedure' or 'ei', got {self._ca_mode!r}")

    # --- lifecycle ------------------------------------------------------

    def open(self):
        self._inst = open_instrument(self.settings)
        # Claim the DIO port even when the procedure is meant to fire its own edge.
        # Claiming it only sets the direction and drives it low, and fire() falls back
        # to pulsing from Python if the loaded .nox turns out to have no DIO step —
        # a fallback that needs the port already open. Cheaper than the alternative,
        # which is a spectrometer armed for an edge nobody sends.
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

        Reloading each time is REQUIRED, not just tidy: bench_autolab_cv.py /
        bench_autolab_ca.py (2026-09-03) showed a second Measure() on an already-run
        procedure object is INERT — it returns instantly, IsMeasuring never goes
        True, nothing runs, and .Signals still holds the previous run. Only a fresh
        LoadProcedure() gives a clean run (and a clean buffer), so there is no
        Ei.Sampler.Reset() to call.
        """
        self._segment = segment
        self._dio_step_present = None   # re-checked per procedure
        self._last_data = None
        self._live_samples = []
        self._t0 = None
        self._overloaded = False
        self._device_lost = False
        self._aborted = False
        # Fresh marks per segment: a stale one would have _report_timing
        # describing the PREVIOUS segment's handshake as this one's.
        self._t_cell_on = self._t_measure_returned = self._t_edge = None
        self._t_spectrum0 = None
        self._t_sample_origin = None
        self._ei_mode = False
        self._max_wait = segment.num_points * segment.delta_time * 3.0 + \
            AUTOLAB_MAX_WAIT_MARGIN_S

        # Ei mode: Python IS the experiment for a chrono hold. No LoadProcedure, so
        # none of the ~0.93 s the procedure spends walking FHGetSetValues ->
        # FHSetSetpointPotential -> FHSwitchCell before FHLevel ever records
        # (MEASURED 20260909_test6/7; UseFastOptions changed it by nothing, test8).
        # CV keeps the procedure: the staircase is a real waveform worth having the
        # instrument generate, and its path already meets spec.
        self._ei_mode = (self._ca_mode == "ei"
                         and segment.data_type != DATA_TYPE_CV)
        if self._ei_mode:
            self._proc = None
            self._cmd = None
            self._prepare_ei(segment)
            self._pulse_delay = 0.0     # Python owns t=0; nothing to wait for
            return

        self._proc = self._inst.LoadProcedure(self._nox_for(segment))
        self._cmd = self._command_for(segment)
        self._apply_wait()          # before _wait_window() reads FHWait back
        self._apply_parameters(segment)
        # Check the trigger arrangement BEFORE working out a pulse delay: when the
        # procedure fires its own edge there is no Python pulse to schedule, and
        # _wait_window()'s advice about tuning the delay would be noise in front of
        # the real problem.
        if self._trigger_in_procedure:
            self._require_dio_step()
        self._pulse_delay = self._wait_window(segment)

    def fire(self):
        """The spectrometer is armed and waiting for the edge right now.

        Every wall-clock mark the handshake has is taken here, so the run log can
        state the alignment rather than have it inferred from the template. Cell-on
        is the origin because that is the moment the experiment starts happening to
        the sample — everything the recorder misses is measured from here.
        """
        if self._ei_mode:
            return self._fire_ei()
        _set_cell(self._inst, True)
        # perf_counter for the diagnostics, not time.time(): both read in 58 ns on
        # this box (measured), but time.time() is adjustable and non-monotonic, so an
        # NTP correction mid-segment would silently corrupt a six-second measurement.
        # _t0 stays wall-clock — it is the .dta file's absolute start stamp.
        self._t_cell_on = time.perf_counter()
        self._t0 = time.time()
        self._t_sample_origin = self._t_cell_on
        self._proc.Measure()          # returns immediately
        self._t_measure_returned = time.perf_counter()
        if self._trigger_in_procedure:
            return                    # the .nox's DIO step raises P1.A itself
        self._pulse_trigger()

    def _fire_ei(self):
        """Cell on, edge, sampling — three statements, nothing in between.

        The .nox route had to wait ~0.93 s for the procedure to reach its recorder,
        and fired the trigger at a PREDICTED time to match, which is why the residual
        could never beat the instrument's own +-35 ms startup scatter. Here there is
        nothing to predict: the same thread closes the cell and raises the edge.
        """
        _set_cell(self._inst, True)
        self._t_cell_on = time.perf_counter()
        self._t0 = time.time()
        self._t_measure_returned = self._t_cell_on     # nothing to call
        self._t_sample_origin = self._t_cell_on
        if not self._trigger_in_procedure:
            self._pulse_trigger()

    def finish(self, aborted=False):
        if self._ei_mode:
            return self._finish_ei(aborted)
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
        self._report_timing()
        self._report_segment_health()

    def _finish_ei(self, aborted=False):
        """Cell off, then the trace Python collected itself."""
        try:
            _set_cell(self._inst, False)
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning("Autolab: could not switch the cell off: %s", exc)
        if aborted or self._aborted:
            return                    # a discarded segment keeps no data
        self._last_data = echem_from_live_samples(self._live_samples)
        if self._last_data is None:
            get_run_logger().warning(
                "Autolab (Ei mode): no live samples were collected for this segment; "
                "no echem data written. pump() is what samples, so this means the "
                "acquisition loop never ran.")
        self._report_timing()
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
            # FIRST, always. Everything below reads the latch that this refreshes —
            # including the overload flags, which means that check has never actually
            # been able to fire in EITHER mode. See sample_ei().
            sample_ei(inst)
            pot_over = bool(inst.Ei.PotentialOverload)
            cur_over = bool(inst.Ei.CurrentOverload)
            if pot_over or cur_over:
                # Say it NOW, not in _report_segment_health() at the end. A chrono
                # step that overloads does so at t=0 — the current spikes and decays —
                # so waiting for the segment boundary means 30 s of clipped data
                # before anyone is told, and then the remaining segments run at the
                # same wrong range. Told at t~=0.1 s, the run can be aborted and the
                # range fixed before the sample has been through the whole ladder.
                # Once per segment: this fires every 100 ms while it persists.
                if not self._overloaded:
                    which = " and ".join(
                        [w for w, f in (("CURRENT", cur_over),
                                        ("POTENTIAL", pot_over)) if f])
                    get_run_logger().warning(
                        "%s: %s OVERLOAD at t=%.1f s — the electrochemistry from here "
                        "is CLIPPED, not measured. In Ei mode nothing autoranges, so "
                        "this will persist: ABORT and raise autolab_current_range "
                        "(members run backwards, e.g. CR09_10mA -> CR08_100mA).",
                        getattr(self._segment, "label", "?"), which,
                        (time.perf_counter() - self._t_sample_origin)
                        if self._t_sample_origin else 0.0)
                self._overloaded = True
            if not inst.AutolabConnection.IsConnected:
                self._device_lost = True
                return
            origin = self._t_sample_origin
            if origin is not None:
                # perf_counter against the cell-on mark. In Ei mode these samples ARE
                # the segment's echem data, not a diagnostic sideline, so they get the
                # monotonic clock rather than wall time.
                self._live_samples.append(
                    (time.perf_counter() - origin,
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
            raise ConfigurationError(
                f"{key} is not set — Autolab mode needs a NOVA procedure template "
                "for this segment type (see config/bench.ini).")
        return path

    def _command_for(self, segment):
        """The command whose .Signals holds the recorded trace for this segment —
        the CV staircase, or the (first) FHLevel recorder for a chrono hold."""
        if segment.data_type == DATA_TYPE_CV:
            return self._proc.Commands[AUTOLAB_CV_COMMAND]
        return self._proc.Commands[CA_RECORDER_COMMAND]

    def _prepare_ei(self, segment):
        """Configure the potentiostat BEFORE the cell closes.

        This is the work the procedure's FHGetSetValues / FHSetSetpointPotential
        commands do, and doing it here is the whole point: it happens while the cell
        is still open, off the clock, instead of costing ~0.7 s inside the run. When
        the cell then closes it is already at the segment's potential, which is the
        shape the Gamry driver has always had:

            set_cell(True); set_digital_out(...); curve.run(True)
        """
        ei = self._inst.Ei
        v = self._chrono_potential(segment)
        t0 = time.perf_counter()
        _set_ei_mode(ei)
        _set_current_range(ei, self.settings.get("autolab_current_range"))
        ei.Setpoint = float(v)
        back = float(ei.Setpoint)
        if abs(back - float(v)) > AUTOLAB_SETPOINT_TOL_V:
            raise RuntimeError(
                f"Autolab Ei.Setpoint did not take: wrote {v}, read back {back} "
                f"(further than {AUTOLAB_SETPOINT_TOL_V} V, so this is not DAC "
                f"rounding).")
        get_run_logger().info(
            "Autolab (Ei mode): %s configured at %+.6f V (asked %+.6f V, DAC step "
            "%.0f uV) in %.1f ms, before the cell closes. No procedure is loaded "
            "for this segment.",
            segment.label, back, v, abs(back - v) * 1e6,
            (time.perf_counter() - t0) * 1000.0)

    def _apply_fast_options(self):
        """FHLevel's 'UseFastOptions' bool, which ships False and has never been
        touched here.

        Worth trying because two separate things track the RECORDER starting, not
        the cell switching on: the ~0.93 s from procedure start to the first sample
        (measured 20260909_test6/7), and a ~110 nA offset on the first five samples
        that survived cell-on moving five seconds closer (test5 -> test7). Both look
        like the current amplifier being reconfigured as FHLevel arms, and this is
        the one documented parameter plausibly aimed at that path.

        Left alone (None) unless a rig asks, and a refusal is a warning rather than
        a failed run: this is an optimisation, not a potential that must be right.
        """
        want = self.settings.get("autolab_ca_fast_options")
        if want is None:
            return
        try:
            self._set(self._cmd, CA_IDX_FAST, bool(want), key=CA_KEY_FAST)
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning(
                "Autolab: could not set FHLevel %s to %s (%s); the template's own "
                "value stands.", CA_KEY_FAST, bool(want), exc)
            return
        get_run_logger().info(
            "Autolab: FHLevel %s = %s. Watch CalcTime[0] and the first five "
            "samples' offset — both track the recorder starting.",
            CA_KEY_FAST, bool(want))

    def _apply_wait(self):
        """Write the template's FHWait, if this rig asks for a different one.

        Why it matters for chrono: the driver writes the DOPING potential into the
        setpoint command at position 2, and the cell switches on at position 3 —
        BEFORE this wait. So the stock 5 s is not a settling period at rest, it is
        five seconds of the actual experiment happening with nothing recording, and
        on a film that is the steepest part of the doping transient.

        Left alone (None) the .nox keeps whatever NOVA saved. _wait_window() reads
        FHWait back afterwards, so the trigger delay follows this automatically.
        """
        want = self.settings.get("autolab_wait_s")
        if want is None:
            return
        try:
            wait = self._proc.Commands[AUTOLAB_WAIT_COMMAND]
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning(
                "Autolab: autolab_wait_s is set but this procedure has no %s "
                "command (%s); leaving the template's own timing alone.",
                AUTOLAB_WAIT_COMMAND, exc)
            return
        self._set(wait, 0, float(want), key=WAIT_KEY_DURATION)
        get_run_logger().info(
            "Autolab: FHWait set to %.3f s (was the template's own value). The cell "
            "is live at the segment potential for this long before recording starts.",
            float(want))

    def _apply_parameters(self, segment):
        s = self.settings
        if segment.data_type == DATA_TYPE_CV:
            self._set(self._cmd, CV_IDX_START, s["cv_initial_v"], key=CV_PARAMS["start"][0])
            self._set(self._cmd, CV_IDX_UPPER, s["cv_limit1_v"], key=CV_PARAMS["upper"][0])
            self._set(self._cmd, CV_IDX_LOWER, s["cv_limit2_v"], key=CV_PARAMS["lower"][0])
            self._set(self._cmd, CV_IDX_STOP, s["cv_final_v"], key=CV_PARAMS["stop"][0])
            self._set(self._cmd, CV_IDX_STEP, s["cv_step_size"] / 1000.0,
                      key=CV_PARAMS["step"][0])                    # mV -> V
            self._set(self._cmd, CV_IDX_SCANRATE, s["cv_scan_rate"] / 1000.0,
                      key=CV_PARAMS["scanrate"][0])                # mV/s -> V/s
            # 2 crossings per full cycle — bench_autolab_cv.py phase 3 confirms.
            self._set(self._cmd, CV_IDX_CROSSINGS, 2 * int(s["cv_cycles"]),
                      key=CV_PARAMS["crossings"][0])
            return
        # Chrono hold: potential on the SETPOINT command, duration + interval on the
        # FHLevel recorder (self._cmd). Potentials are the same settings as the Gamry
        # path — a doping cycle is start + run_number * step.
        setpoint = self._proc.Commands[CA_SETPOINT_COMMAND]
        self._set(setpoint, CA_IDX_POTENTIAL, self._chrono_potential(segment),
                  key=CA_KEY_POTENTIAL)
        hold = (s["prededoping_time"] if segment.data_type == DATA_TYPE_PREDEDOPING
                else s["chrono_time"])
        self._set(self._cmd, CA_IDX_DURATION, hold, key=CA_KEY_DURATION)
        self._set(self._cmd, CA_IDX_INTERVAL, segment.delta_time,
                  key=CA_KEY_INTERVAL)
        self._apply_fast_options()
        self._neutralise_extra_ca_steps(segment)

    def _neutralise_extra_ca_steps(self, segment):
        """`Chrono amperometry.nox` has THREE FHLevel hold steps; spec-echem wants
        one. Zero the duration of every FHLevel after the first so only step 1
        holds — otherwise a real sample gets driven to steps 2-3's default 0 V for
        ~10 s after every segment (partial de-doping).

        Finds the extra steps by IdName position rather than a hardcoded index, so a
        purpose-built single-step template makes this a clean no-op. Positional
        command-list access is the one part of the SDK not yet bench-verified (only
        iteration is), so this is defensive and logs what it did.
        """
        try:
            idnames = list(getattr(self._proc.Commands, "IdNames", []) or [])
            commands = list(self._proc.Commands)
        except Exception as exc:  # noqa: BLE001
            get_run_logger().warning(
                "Autolab: could not enumerate commands to neutralise extra CA "
                "hold steps (%s). If this is the 3-step stock template, segments "
                "2-3 will run at 0 V — use a single-step CA .nox.", exc)
            return
        levels = [i for i, idn in enumerate(idnames) if idn == CA_RECORDER_COMMAND]
        extras = levels[1:]                       # keep step 1, zero the rest
        zeroed = 0
        for pos in extras:
            try:
                prm = self._resolve_param(
                    commands[pos], CA_KEY_DURATION, CA_IDX_DURATION)
                prm.ValueAsObject = 0.0
                zeroed += 1
            except Exception as exc:  # noqa: BLE001
                get_run_logger().warning(
                    "Autolab: extra CA hold step at position %d not neutralised: "
                    "%s", pos, exc)

        # Zeroing the RECORDERS is not enough. Each extra hold has its own setpoint
        # command (+0.5 V and -0.5 V in the stock template), and those still execute
        # with the cell on — so a film got two unrecorded half-volt excursions after
        # every doping cycle, writing nothing because the recorders are zero-length.
        # Invisible on a resistor, which is why it survived this long. Park them at
        # the segment's own potential so the cell simply does not move.
        held = self._chrono_potential(segment)
        setpoints = [i for i, idn in enumerate(idnames) if idn == CA_SETPOINT_COMMAND]
        parked = 0
        for pos in setpoints[1:]:
            try:
                prm = self._resolve_param(
                    commands[pos], CA_KEY_POTENTIAL, CA_IDX_POTENTIAL)
                prm.ValueAsObject = held
                parked += 1
            except Exception as exc:  # noqa: BLE001
                get_run_logger().warning(
                    "Autolab: extra CA setpoint at position %d not parked (%s); the "
                    "cell may be driven to the template's own potential after this "
                    "segment.", pos, exc)
        if parked:
            get_run_logger().info(
                "Autolab: parked %d trailing setpoint(s) at %+.3f V so the ignored "
                "steps cannot move the cell.", parked, held)
        if zeroed:
            get_run_logger().info(
                "Autolab: neutralised %d extra CA hold step(s) so only step 1 runs.",
                zeroed)

    def _id_names(self, cmd):
        """This command's parameter keys, in list order, or None if it has none.

        The one authoritative source for what each position IS. The SDK manual's
        §6.2 table is not: it lists eight parameters for the staircase with
        "Interval time" fifth, and this instrument reports seven.
        """
        try:
            names = [str(n) for n in cmd.CommandParameters.IdNames]
        except Exception:  # noqa: BLE001 — plenty of SDKs expose no such member
            return None
        return names or None

    def _resolve_param(self, cmd, key, index):
        """The parameter object at the MEASURED index, with the key used to CHECK it.

        The tempting design is the other way round — prefer the documented name,
        fall back to the index — on the grounds that an index is a position and a
        template edit can move a position silently. The goal is right; the direction
        is wrong, for two reasons:

        1. The indices are the trustworthy half. Each was verified against recorded
           data on this rig 2026-09-03 (setting CV_IDX_CROSSINGS to 4 doubled the
           points and drove ScanNumber to 2). The names come from a manual that
           demonstrably does not describe these commands, and the chrono keys are
           guesses from another project driving a DIFFERENT command.
        2. `_set()` cannot catch a bad name. It writes and reads back the SAME
           object, so a key that resolves to the wrong parameter verifies perfectly
           and runs the wrong experiment on a real film.

        A wrong key that RAISES is harmless. A wrong key that RESOLVES is silent.
        So: the write always lands where the bench measured, and `IdNames` is used to
        confirm the position still means what it meant — which is exactly the
        protection the name-first version was reaching for, failing loudly instead of
        quietly relocating the write.

        `list(cmd.CommandParameters)[index]`, not `[index]`: this SDK's
        CommandParameterList rejects a bare Python int in get_Item ("No method matches
        given arguments"), proven on the rig 2026-09-03. Iteration works.
        """
        names = self._id_names(cmd)

        # No measured index: the name is all there is. Only correct because it is
        # checked against IdNames rather than handed blindly to the SDK.
        if index is None:
            if names is not None and key in names:
                self._log_route(key, "resolved by NAME (no measured index)")
                return list(cmd.CommandParameters)[names.index(key)]
            raise NotImplementedError(
                f"Autolab parameter {key!r} has no known index and the name did not "
                "resolve — see docs/autolab-driver-finishing.md.")

        if key is not None and names is not None:
            at_index = names[index] if index < len(names) else None
            if at_index == key:
                self._log_route(
                    key, f"confirmed by IdNames at the measured index {index}")
            else:
                # Loud, and once per key: the template moved under us, or the key is
                # wrong. Either way the measured index is the half that was checked
                # against real data, so it still wins — but nobody should find this
                # out by reading a strange voltammogram.
                elsewhere = (f"the key sits at index {names.index(key)}"
                             if key in names else "the key is not in this list at all")
                self._log_route(
                    key,
                    f"MISMATCH: index {index} is named {at_index!r}, not {key!r} "
                    f"({elsewhere}). Using the bench-measured index {index}. If the "
                    f".nox template was edited, re-measure before trusting this run.",
                    warn=True)
        elif key is not None:
            self._log_route(
                key, f"no IdNames on this command; using the measured index {index}")

        return list(cmd.CommandParameters)[index]

    def _log_route(self, key, message, warn=False):
        """Say how each parameter was resolved — once per key, per run.

        Per KEY and not once per run: name support is a property of each command's
        parameter list, so "the CV staircase carries IdNames" says nothing about
        FHWait. A single global flag would report the first success as though it had
        settled the whole question.
        """
        if key in self._named_params:
            return
        self._named_params.add(key)
        log = get_run_logger()
        (log.warning if warn else log.info)(
            "Autolab: parameter %r — %s", key, message)

    def _set(self, cmd, index, value, key=None):
        """Write one parameter and verify it stuck — a silently ignored potential
        would run the wrong experiment on a real sample.

        Note what this check CANNOT do: it writes and reads back the same object, so
        it proves the SDK accepted the value, never that the object was the right
        parameter. Guarding the position is _resolve_param's job.
        """
        prm = self._resolve_param(cmd, key, index)
        prm.ValueAsObject = value
        back = prm.ValueAsObject
        if abs(float(back) - float(value)) > 1e-9:
            raise RuntimeError(
                f"Autolab parameter {key or index!r} did not take: wrote {value}, "
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

    def _setup_lag(self, segment):
        """Seconds between FHWait expiring and the template's first recorded sample.

        Per template, because they differ: the CV spends the gap on
        FHPreCurrentRangingCV plus settle, the CA on cell/setpoint settle and
        starting the sampler. Measured values and provenance are on
        AUTOLAB_SETUP_LAG_CV_S / _CA_S above. Overridable per rig, since it is an
        instrument-and-template property rather than a universal constant.
        """
        if segment is not None and segment.data_type == DATA_TYPE_CV:
            key, default = "autolab_setup_lag_cv_s", AUTOLAB_SETUP_LAG_CV_S
        else:
            key, default = "autolab_setup_lag_ca_s", AUTOLAB_SETUP_LAG_CA_S
        value = self.settings.get(key)
        return float(default if value is None else value)

    def _wait_window(self, segment=None):
        """When to pulse P1.A, measured from Measure().

        The procedure's own FHWait, read live so a NOVA edit is picked up, PLUS
        this template's measured setup lag — the ~1 s it spends between the wait
        expiring and its first recorded sample. Pulsing at the raw FHWait fires
        that much early.

        `autolab_pulse_delay_s` still wins if set, as a manual escape hatch, but it
        is no longer the recommended way: it is a single absolute number, and one
        number cannot serve both templates (a CV-derived 5.95 s fires 172 ms early
        on every chrono segment). Prefer leaving it unset and tuning the per-
        template lags.

        Unused when autolab_trigger_in_procedure is set — the .nox fires its own
        edge then.
        """
        override = self.settings.get("autolab_pulse_delay_s")
        if override is not None and self.settings.get("autolab_wait_s") is not None:
            # These two contradict each other. autolab_pulse_delay_s is an absolute
            # number measured against whatever FHWait the template had at the time;
            # once the wait is rewritten it is stale by exactly the amount it moved,
            # and honouring it would fire the trigger seconds away from the recorder.
            # The derived value is self-consistent by construction, so it wins.
            get_run_logger().warning(
                "Autolab: ignoring autolab_pulse_delay_s (%.3f s) because "
                "autolab_wait_s (%.3f s) rewrites the wait it was measured against. "
                "Using FHWait + the measured setup lag instead.",
                float(override), float(self.settings["autolab_wait_s"]))
            override = None
        if override is not None:
            get_run_logger().info(
                "Autolab: pulsing at the manual autolab_pulse_delay_s (%.3f s). "
                "Unset it to use FHWait + this template's measured setup lag, "
                "which is per-template and follows a NOVA edit to the wait.",
                float(override))
            return float(override)
        try:
            wait = self._proc.Commands[AUTOLAB_WAIT_COMMAND]
            base = float(self._resolve_param(
                wait, WAIT_KEY_DURATION, 0).ValueAsObject)
        except Exception:  # noqa: BLE001
            get_run_logger().warning(
                "Autolab: no wait command in this procedure — pulsing the trigger "
                "immediately, so the spectra lead the electrochemistry.")
            return 0.0
        lag = self._setup_lag(segment)
        get_run_logger().info(
            "Autolab: pulsing at %.3f s (FHWait %.2f s + %.2f s measured setup lag "
            "for this template).", base + lag, base, lag)
        return base + lag

    def _require_dio_step(self):
        """With autolab_trigger_in_procedure set, refuse a .nox that cannot fire.

        The flag says the procedure raises P1.A on its own clock. If the template has
        no digital-output step, nothing raises the edge: the spectrometer sits armed
        for a trigger that never comes and the run hangs.

        This RAISES rather than quietly pulsing from Python instead. A fallback would
        keep the run alive but silently change what the data means — the Python path
        needs a measured `autolab_pulse_delay_s`, and someone who configured the
        procedure to fire has no reason to have tuned one, so the fallback would emit
        the edge at whatever stale delay is in bench.ini (the untuned value skews by
        ~1 s). Producing plausible, mistimed data while reporting success is worse
        than stopping.

        Called from prepare(), which runs BEFORE the cell is switched on and before
        the spectrometer arms — so this costs a clear error, not a hung run or a
        disturbed sample.

        Matched loosely on the IdName: NOVA renders this step as `Dio_0` / `HDio`
        (see docs/metrohm-rig-status.md) and the IdName the SDK reports for a
        hand-added one is not yet known.
        """
        try:
            idnames = list(getattr(self._proc.Commands, "IdNames", []) or [])
        except Exception:  # noqa: BLE001
            idnames = []
        found = [n for n in idnames if "dio" in str(n).lower()]
        if found:
            get_run_logger().info(
                "Autolab: procedure fires its own trigger (%s) — Python stays out "
                "of the timing path.", ", ".join(str(n) for n in found))
            return
        raise ConfigurationError(
            "autolab_trigger_in_procedure is set, but "
            f"'{os.path.basename(self._nox_for(self._segment))}' has no "
            "digital-output step, so nothing would raise the Avantes trigger. "
            f"Commands found: {', '.join(str(n) for n in idnames) or 'none'}. "
            "Add the P1.A step in NOVA (copy it from a PC_Spectral* procedure), or "
            "clear autolab_trigger_in_procedure in config/bench.ini to pulse from "
            "Python instead.")

    def _pulse_trigger(self):
        """Pulse the trigger bit(s) after the wait window, so the optical and echem
        clocks start together. Sleeping here is harmless: the spectrometer is already
        armed and doing nothing but waiting for this edge. Chunked so an abort lands.

        Which bits go high is `autolab_dio_mask`. It defaults to 0xFF — ALL EIGHT pins
        of the port — which is how this has always worked and is why the wired pin
        never had to be identified. That is safe only while nothing else lives on the
        port. The AvaLight-Mini2 shutter is TTL-controlled and its line is expected on
        the Autolab DIO (see metrohm-rig-status.md), so once that is wired, 0xFF would
        toggle the shutter on every segment — mid-run. Run
        examples/probe_dio_pin.py to find the real bit, then set the mask.
        """
        # Anchored on cell ON — the same origin the delay was MEASURED against —
        # not on entry to this function. Measure() returns 0.128-0.287 s after cell
        # ON (test6, six segments), and anchoring here added every bit of that on
        # top: the edge landed ~0.21 s AFTER the recorder's first sample on every
        # chrono segment. That is spectra trailing echem, written into real files.
        origin = getattr(self, "_t_cell_on", None) or time.perf_counter()
        deadline = origin + self._pulse_delay
        while time.perf_counter() < deadline and not self._aborted:
            time.sleep(min(0.05, max(0.0, deadline - time.perf_counter())))
        if self._aborted:
            return
        port = self._port
        mask = self._dio_mask
        port.Value = 0
        time.sleep(0.001)
        port.Value = mask          # rising edge -> the armed Avantes fires
        self._t_edge = time.perf_counter()
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

    def note_first_spectrum(self, t_perf):
        self._t_spectrum0 = t_perf

    def _report_timing(self):
        """State the handshake in wall-clock, from cell-on.

        Written because every claim about the 5-6 s gap so far has been INFERRED
        from the template's FHWait plus a measured setup-lag constant, which is
        exactly the reasoning examples/diag_trigger_timing.py exists to refuse. The
        numbers here are taken, not derived, and CalcTime[0] is the instrument's own
        account of when its recorder started — so the two can be checked against
        each other on any run.
        """
        t0 = getattr(self, "_t_cell_on", None)
        if t0 is None:
            return
        parts = []
        edge = getattr(self, "_t_edge", None)
        spec0 = getattr(self, "_t_spectrum0", None)
        for label, mark in (("Measure() returned", getattr(self, "_t_measure_returned", None)),
                            ("trigger edge", edge),
                            ("spectrum 0 landed", spec0)):
            if mark is not None:
                parts.append(f"{label} +{mark - t0:.3f} s")
        if edge is not None and spec0 is not None:
            # The decisive one. A few ms means the detector genuinely sat waiting for
            # the edge; ~0 or negative means it was already holding a scan and the
            # alignment is luck, not hardware.
            parts.append(f"EDGE -> spectrum 0 {(spec0 - edge) * 1000:+.1f} ms")
        if self._ei_mode:
            if self._live_samples:
                parts.append(f"first Ei sample +{self._live_samples[0][0]:.3f} s")
        else:
            raw = None
            try:
                raw = raw_first_calctime(self._cmd)
            except Exception:  # noqa: BLE001 — diagnostics must never break a run
                pass
            if raw is not None:
                parts.append(f"recorder's own CalcTime[0] {raw:.3f} s")
        if not parts:
            return
        get_run_logger().info(
            "%s timing, from cell ON: %s.",
            getattr(self._segment, "label", "?"), " | ".join(parts))

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
        self._warn_if_current_never_rose(label)

    def _warn_if_current_never_rose(self, label):
        """bench_autolab_fault.py (2026-09-03): an open cell / loose lead is
        INVISIBLE to every status signal — IsMeasuring goes False, no overload
        fires, .Signals fills normally. The only tell is that the current stays in
        the noise (22 nA seen for a fully open cell vs ~100 uA connected). So check
        the trace we just built and say so, since nothing else will.
        """
        data = self._last_data
        if data is None or not len(data.current):
            return
        floor = float(self.settings.get("autolab_min_current_a", 1e-7))
        peak = float(np.nanmax(np.abs(np.asarray(data.current, dtype=float))))
        if peak < floor:
            get_run_logger().warning(
                "%s: the measured current never exceeded %.1e A (peak %.1e A). The "
                "cell is probably open or a lead is loose — this segment ran to "
                "completion and the file looks normal, but it carries no "
                "electrochemistry.", label, floor, peak)


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
