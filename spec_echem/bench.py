"""
Bench defaults — the settings that describe *this rig*, not *this experiment*.

Two different things were jumbled together before:

  * **Bench preferences** — the lamp/ND combo's usable wavelength range, the working
    integration time, where data lives on this machine, whether this machine can drive
    the Gamry from Python. They change when the *hardware* changes: rarely.
  * **Experiment settings** — sample, electrolyte, folder, CV vertices, potentials.
    They change every run, and are saved/loaded per experiment as JSON.

Bench values are read from plain INI files you can open, edit, back up, and paste into
an email. Precedence, lowest to highest:

    1. DEFAULT_SETTINGS      (code — the floor; guarantees the app always runs)
    2. config/defaults.ini   (repo-tracked — lab-wide, MACHINE-INDEPENDENT standards)
    3. the user bench file   (untracked, per-rig — THIS machine: data_root, Gamry mode)
    4. an experiment JSON    (only when you explicitly load one)

Machine-specific values must never live in the repo-tracked file: `data_root` differs
between the Windows instrument box and the Mac, so committing it would make every pull a
conflict.

The reader is deliberately forgiving. These files are meant to be hand-edited, so a typo
is a matter of when, not if: a bad value is skipped with a warning and the layer below it
stands, rather than crashing the GUI on launch.

No Qt. No hardware imports. Safe to unit-test anywhere.
"""
import configparser
import os
from pathlib import Path

APP_NAME = "spec-echem"
REPO_DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "defaults.ini"


def _opt_float(raw):
    """A float, or None for an empty value (= 'no crop' for the wavelength window)."""
    raw = raw.strip()
    if not raw or raw.lower() in ("none", "full"):
        return None
    return float(raw)


def _bool(raw):
    raw = raw.strip().lower()
    if raw in ("true", "yes", "1", "on"):
        return True
    if raw in ("false", "no", "0", "off"):
        return False
    raise ValueError(f"expected true/false, got {raw!r}")


def _str(raw):
    return raw.strip()


# The contract. A key not listed here is an EXPERIMENT setting and does not belong in a
# bench file — keeping this list closed is what stops it becoming a junk drawer.
BENCH_SCHEMA = {
    "spectrometer": {
        "wavelength_min": _opt_float,     # None = full range (no crop)
        "wavelength_max": _opt_float,
        "integration_time_ms": float,
        "scan_averages": int,
    },
    "linearity": {
        "lin_start_ms": float,
        "lin_stop_ms": float,
        "lin_steps": int,
        "lin_tolerance_pct": float,
        "lin_max_fill_pct": float,
    },
    "bench": {
        "data_root": _str,                # machine path — NEVER in the repo-tracked file
        "potentiostat_mode": _str,
        "save_dta": _bool,
        "trigger": _bool,
    },
    # Metrohm Autolab: install paths and the NOVA procedure templates. ALL of these
    # are machine-specific — like data_root, they must never go in the tracked
    # defaults.ini, or every pull is a conflict.
    "autolab": {
        "autolab_sdk": _str,              # EcoChemie.Autolab.Sdk assembly, no .dll
        "autolab_adx": _str,              # the Adk.x hardware driver
        "autolab_hdw": _str,              # this instrument's HardwareSetup XML
        "autolab_nox_cv": _str,           # standard CV procedure template
        "autolab_nox_ca": _str,           # chronoamperometry procedure template
        "autolab_dio_port": int,          # DioPortsP1 index; 0 = P1.A
        "autolab_dio_mask": int,          # which pins the pulse drives; 0xFF = all
        "autolab_pulse_delay_s": _opt_float,  # None = FHWait + template setup lag
        "autolab_setup_lag_cv_s": _opt_float,
        "autolab_setup_lag_ca_s": _opt_float,
        # True when the .nox carries its own FHDIO step (the Autolab raises P1.A
        # itself). Then Python does not pulse and the pulse-delay is unused.
        "autolab_trigger_in_procedure": _bool,
    },
}

