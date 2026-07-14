"""
Per-run file logging. Qt-free.

One log file per run, named to match the data folder and written beside the data:
    {run_folder}/{name}.log
DEBUG and up goes to the file. The GUI attaches its own handler (in gui/workers.py)
to mirror INFO records to the status pane. Call configure_run_logging() at run
START — not at import time — and close_run_logging() when the run ends.
"""
import logging
from pathlib import Path

from spec_echem.build_info import build_id

RUN_LOGGER_NAME = "spec_echem.run"


def get_run_logger():
    return logging.getLogger(RUN_LOGGER_NAME)


def configure_run_logging(run_folder, name):
    """
    Attach a fresh DEBUG FileHandler at {run_folder}/{name}.log and return
    (logger, path). Any FileHandler from a previous run is removed/closed first.
    """
    logger = logging.getLogger(RUN_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
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
