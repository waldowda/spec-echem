"""
Settings load/save for spec-echem experiments.
No Qt imports. No hardware imports.
Validation happens at the GUI boundary, not here.
"""
import json
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
    "data_root": r"C:\Users\inst-chem\Documents\specechem_data",
    "data_folder": "",          # format: YYYYMMDD_Description

    # --- Sample info (documentation) ---
    "sample_name": "",
    "electrolyte": "",
    "notes": "",

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
    # historical behaviour; safe only while nothing else shares the port. Set a
    # single bit once examples/probe_dio_pin.py has found the wired pin.
    # Parsed by parse_dio_mask() below, so 0x04 / 0b100 / 4 all mean the same thing.
    "autolab_dio_mask": 0xFF,
    # The template's FHWait, in seconds. None = leave whatever the .nox carries.
    # The stock CA template ships 5.0 s, and the driver writes the DOPING potential
    # into the setpoint command that runs BEFORE it — so the cell sits at the doping
    # potential for those 5 s with nothing recording, and the steepest part of the
    # transient is lost. Set this to shrink or remove that window.
    "autolab_wait_s": None,
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