# Flat key -> (section, parser)
_FLAT = {key: (section, parse)
         for section, keys in BENCH_SCHEMA.items()
         for key, parse in keys.items()}

BENCH_KEYS = tuple(_FLAT)


def _os_config_path():
    """The conventional per-user config location — used only as a fallback."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME / "bench.ini"


def user_bench_path():
    """
    This machine's bench file: `config/bench.ini`, right beside the tracked
    `config/defaults.ini` (and gitignored, because it holds machine paths).

    The conventional spot would be %APPDATA% / ~/.config, but that convention exists for
    multi-user machines and read-only installs in Program Files. Neither applies to a lab
    instrument running from a writable checkout on a shared account — and %APPDATA% is a
    HIDDEN folder, which makes a file you're meant to hand-edit, back up, and email
    needlessly hard to find. So: keep it with the code, where you already are.

    Falls back to the OS config dir only if the install directory isn't writable (e.g. a
    non-editable pip install into site-packages).
    """
    config_dir = REPO_DEFAULTS.parent
    if os.access(config_dir if config_dir.exists() else config_dir.parent, os.W_OK):
        return config_dir / "bench.ini"
    return _os_config_path()


def read_bench_file(path):
    """
    Parse one INI file into a {key: value} dict of known bench keys.

    Returns (values, warnings). Unknown keys and unparseable values are reported as
    warnings and skipped — never raised. A missing file is simply empty, not an error.
    """
    values, warnings = {}, []
    path = Path(path)
    if not path.exists():
        return values, warnings

    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        return values, [f"{path.name}: unreadable ({exc}); ignoring it."]

    for section in parser.sections():
        for key, raw in parser.items(section):
            if key not in _FLAT:
                warnings.append(f"{path.name}: unknown setting '{key}' ignored.")
                continue
            _, parse = _FLAT[key]
            try:
                values[key] = parse(raw)
            except (ValueError, TypeError):
                warnings.append(
                    f"{path.name}: '{key} = {raw}' isn't valid; using the default instead.")
    return values, warnings


def load_bench_defaults(repo_path=REPO_DEFAULTS, user_path=None):
    """
    Overlay the repo-tracked lab defaults with this machine's bench file.

    Returns (values, warnings). Apply the result on top of DEFAULT_SETTINGS.
    """
    user_path = user_bench_path() if user_path is None else Path(user_path)
    values, warnings = read_bench_file(repo_path)
    user_values, user_warnings = read_bench_file(user_path)
    values.update(user_values)                 # this machine wins over the lab default
    return values, warnings + user_warnings


def save_bench_defaults(settings, path=None):
    """
    Write the bench subset of `settings` to this machine's bench file.

    Only BENCH_KEYS are written — experiment values (sample, folder, CV vertices) are
    deliberately excluded, so clicking "Save as defaults" mid-experiment cannot quietly
    turn one run's parameters into the rig's defaults.
    """
    path = user_bench_path() if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    for section, keys in BENCH_SCHEMA.items():
        rows = {}
        for key in keys:
            if key not in settings:
                continue
            value = settings[key]
            rows[key] = "" if value is None else str(value)
        if rows:
            parser[section] = rows

    with path.open("w", encoding="utf-8") as fh:
        fh.write("# spec-echem bench defaults — THIS machine.\n"
                 "# Hand-editable: close the app first, since 'Save as defaults' rewrites\n"
                 "# this file. A bad value is ignored (with a warning), not fatal.\n"
                 "# Blank wavelength_min/max means the full spectrometer range (no crop).\n\n")
        parser.write(fh)
    return path


def apply_bench_defaults(settings, values):
    """Overlay bench values onto a settings dict, in place. Returns the dict."""
    for key in BENCH_KEYS:
        if key in values:
            settings[key] = values[key]
    return settings
