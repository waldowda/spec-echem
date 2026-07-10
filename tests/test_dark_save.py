"""
Tests for the dark/reference serial naming behind the Instrument tab's
"Save Dark/Reference to File". Multiple saves in one day increment NNN so they
don't collide; overwriting stays available via the Save dialog choosing an
existing name. Only the pure naming helper is exercised (no widgets/QApplication).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gui.tabs.instrument_tab import _next_serial_path


@pytest.mark.parametrize("kind", ["dark", "ref"])
def test_next_serial_path_increments(tmp_path, kind):
    date = "20260704"
    p1 = _next_serial_path(tmp_path, date, kind)
    assert p1.name == f"20260704_{kind}_001.txt"
    p1.write_text("saved")                      # simulate the first save
    p2 = _next_serial_path(tmp_path, date, kind)
    assert p2.name == f"20260704_{kind}_002.txt"
    p2.write_text("saved")
    assert _next_serial_path(tmp_path, date, kind).name == f"20260704_{kind}_003.txt"


def test_next_serial_path_new_day_starts_fresh(tmp_path):
    (tmp_path / "20260704_dark_001.txt").write_text("x")
    assert _next_serial_path(tmp_path, "20260705", "dark").name == "20260705_dark_001.txt"


def test_dark_and_ref_series_are_independent(tmp_path):
    _next_serial_path(tmp_path, "20260704", "dark")
    (tmp_path / "20260704_dark_001.txt").write_text("x")
    # A saved dark does not bump the ref series.
    assert _next_serial_path(tmp_path, "20260704", "ref").name == "20260704_ref_001.txt"
