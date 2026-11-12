"""
Film size measured after the run (planned 2026-11-11).

Thickness and immersed area are usually estimates at Start; the film is measured
(profilometer, a photo of the wetted area) only after the electrochemistry. The
measured values go into `{folder}_film.json` beside the run, never into the run's
metadata, so both what was assumed and what was measured stay on record.

The file is an append-only history. The first entry is the run's own values, copied
from its metadata when the file is created; every edit adds an entry; the latest
entry is the one used. Nothing is ever overwritten.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from spec_echem.build_info import build_id

FILM_FORMAT = "spec-echem film size"
FILM_VERSION = 1
STATUSES = ("estimate", "measured")
KEYS = ("thickness_nm", "thickness_u_nm", "area_cm2", "area_u_cm2")


class FilmFileError(ValueError):
    """The film file exists but cannot be read; the caller says so rather than
    quietly falling back to the run's estimate."""


def film_path(folder):
    folder = Path(folder)
    return folder / f"{folder.name}_film.json"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def entry_from_settings(settings, when=None):
    """The run's own film size as a history entry. A run from before the status
    field existed says so in its source rather than claiming either status."""
    settings = settings or {}
    status = settings.get("film_size_status")
    source = "run metadata"
    if status not in STATUSES:
        status, source = "estimate", "run metadata (status not recorded)"
    return {
        "thickness_nm": float(settings.get("film_thickness_nm") or 0.0),
        "thickness_u_nm": float(settings.get("film_thickness_u_nm") or 0.0),
        "area_cm2": float(settings.get("film_area_cm2") or 0.0),
        "area_u_cm2": float(settings.get("film_area_u_cm2") or 0.0),
        "status": status,
        "note": "",
        "source": source,
        "time": when or "",
        "build": "",
    }


def new_entry(thickness_nm, area_cm2, status, thickness_u_nm=0.0, area_u_cm2=0.0,
              note="", source="Charge tab"):
    """One edit, validated. Uncertainty 0 means two significant figures, as on the
    Parameters tab."""
    entry = {"thickness_nm": float(thickness_nm), "thickness_u_nm": float(thickness_u_nm),
             "area_cm2": float(area_cm2), "area_u_cm2": float(area_u_cm2),
             "status": status, "note": str(note or "").strip(), "source": source,
             "time": _now(), "build": build_id()}
    _validate(entry)
    return entry


def _validate(entry):
    if entry.get("status") not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, not {entry.get('status')!r}")
    for key in KEYS:
        if not isinstance(entry.get(key), (int, float)):
            raise ValueError(f"{key} is missing or not a number")
    if entry["thickness_nm"] <= 0 or entry["area_cm2"] <= 0:
        raise ValueError("thickness and area must both be greater than zero")
    if entry["thickness_u_nm"] < 0 or entry["area_u_cm2"] < 0:
        raise ValueError("an uncertainty cannot be negative")


def read_history(folder):
    """The entries in the film file, oldest first; [] when there is no file."""
    path = film_path(folder)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["entries"]
        for entry in entries:
            _validate(entry)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FilmFileError(f"{path.name} could not be read: {exc}") from exc
    return entries


def current(folder, recorded_settings, run_started=None):
    """(the entry in use, the full history). Without a film file the history is the
    run's own values alone, not yet written anywhere."""
    history = read_history(folder) if folder is not None else []
    if not history:
        history = [entry_from_settings(recorded_settings, run_started)]
    return history[-1], history


def add_entry(folder, entry, recorded_settings, run_started=None):
    """Append `entry`. The first write also records the run's own values as entry 1,
    so the file alone shows what was assumed before anything was measured."""
    _validate(entry)
    history = read_history(folder)
    if not history:
        first = entry_from_settings(recorded_settings, run_started)
        try:
            _validate(first)
            history = [first]
        except ValueError:
            history = []        # an old run that recorded no film size at all
    history.append(entry)
    path = film_path(folder)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"format": FILM_FORMAT, "version": FILM_VERSION,
                               "entries": history}, indent=2), encoding="utf-8")
    os.replace(tmp, path)        # never a half-written history
    return history


def copied_entry(source_folder, source_settings, source_run_started=None):
    """The entry in use in another run, as a new entry for this one: a film measured
    once and run several times is entered once."""
    entry, _history = current(source_folder, source_settings, source_run_started)
    copy = dict(entry)
    name = Path(source_folder).name
    copy["source"] = f"copied from {name} ({entry.get('source', '')})"
    copy["time"], copy["build"] = _now(), build_id()
    return copy


def describe(entry):
    """'142 nm x 1.6 cm2, measured 2026-11-14 (profilometer)' or
    '150 nm x 1.6 cm2, ESTIMATE (provisional)' -- what every plot and CSV says."""
    size = f"{entry['thickness_nm']:g} nm x {entry['area_cm2']:g} cm2"
    if entry["status"] == "measured":
        when = (entry.get("time") or "")[:10]
        text = f"{size}, measured" + (f" {when}" if when else "")
        return text + (f" ({entry['note']})" if entry.get("note") else "")
    return f"{size}, ESTIMATE (provisional)" + (
        f" ({entry['note']})" if entry.get("note") else "")
