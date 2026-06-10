"""
Acquisition worker — Qt thread wrapper around the Qt-free experiment core.

Pattern: worker-object moved to a QThread (NOT a QThread subclass). The worker
emits signals carrying only plain data (str, ints, numpy/pandas objects) — never
widgets — so the GUI thread can update safely. Stop and Abort are threading.Events:
    - Stop  : checked between segments; the current segment finishes and is saved.
    - Abort : checked between segments AND inside the measure poll loop; the
              current segment is abandoned with no file written.
"""
import threading

from qtpy.QtCore import QObject, Signal

from spec_echem.experiment import run_one_segment


class AcquisitionWorker(QObject):
    segment_started = Signal(str, int, int)   # label, index (1-based), total
    segment_done = Signal(str, object)        # label, absorbance DataFrame
    status = Signal(str)
    finished = Signal(str)                    # 'done' | 'stopped' | 'aborted' | 'error'

    def __init__(self, spec, segments, dark, ref, wavelengths, data_root, added_path):
        super().__init__()
        self.spec = spec
        self.segments = segments
        self.dark = dark
        self.ref = ref
        self.wavelengths = wavelengths
        self.data_root = data_root
        self.added_path = added_path
        self.stop_event = threading.Event()
        self.abort_event = threading.Event()

    def request_stop(self):
        self.stop_event.set()

    def request_abort(self):
        self.abort_event.set()

    def run(self):
        try:
            total = len(self.segments)
            for i, seg in enumerate(self.segments):
                if self.abort_event.is_set():
                    self.finished.emit("aborted")
                    return
                if self.stop_event.is_set():
                    self.finished.emit("stopped")
                    return

                self.segment_started.emit(seg.label, i + 1, total)
                self.status.emit(
                    f"Armed for {seg.label} ({i + 1}/{total}) — waiting for Gamry trigger"
                )

                result = run_one_segment(
                    self.spec, seg, self.dark, self.ref, self.wavelengths,
                    self.data_root, self.added_path, self.abort_event,
                )
                if result is None:
                    self.finished.emit("aborted")
                    return

                absorb_df, path = result
                self.segment_done.emit(seg.label, absorb_df)
                self.status.emit(f"{seg.label} complete → {path.name}")

            self.finished.emit("done")
        except Exception as exc:  # noqa: BLE001 — surface any failure to the status log
            self.status.emit(f"Error: {exc}")
            self.finished.emit("error")
