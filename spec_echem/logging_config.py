"""
File logging. Qt-free. Two logs, deliberately, because they answer different questions.

**Run log** — {run_folder}/{name}.log, one per run, written INSIDE the data folder so it
travels with the data: hand a folder to a collaborator and the record of how it was
produced goes along. Opened at run START by configure_run_logging(), closed at the end.

**App log** — {data_root}/logs/spec-echem.log, opened at LAUNCH by configure_app_logging()
and rotated nightly, keeping every day (nothing is deleted). This is the session
narrative: connecting an instrument, collecting a dark, a failed Connect — all of which
happen long before any run exists, and used to go nowhere but the shell. It is the log a
student goes back to when asking "what did I do this afternoon, and where did it go
wrong?" — so it is organised by DAY: spec-echem.log is today, spec-echem.log.2026-07-26
is yesterday.

The app handler lives on the PACKAGE logger (`spec_echem`), the parent of every module
logger, so anything any module logs lands in it — including the run logger, which
propagates up. Run records therefore appear in both; app records appear only in the app
log, since propagation only travels upward.

DEBUG and up goes to both files. The GUI attaches its own handler (gui/workers.py) to
mirror INFO records to the Run tab's status pane during a run.
"""
import logging
import os
import platform
import struct
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from spec_echem.build_info import build_id

APP_LOGGER_NAME = "spec_echem"
RUN_LOGGER_NAME = "spec_echem.run"

APP_LOG_NAME = "spec-echem.log"
# Rotate at midnight: spec-echem.log is always today, older days are
# spec-echem.log.2026-07-26. Chosen over size-based rotation because "send me the log
# from the day it broke" is the actual request — a single size-rotated file would run
# months on a lab machine and bury one afternoon in the middle of a term.
#
# 0 = keep every day, delete nothing. This is instrument provenance: at ~30 KB/day it
# costs ~11 MB/year, and the one time an old log matters is tracing a result months
# later — precisely when an automatic cutoff would already have discarded it. Old files
# are dated, so pruning by hand is easy if it ever becomes worth doing.
APP_LOG_BACKUP_DAYS = 0


def get_app_logger():
    return logging.getLogger(APP_LOGGER_NAME)


def get_run_logger():
    return logging.getLogger(RUN_LOGGER_NAME)


def configure_app_logging(data_root):
    """
    Open the rotating app log at {data_root}/logs/spec-echem.log and return its Path.
    Call once at launch. Returns None if the log can't be opened — a read-only or
    missing data root must not stop the application from starting.
    """
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    _remove_file_handlers(logger)

    try:
        folder = Path(data_root) / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / APP_LOG_NAME
        handler = TimedRotatingFileHandler(
            path, when="midnight", backupCount=APP_LOG_BACKUP_DAYS, encoding="utf-8")
    except OSError:
        return None

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    logger.addHandler(handler)

    _log_launch_banner(logger)
    return path


def _driver_status():
    """(avaspec, toolkitpy) importability, as the application itself sees it.

    Imported lazily: both modules import spec_echem.logging_config, so a top-level
    import here would be circular. Reports the REAL import result rather than mere
    presence on disk — "the file is there but the DLL isn't" is a failure mode worth
    telling apart, and it is what actually decides whether Python Gamry mode is offered.
    """
    try:
        from spec_echem.spectrometer import AVASPEC_AVAILABLE
    except Exception:      # noqa: BLE001 — a broken SDK must not stop the log opening
        AVASPEC_AVAILABLE = False
    try:
        from spec_echem.potentiostat import TOOLKITPY_AVAILABLE
    except Exception:      # noqa: BLE001
        TOOLKITPY_AVAILABLE = False
    return AVASPEC_AVAILABLE, TOOLKITPY_AVAILABLE


def _log_launch_banner(logger):
    """A banner per launch: the file covers a whole day of sessions, so this is what
    makes one findable when scrolling. Blank lines above and below because the
    timestamp/level prefix indents every line ~35 chars — without the whitespace the
    rule doesn't read as a break.

    The environment lines earn their place: "Python mode is greyed out" looks like a
    dead potentiostat but is almost always the wrong conda env (toolkitpy is 32-bit
    only). Recording it means any pasted log answers that question by itself.
    """
    avaspec_ok, toolkitpy_ok = _driver_status()
    env = os.environ.get("CONDA_DEFAULT_ENV") or Path(sys.prefix).name
    yes_no = lambda ok: "yes" if ok else "no"        # noqa: E731

    logger.info("")
    logger.info("=" * 60)
    logger.info("==============  SPEC-ECHEM LAUNCHED  =======================")
    logger.info("=" * 60)
    logger.info("  build   %s", build_id())
    logger.info("  python  %s (%d-bit), env %s",
                platform.python_version(), struct.calcsize("P") * 8, env)
    logger.info("  drivers avaspec: %s | toolkitpy: %s",
                yes_no(avaspec_ok), yes_no(toolkitpy_ok))
    logger.info("")


def app_log_path(data_root):
    """Where configure_app_logging() would write, without opening anything."""
    return Path(data_root) / "logs" / APP_LOG_NAME


def configure_run_logging(run_folder, name):
    """
    Attach a fresh DEBUG FileHandler at {run_folder}/{name}.log and return
    (logger, path). Any FileHandler from a previous run is removed/closed first.
    """
    logger = logging.getLogger(RUN_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    # Propagate up to the package logger so the run also appears in the app log — the
    # session narrative would have a run-shaped hole otherwise. Nothing above spec_echem
    # holds a handler, so this doesn't put anything on the shell.
    logger.propagate = True
    _remove_file_handlers(logger)

    run_folder = Path(run_folder)
    run_folder.mkdir(parents=True, exist_ok=True)
    path = run_folder / f"{name}.log"

    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    logger.addHandler(handler)

    # First line of every run log: the code that produced it. The log is appended to, so
    # this also marks where each run begins in a folder that gets re-run.
    logger.info("spec-echem %s", build_id())
    return logger, path


def close_run_logging():
    """Remove and close the run's FileHandler(s)."""
    _remove_file_handlers(get_run_logger())


def _remove_file_handlers(logger):
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()
