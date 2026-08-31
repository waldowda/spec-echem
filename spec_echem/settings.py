"""
Settings load/save for spec-echem experiments.
No Qt imports. No hardware imports.
Validation happens at the GUI boundary, not here.
"""
import json
from pathlib import Path

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
    "autolab_pulse_delay_s": None,  # None = use the procedure's own wait window

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


def load_settings(path):
    """
    Load settings from a JSON file.
    Any keys missing from the file are filled in from DEFAULT_SETTINGS.

    Args:
        path: str or Path to the settings JSON file

    Returns:
        dict with all settings keys present
    """
    with open(path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    settings = DEFAULT_SETTINGS.copy()
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
