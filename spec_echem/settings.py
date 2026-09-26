"""
Settings load/save for spec-echem experiments.
No Qt imports. No hardware imports.
Validation happens at the GUI boundary, not here.
"""
import json
import math
from pathlib import Path

def parse_dio_mask(raw):
    """The Autolab trigger pin mask, as a checked 1..0xFF integer.

    Accepts what a bit mask is naturally written as — ``0x04``, ``0b100``, ``"0x04"``
    or ``4`` — because it arrives as INI text from a bench file, as a JSON number from
    an experiment file, and as an int from the code defaults. Hex is the form every
    comment here and every line of probe_dio_pin.py's advice uses, so refusing it
    would mean a mask that reverts to "all eight pins" while only warning about it.

    0 is refused outright: it would take the line low -> low -> low, send no rising
    edge, and hang the segment until the wait-timeout with nothing saying why.
    """
    if isinstance(raw, str):
        text = raw.strip()
        try:
            value = int(text, 0)          # 0x04, 0b100, 0o10, 4
        except ValueError:
            value = int(text, 10)         # a leading zero is not a hard error
    else:
        value = int(raw)
    if not 1 <= value <= 0xFF:
        raise ValueError(
            f"autolab_dio_mask must be a pin mask in 1..255 (0x01..0xFF), got {raw!r}"
            + ("; 0 drives no pin high, so the Avantes never sees an edge."
               if value == 0 else "."))
    return value


