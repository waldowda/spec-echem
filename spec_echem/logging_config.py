"""
File logging. Qt-free. Two logs, deliberately, because they answer different questions.

**Run log** — {run_folder}/{name}.log, one per run, written INSIDE the data folder so it
travels with the data: hand a folder to a collaborator and the record of how it was
produced goes along. Opened at run START by configure_run_logging(), closed at the end.

**App log** — {data_root}/logs/spec-echem.log, opened at LAUNCH by configure_app_logging()
and rotated nightly (30 days kept) so it never needs maintenance. This is the session
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
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from spec_echem.build_info import build_id

APP_LOGGER_NAME = "spec_echem"
RUN_LOGGER_NAME = "spec_echem.run"

APP_LOG_NAME = "spec-echem.log"
# Rotate at midnight, keeping a month: spec-echem.log is always today, and older days
# are spec-echem.log.2026-07-26. Chosen over size-based rotation because "send me the
# log from the day it broke" is the actual request — a single size-rotated file would
# run months on a lab machine and bury one afternoon in the middle of a term.
APP_LOG_BACKUP_DAYS = 30


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

    # A banner per launch: the file is append-and-rotate, so this is what makes one
    # session findable in a log covering weeks of them.
    logger.info("=" * 60)
    logger.info("spec-echem %s starting", build_id())
    return path


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
