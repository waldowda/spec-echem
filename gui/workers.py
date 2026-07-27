"""
Acquisition worker — Qt thread wrapper around the Qt-free experiment core.

Pattern: worker-object moved to a QThread (NOT a QThread subclass). The worker
emits signals carrying only plain data (str, ints, numpy/pandas objects) — never
widgets — so the GUI thread can update safely. Stop and Abort are threading.Events:
    - Stop  : checked between segments; the current segment finishes and is saved.
    - Abort : checked between segments AND inside the measure poll loop; the
              current segment is abandoned with no file written.
"""
import logging
import threading

from qtpy.QtCore import QObject, Signal

from spec_echem.experiment import run_one_segment
from spec_echem.logging_config import get_run_logger


class QtLogHandler(logging.Handler):
    """
    Bridges logging records to a Qt signal so the GUI status pane mirrors the
    log. Lives here (not in logging_config) to keep the core Qt-free. The sink
    is a bound Qt signal's emit, which is thread-safe across the worker thread.
    """

    def __init__(self, emit_func, level=logging.INFO):
        super().__init__(level)
        self._emit = emit_func
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self._emit(self.format(record))


class AcquisitionWorker(QObject):
    segment_started = Signal(str, int, int)   # label, index (1-based), total
    segment_done = Signal(str, object)        # label, absorbance DataFrame
    status = Signal(str)
    finished = Signal(str)                    # 'done' | 'stopped' | 'aborted' | 'error'

    def __init__(self, spec, segments, dark, ref, wavelengths, data_root, added_path,
                 potentiostat=None):
        super().__init__()
        self.spec = spec
        self.segments = segments
        self.dark = dark
        self.ref = ref
        self.wavelengths = wavelengths
        self.data_root = data_root
        self.added_path = added_path
        self.potentiostat = potentiostat
        self.stop_event = threading.Event()
        self.abort_event = threading.Event()

    def request_stop(self):
        self.stop_event.set()

    def request_abort(self):
        self.abort_event.set()
        if self.potentiostat is not None:
            self.potentiostat.stop()

    def run(self):
        logger = get_run_logger()
        ui_handler = QtLogHandler(self.status.emit)
        logger.addHandler(ui_handler)
        reason = "done"
        try:
            if self.potentiostat is not None:
                self.potentiostat.open()
            total = len(self.segments)
            logger.info("Run started: %d segments.", total)
            for i, seg in enumerate(self.segments):
                if self.abort_event.is_set():
                    reason = "aborted"
                    break
                if self.stop_event.is_set():
                    reason = "stopped"
                    break

                self.segment_started.emit(seg.label, i + 1, total)
                # Logged BEFORE run_one_segment, which does the Gamry setup first and
                # only then arms — so this states intent, not that arming has happened.
                logger.info("Starting %s (%d/%d) — Gamry setup, then arm and wait for trigger",
                            seg.label, i + 1, total)
                logger.debug("%s: %d points @ %.4gs, trigger=%s",
                             seg.label, seg.num_points, seg.delta_time, seg.trigger)

                result = run_one_segment(
                    self.spec, seg, self.dark, self.ref, self.wavelengths,
                    self.data_root, self.added_path, self.abort_event,
                    self.potentiostat,
                )
                if result is None:
                    reason = "aborted"
                    break

                absorb_df, path = result
                self.segment_done.emit(seg.label, absorb_df)
                logger.info("%s complete → %s", seg.label,
                            path.name if path is not None else "discarded (not saved)")
        except Exception:  # noqa: BLE001 — surface any failure to the log + UI
            logger.exception("Acquisition error")
            reason = "error"
        finally:
            if self.potentiostat is not None:
                try:
                    self.potentiostat.close()
                except Exception:  # noqa: BLE001 — never let cleanup mask the run result
                    logger.exception("Potentiostat close error")
            logger.info("Run finished: %s.", reason)
            logger.removeHandler(ui_handler)
            self.finished.emit(reason)