DEFAULT_SETTINGS = {
    # --- Spectrometer ---
    "integration_time_ms": 0.022,
    "scan_averages": 200,
    # Usable wavelength window (nm) — crops the noisy lamp edges from what is
    # collected/written. None = full calibrated window (~380-1100 nm, no crop).
    "wavelength_min": None,
    "wavelength_max": None,

    # --- Linearity check ramp (bench defaults; see spec_echem/bench.py) ---
    # Lamp-dependent, so these are overridden per rig rather than hard-coded here.
    "lin_start_ms": 0.022,
    "lin_stop_ms": 0.15,
    "lin_steps": 20,
    "lin_tolerance_pct": 2.0,
    "lin_max_fill_pct": 85.0,

    # --- Data location ---
    # `~` is expanded at every write (data.resolve_data_root). A default naming one
    # lab's Windows account was wrong for everyone else; each rig overrides this in
    # config/bench.ini anyway, so this only affects a fresh clone.
    "data_root": "~/specechem_data",
    "data_folder": "",          # format: YYYYMMDD_Description

    # --- Sample info (documentation) ---
    "sample_name": "",
    "electrolyte": "",
    "notes": "",
    # Film geometry, for the density of states: volume = area x thickness.
    # Thickness 150 nm is a typical spin-coated OMIEC (the user).
    #
    # Area is the IMMERSED coated area, one side -- the part below the electrolyte
    # line. NOT the whole coated strip (film above the meniscus cannot dope: doping
    # needs ion insertion) and NOT the optical spot (the current integrates over the
    # whole wetted film whether or not it is illuminated). It varies with immersion
    # depth, so it belongs per-run rather than as a constant.
    #
    # 1.6 = 2 cm immersed x 0.8 cm wide (the user): the ITO/FTO slide has to clear a
    # 1 cm cell, so it is cut narrower than the cuvette. CHECK IT PER RUN -- the area
    # scales the DOS directly, and immersion depth is the part that moves. Set it to
    # 0 if unknown and the plot falls back to dQ/dV rather than reporting a magnitude
    # nothing supports.
    "film_thickness_nm": 150.0,
    "film_area_cm2": 1.6,

    # --- Trigger ---
    "trigger": True,

    # --- Potentiostat control ---
    # "external" = human starts the Gamry .GSequence (Phase-1, proven default);
    # "python"   = Python drives the Gamry via EchemToolkitPy (Phase-2);
    # "autolab"  = Python drives a Metrohm Autolab via its SDK.
    "potentiostat_mode": "external",
    # Python mode only: also emit native Gamry .DTA files (dta/ subfolder)
    # alongside the clean analysis .txt. No-op in External mode.
    "save_dta": True,

    # --- Metrohm Autolab (autolab mode only) ---
    # Install paths and NOVA procedure templates. All machine-specific, so they
    # live in config/bench.ini and are deliberately absent from the tracked
    # config/defaults.ini — committing them would make every pull a conflict.
    "autolab_sdk": "",              # EcoChemie.Autolab.Sdk assembly, no .dll
    "autolab_adx": "",              # the Adk.x hardware driver
    "autolab_hdw": "",              # this instrument's HardwareSetup XML
    "autolab_nox_cv": "",           # standard CV procedure template
    "autolab_nox_ca": "",           # chronoamperometry procedure template
    "autolab_dio_port": 0,          # DioPortsP1 index; 0 = P1.A
    # Which pins of that port the trigger pulse drives. 0xFF = all eight, the
    # historical behavior; safe only while nothing else shares the port. Set a
    # single bit once examples/probe_dio_pin.py has found the wired pin.
    # Parsed by parse_dio_mask() below, so 0x04 / 0b100 / 4 all mean the same thing.
    "autolab_dio_mask": 0xFF,
    # The template's FHWait, in seconds. None = leave whatever the .nox carries.
    # The stock CA template ships 5.0 s, and the driver writes the DOPING potential
    # into the setpoint command that runs BEFORE it — so the cell sits at the doping
    # potential for those 5 s with nothing recording, and the steepest part of the
    # transient is lost. Set this to shrink or remove that window.
    "autolab_wait_s": None,
    # FHLevel's "Use fast options" bool. None = leave the .nox alone (it ships
    # False). True is an experiment: see AutolabPotentiostat._apply_fast_options.
    "autolab_ca_fast_options": None,
    # Who runs a chrono hold. "procedure" loads the .nox for every segment (shipped
    # behavior); "ei" drives doping/dedoping/pre-dedoping from Python via Ei and
    # leaves CV on the procedure. Ei exists because the .nox spends ~0.93 s reaching
    # its recorder (measured 2026-09-09) and nothing configurable shortens it.
    "autolab_ca_mode": "procedure",
    # Fixed current range for Ei mode, e.g. "CR10_1mA". Blank = leave whatever the
    # instrument has. The procedure sets this itself via FHGetSetValues; with no
    # procedure, it becomes Python's job.
    "autolab_current_range": "",
    # Gamry I/E range: a full-scale current in amperes, pinned for the segment.
    # Before this existed the range was never set at all and every Python-mode run
    # sat on the instrument's power-up range - 600 mA - which put 1-2 uA of noise on
    # films drawing 1-26 uA. 6 mA is the shipped default because it clips nothing
    # observed on these rigs (peaks to 742 uA); the per-segment advisory then names
    # a finer range for the actual sample. "auto" is accepted but NOT the default:
    # Gamry documents auto-ranging as not recommended above 1 point/s and we sample
    # at 10. See apply_gamry_current_range.
    "gamry_current_range": 6.0e-3,
    "autolab_pulse_delay_s": None,  # None = FHWait + the template's setup lag
    "autolab_setup_lag_cv_s": None,  # None = AUTOLAB_SETUP_LAG_CV_S (measured)
    "autolab_setup_lag_ca_s": None,  # None = AUTOLAB_SETUP_LAG_CA_S (measured)
    "autolab_trigger_in_procedure": False,  # True = the .nox's FHDIO step fires P1.A

    # --- Cyclic voltammetry (vertices map to Gamry VINIT/VLIMIT1/VLIMIT2/VFINAL) ---
    "cv_enabled": True,
    "cv_cycles": 3,
    "cv_initial_v": 0.0,        # V  — Initial E
    "cv_limit1_v": -0.5,        # V  — Scan Limit 1
    "cv_limit2_v": 0.7,         # V  — Scan Limit 2
    "cv_final_v": 0.0,          # V  — Final E
    "cv_step_size": 10.0,       # mV
    "cv_scan_rate": 100.0,      # mV/s

    # --- Pre-dedoping baseline ---
    "prededoping_enabled": True,
    "prededoping_potential": 0.0,   # V — placeholder for EchemToolkitPy
    "prededoping_time": 30.0,       # s
    "prededoping_discard": False,   # run the step to condition the film, write no files

    # --- Doping / dedoping cycles ---
    "doping_enabled": True,
    "doping_potential_start": 0.2,  # V — first doping potential
    "doping_potential_end": 0.8,    # V — last doping potential
    "doping_potential_step": 0.1,   # V — increment between cycles
    "dedoping_potential": 0.0,      # V — placeholder for EchemToolkitPy
    "chrono_time": 30.0,            # s — duration of each doping or dedoping step
    "chrono_delta_time": 0.100,     # s — time between spectra acquisitions
}


