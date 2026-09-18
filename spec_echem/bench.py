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

from spec_echem.settings import parse_dio_mask

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


def _opt_bool(raw):
    """A bool, or None for an empty value (= leave whatever the .nox carries)."""
    raw = raw.strip()
    if not raw or raw.lower() in ("none", "default", "template"):
        return None
    return _bool(raw)


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
        # parse_dio_mask, not int: a mask is naturally written 0x04, and bare int()
        # would reject that, warn, and silently fall back to all eight pins.
        "autolab_dio_mask": parse_dio_mask,  # which pins the pulse drives; 0xFF = all
        "autolab_wait_s": _opt_float,     # write FHWait; blank = leave the .nox alone
        "autolab_ca_fast_options": _opt_bool,  # FHLevel UseFastOptions; blank = leave
        "autolab_ca_mode": _str,          # "procedure" (default) or "ei"
        "autolab_current_range": _str,    # Ei mode only, e.g. CR10_1mA; blank = leave
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


NEWLINE = chr(10)

HEADER_COMMENT = """# spec-echem bench defaults -- THIS machine.
# Hand-editable: an existing file is edited IN PLACE, so comments you add here
# survive 'Save as defaults'. A bad value is ignored (with a warning), not fatal.
# Blank wavelength_min/max means the full spectrometer range (no crop).
"""


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
        # `[detector.<serial>]` holds what was measured FROM a specific detector, not
        # settings for this rig. Skip it silently -- warning about it would train the
        # user to ignore warnings, and BENCH_SCHEMA stays closed either way.
        if section.startswith(DETECTOR_SECTION_PREFIX):
            continue
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

    Only BENCH_KEYS are written -- experiment values (sample, folder, CV vertices) are
    deliberately excluded, so clicking "Save as defaults" mid-experiment cannot quietly
    turn one run's parameters into the rig's defaults.

    An EXISTING file is edited in place, comments and all. This file is hand-maintained
    and its comments carry measured bench findings; a `configparser` round-trip would
    drop every one of them, so a single "Save as defaults" click would erase the record
    of the work behind the settings. Per-detector `[detector.*]` sections survive for
    the same reason: nothing here knows what they mean, so nothing here may discard
    them.
    """
    path = user_bench_path() if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    updates = {}
    for section, keys in BENCH_SCHEMA.items():
        rows = {}
        for key in keys:
            if key not in settings:
                continue
            value = settings[key]
            rows[key] = "" if value is None else str(value)
        if rows:
            updates[section] = rows

    if path.exists():
        text = update_ini_text(path.read_text(encoding="utf-8"), updates)
    else:
        parts = [HEADER_COMMENT]
        for section, rows in updates.items():
            parts.append("[" + section + "]")
            parts.extend(f"{key} = {value}" for key, value in rows.items())
            parts.append("")
        text = NEWLINE.join(parts).rstrip(NEWLINE) + NEWLINE

    path.write_text(text, encoding="utf-8")
    return path


def apply_bench_defaults(settings, values):
    """Overlay bench values onto a settings dict, in place. Returns the dict."""
    for key in BENCH_KEYS:
        if key in values:
            settings[key] = values[key]
    return settings


# --- Per-detector records -------------------------------------------------------
#
# Not bench settings. A bench setting says how THIS RIG is configured and is a choice;
# these are facts MEASURED FROM a specific piece of hardware, keyed by its serial, so
# they stay correct if a detector ever moves between machines. Kept out of
# BENCH_SCHEMA deliberately -- that list is a closed contract for hand-editable
# preferences, and a growing set of per-serial facts would turn it into a junk drawer.

DETECTOR_SECTION_PREFIX = "detector."
DETECTOR_FLOOR_KEY = "min_integration_ms"


def detector_section_name(serial):
    """The INI section holding what we know about one detector."""
    return DETECTOR_SECTION_PREFIX + str(serial)


def read_detector_floor(serial, path=None):
    """
    The minimum integration time recorded for this detector, in ms, or None.

    Only a FALLBACK. The hardware is asked at every Connect (~70 ms, MEASURED, and
    bit-reproducible), because a stale stored floor fails silently in exactly the way
    the probe exists to prevent. This is what to use when there is no device to ask.
    """
    if not serial:
        return None
    path = user_bench_path() if path is None else Path(path)
    if not Path(path).exists():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        value = float(parser.get(detector_section_name(serial), DETECTOR_FLOOR_KEY))
    except (configparser.Error, ValueError, TypeError):
        return None
    return value if value > 0 else None


def save_detector_floor(serial, floor_ms, path=None):
    """
    Record a detector's measured floor, keyed by serial. Returns the path written, or
    None if nothing needed writing.

    Edits the file as TEXT rather than rewriting it through configparser, because this
    file is hand-maintained and its comments carry measured bench findings -- a
    configparser round-trip silently drops every one of them.

    Writes nothing when the stored value already matches, so a Connect that merely
    confirms what is on disk (the normal case) does not touch the file at all.
    """
    if not serial or floor_ms is None:
        return None
    try:
        floor = float(floor_ms)
    except (TypeError, ValueError):
        return None
    if not floor > 0:
        return None

    path = user_bench_path() if path is None else Path(path)
    if read_detector_floor(serial, path) == floor:
        return None

    header = "[" + detector_section_name(serial) + "]"
    row = DETECTOR_FLOOR_KEY + " = " + repr(floor)
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    if header in text:
        out, in_section, replaced = [], False, False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == header:
                in_section = True
                out.append(line)
                continue
            if in_section and stripped.startswith("[") and stripped.endswith("]"):
                in_section = False
            if in_section and stripped.lower().startswith(DETECTOR_FLOOR_KEY):
                out.append(row)
                replaced = True
                continue
            out.append(line)
        if not replaced:                       # section exists but the key does not
            out.append(row)
        text = "\n".join(out) + "\n"
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n".join([
            "",
            header,
            "# MEASURED from this detector, not chosen. Below this the SDK REJECTS a",
            "# measurement outright (code -11) rather than clamping to it.",
            row,
            "",
        ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_detector_sections(path):
    """Every `[detector.*]` section in a bench file, as {section_name: {key: raw}}."""
    path = Path(path)
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return {}
    return {name: dict(parser.items(name))
            for name in parser.sections()
            if name.startswith(DETECTOR_SECTION_PREFIX)}


# --- Comment-preserving INI editing ---------------------------------------------

def update_ini_text(text, updates):
    """
    Apply {section: {key: value}} to INI text, KEEPING comments and layout.

    `configparser` cannot do this. It parses to a dict and re-emits, so every comment
    in the file is silently dropped on the next write. That matters here because
    `config/bench.ini` is hand-maintained and its comments carry measured bench
    findings -- why a current range was chosen, which DIO pin actually fires the
    trigger, what a wait parameter was proven to do. Losing those to a "Save as
    defaults" click destroys the record of the work that produced the settings.

    Rules: an existing key is rewritten where it sits, so the comment above it still
    applies to it. A new key is appended to its section. A new section is appended to
    the file. Keys already present but not mentioned in `updates` are left alone.
    """
    lines = text.splitlines()
    # Where each section starts, and where its last content line is.
    section_of_line, section_start, section_end = {}, {}, {}
    current = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            section_start[current] = i
            section_end[current] = i
        elif current is not None:
            section_of_line[i] = current
            if stripped and not stripped.startswith(("#", ";")):
                section_end[current] = i

    # Existing "key = value" lines, by (section, key).
    located = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip().lower()
        section = section_of_line.get(i)
        if section is not None:
            located[(section, key)] = i

    inserts, appends = [], []
    for section, rows in updates.items():
        for key, value in rows.items():
            row = f"{key} = {value}"
            where = located.get((section, key.lower()))
            if where is not None:
                lines[where] = row                       # rewrite in place
            elif section in section_start:
                inserts.append((section_end[section] + 1, row))
            else:
                appends.append((section, row))

    # Insert from the bottom up so earlier indices stay valid.
    for at, row in sorted(inserts, key=lambda pair: -pair[0]):
        lines.insert(at, row)

    out = "\n".join(lines).rstrip("\n")
    pending = {}
    for section, row in appends:
        pending.setdefault(section, []).append(row)
    for section, rows in pending.items():
        out += "\n\n[" + section + "]\n" + "\n".join(rows)
    return out + "\n"
