"""
Tests for the dark-spectrum serial naming behind the Instrument tab's
"Save Dark to File". Multiple darks in one day increment NNN so they don't
collide; overwriting stays available via the Save dialog choosing an existing
name. Only the pure naming helper is exercised (no widgets / QApplication).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.tabs.instrument_tab import _next_dark_path


def test_next_dark_path_serial_increments(tmp_path):
    date = "20260704"
    p1 = _next_dark_path(tmp_path, date)
    assert p1.name == "20260704_dark_001.txt"
    p1.write_text("saved")                      # simulate the first save
    p2 = _next_dark_path(tmp_path, date)
    assert p2.name == "20260704_dark_002.txt"
    p2.write_text("saved")
    assert _next_dark_path(tmp_path, date).name == "20260704_dark_003.txt"


def test_next_dark_path_new_day_starts_fresh(tmp_path):
    (tmp_path / "20260704_dark_001.txt").write_text("x")
    # A different day begins its own 001 series.
    assert _next_dark_path(tmp_path, "20260705").name == "20260705_dark_001.txt"