def load_settings(path, base=None):
    """
    Load settings from a JSON file, overlaid on `base`.

    `base` is what the missing keys fall back to, and getting it wrong is not
    cosmetic. Backfilling from DEFAULT_SETTINGS alone silently reverts every bench
    value the file does not happen to mention — so a settings file saved before a
    bench key existed drags the whole rig back to code defaults for that key, with
    nothing said. That is how 20260909_test5 ran with autolab_wait_s=None and
    autolab_dio_mask=255 while config/bench.ini said 0.0 and 1: the file predated
    both keys.

    Callers that have bench defaults MUST pass them (the GUI passes
    MainWindow.bench_base()). The DEFAULT_SETTINGS fallback here is for callers with
    no rig — tests, and reading a settings file off a different machine.

    Args:
        path: str or Path to the settings JSON file
        base: dict to fill missing keys from; defaults to DEFAULT_SETTINGS

    Returns:
        dict with all settings keys present
    """
    with open(path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    settings = (DEFAULT_SETTINGS if base is None else base).copy()
    settings.update(saved)
    return settings


def save_settings(settings, path):
    """
    Save settings dict to a JSON file.

    Args:
        settings: dict of settings values
        path: str or Path to write to (parent directory must exist)
    """
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# How far above the detector's floor the linearity ramp should reach when the stored
# stop is unusable. The ramp has to span enough exposure for curvature to appear, and
# "enough" is lamp-dependent, not a property of the detector — this is a starting
# point that `Find saturation` then refines, NOT a derived constant. The two detectors
# measured so far do not share one multiplier (0.15/0.009033 is ~17x; a 1.048 ms
# detector wants ~8 ms, or ~8x), so treat this as a first guess that gets the ramp
# into runnable territory rather than a number with physics behind it.
LIN_STOP_FLOOR_SPANS = 8.0


def clamp_to_detector_floor(settings, floor_ms):
    """
    Raise any exposure that sits BELOW what the connected detector will accept.

    Detectors differ by ~100x in the shortest exposure they honor (MEASURED: 0.009033 ms
    on a SensorType 22 part, 1.048 ms on a SensorType 10 part). Below its floor a
    detector does not clamp politely — `AVS_PrepareMeasure` REJECTS the request with
    code -11 — so a ramp tuned for the fast detector cannot run at all on the slow one.

    Clamp, never overwrite. Only values below the floor move, and they move up to the
    floor exactly. A value already above it is a scientific choice — an integration time
    set from a linearity check against this rig's lamp to land ~85% fill — and pulling
    that down to the hardware minimum would silently destroy the working point. The
    floor is a hardware constraint; where to sit above it is the scientist's call.

    Args:
        settings: dict to modify in place
        floor_ms: the detector's minimum integration time, or None if unknown
            (no device, or the probe failed) — then nothing is changed.

    Returns:
        list of (key, old_value, new_value) for every value that moved, so the caller
        can SAY what it changed. Empty when nothing did.
    """
    if floor_ms is None:
        return []
    try:
        floor = float(floor_ms)
    except (TypeError, ValueError):
        return []
    if not floor > 0:
        return []

    changes = []
    for key in ("integration_time_ms", "lin_start_ms"):
        try:
            current = float(settings.get(key))
        except (TypeError, ValueError):
            continue
        if current < floor:
            settings[key] = floor
            changes.append((key, current, floor))

    # The ramp's stop is not a floor question but an ordering one: whatever happened
    # above, it has to stay above the start or the check has no range to walk.
    try:
        start = float(settings.get("lin_start_ms"))
        stop = float(settings.get("lin_stop_ms"))
    except (TypeError, ValueError):
        return changes
    if stop <= start:
        new_stop = floor * LIN_STOP_FLOOR_SPANS
        if new_stop <= start:                      # pathological floor; keep it ordered
            new_stop = start * LIN_STOP_FLOOR_SPANS
        settings["lin_stop_ms"] = new_stop
        changes.append(("lin_stop_ms", stop, new_stop))
    return changes


def tidy_detector_floor(floor_ms, sig_figs=3):
    """
    Round a probed floor UP to a legible number: 1.04803466796875 -> 1.05.

    Rounding UP is what makes this safe -- the result is never below the true floor,
    so an exposure built on it is always one the detector accepts.

    The trailing digits are not real precision. The floor is found by bisecting
    `AVS_PrepareMeasure` to a tolerance of 1e-4 ms, so `1.04803466796875` claims
    fifteen digits of a number known to four, and on a fast detector 1e-4 ms is over
    1% of the value. Quoting the raw bisect result implies a precision the method does
    not have, and it is hostile to type, read back, or compare against a datasheet.

    MEASURED floors tidy to: 1.048034... -> 1.05 (datasheet says ~1.05), and
    0.009033 -> 0.00904.

    The raw value is still what gets logged and written to the run metadata -- this
    is the number the software OPERATES on, not a replacement for the measurement.
    """
    if floor_ms is None:
        return None
    try:
        floor = float(floor_ms)
    except (TypeError, ValueError):
        return None
    if not floor > 0:
        return None
    exponent = math.floor(math.log10(floor)) - (sig_figs - 1)
    step = 10.0 ** exponent
    # Round after multiplying back up: ceil(x/step)*step lands on values like
    # 0.009040000000000001, and that dust would go straight into bench.ini.
    return round(math.ceil(floor / step) * step, max(0, -exponent))
