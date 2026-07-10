"""
Tests for spec_echem.logging_config — per-run file logging.
"""
import logging

from spec_echem.logging_config import (
    configure_run_logging, close_run_logging, get_run_logger, RUN_LOGGER_NAME,
)


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
