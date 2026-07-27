"""
Tests for spec_echem.logging_config — the per-run log and the app log.
"""
import logging

import pytest

from spec_echem.build_info import build_id
from spec_echem.logging_config import (
    configure_run_logging, close_run_logging, get_run_logger, RUN_LOGGER_NAME,
    configure_app_logging, get_app_logger, app_log_path, APP_LOGGER_NAME,
    APP_LOG_BACKUP_DAYS, APP_LOG_NAME,
)


@pytest.fixture
def app_log(tmp_path):
    """Open an app log under tmp_path and always tear it down — the handler sits on
    the package logger, which is process-global, so a leak would pollute later tests."""
    path = configure_app_logging(tmp_path)
    yield path
    logger = get_app_logger()
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()


def test_log_file_created_with_content(tmp_path):
    folder = tmp_path / "20250715_Test"
    logger, path = configure_run_logging(folder, "20250715_Test")
    logger.info("info message")
    logger.debug("debug detail")
    close_run_logging()

    assert path == folder / "20250715_Test.log"
    assert path.exists()
    text = path.read_text()
    assert "info message" in text
    assert "debug detail" in text   # file handler is DEBUG level


def test_close_removes_file_handlers(tmp_path):
    configure_run_logging(tmp_path / "run1", "run1")
    close_run_logging()
    logger = get_run_logger()
    assert not any(isinstance(h, logging.FileHandler) for h in logger.handlers)


def test_reconfigure_does_not_accumulate_handlers(tmp_path):
    configure_run_logging(tmp_path / "run1", "run1")
    configure_run_logging(tmp_path / "run2", "run2")
    logger = get_run_logger()
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1   # old run's handler was removed
    close_run_logging()


def test_logger_name():
    assert RUN_LOGGER_NAME == "spec_echem.run"
    assert APP_LOGGER_NAME == "spec_echem"


def test_app_log_opens_at_the_documented_path(app_log, tmp_path):
    assert app_log == app_log_path(tmp_path) == tmp_path / "logs" / "spec-echem.log"
    assert "SPEC-ECHEM LAUNCHED" in app_log.read_text()   # launch banner


def test_launch_banner_records_the_environment(app_log):
    """The banner carries the interpreter and driver availability because "Python mode
    is greyed out" reads like a dead potentiostat but is nearly always the wrong conda
    env — toolkitpy is 32-bit only. A pasted log should answer that without asking."""
    import platform
    import struct

    text = app_log.read_text()
    assert platform.python_version() in text
    assert f"{struct.calcsize('P') * 8}-bit" in text
    assert "toolkitpy:" in text and "avaspec:" in text
    assert build_id() in text


def test_app_log_captures_records_from_any_module(app_log):
    """The whole point: a module logging before any run exists still lands somewhere.
    Uses the spectrometer's real logger name, since Connect is the motivating case."""
    logging.getLogger("spec_echem.spectrometer").info("AVS_Init returned: 0")
    assert "AVS_Init returned: 0" in app_log.read_text()


def test_app_log_also_captures_the_run(app_log, tmp_path):
    """Run records must propagate up, or the session narrative has a run-shaped hole."""
    configure_run_logging(tmp_path / "run1", "run1")
    get_run_logger().info("Run started: 6 segments.")
    close_run_logging()
    assert "Run started: 6 segments." in app_log.read_text()


def test_app_log_rotates_daily_and_deletes_nothing(app_log):
    """Rotation is by DAY, not size: "send me the log from the day it broke" is the
    request this has to serve, and a size-rotated file would span a whole term.

    backupCount of 0 means keep everything — this is instrument provenance, and an
    automatic cutoff would discard exactly the old log someone eventually needs.
    """
    from logging.handlers import TimedRotatingFileHandler
    handlers = [h for h in get_app_logger().handlers
                if isinstance(h, logging.FileHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, TimedRotatingFileHandler)
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == APP_LOG_BACKUP_DAYS == 0


def test_old_daily_logs_survive_a_rollover(app_log):
    """Behavioural, not just the flag: getFilesToDelete() still LISTS old files even
    with backupCount 0 — only a guard in doRollover spares them. Assert the files are
    actually still on disk, so a future handler swap can't quietly start deleting."""
    import time
    old_days = ["2025-08-01", "2025-12-25", "2026-03-02"]
    for day in old_days:
        (app_log.parent / f"{APP_LOG_NAME}.{day}").write_text(f"old day {day}")

    handler = next(h for h in get_app_logger().handlers if hasattr(h, "doRollover"))
    handler.rolloverAt = time.time() - 1     # force a roll without waiting for midnight
    get_app_logger().info("triggers the rollover")

    for day in old_days:
        assert (app_log.parent / f"{APP_LOG_NAME}.{day}").exists(), \
            f"{day} was deleted — the app log must not discard instrument history"


def test_app_log_failure_does_not_stop_startup(tmp_path):
    """A read-only or unusable data root must not prevent the app from launching."""
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory")   # mkdir will fail against a file
    assert configure_app_logging(tmp_path) is None
