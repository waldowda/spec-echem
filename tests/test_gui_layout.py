"""
Layout regression tests for the GUI.

The first automated coverage of gui/. It exists because of a real bug: lengthening
the spectrometer connect-failure message widened the whole application window past
its half-column layout. A QLabel in a layout asks for its full text width and the
layout grants it — it does not clip — so any label holding text of uncontrolled
length (hardware error strings) has to wrap, and has to be unable to drive width.

Headless: forces the offscreen Qt platform, so it runs with no display.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtWidgets import QApplication            # noqa: E402
from gui.main_window import MainWindow             # noqa: E402

# The message a student sees with the spectrometer unplugged — the actual case.
LONG_ERROR = ("No Avantes spectrometer found. Check the USB cable, and close "
              "AvaSoft or any other program using the spectrometer.")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    win = MainWindow()
    win.show()          # size hints aren't computed for a window that never laid out
    yield win
    win.close()


def _connection_group_width(tab):
    """Minimum width the Spectrometer Connection group demands of the layout.

    activate() first: a size hint is cached until the layout is told its contents
    changed, so reading it straight after setText returns the stale value.
    """
    group = tab.spec_status.parentWidget()
    group.layout().activate()
    return group.minimumSizeHint().width()


def test_long_connect_error_does_not_widen_the_layout(window):
    tab = window.instrument_tab
    before = _connection_group_width(tab)

    tab.spec_status.setText("● Connect failed")
    tab.spec_detail.setText(LONG_ERROR)

    # Some slack for font differences between machines; the bug was +900 px.
    assert _connection_group_width(tab) <= before + 20


def test_error_text_inline_would_widen_the_layout(window):
    """Guards the test above: prove the failure mode is real, so a future refactor
    that puts the message back inline can't pass by accident."""
    tab = window.instrument_tab
    before = _connection_group_width(tab)
    tab.spec_status.setText(f"● Connect failed: {LONG_ERROR}")
    assert _connection_group_width(tab) > before + 100


def test_detail_labels_wrap_and_cannot_drive_width(window):
    from qtpy.QtWidgets import QSizePolicy
    tab = window.instrument_tab
    for label in (tab.spec_detail, tab.pstat_detail):
        assert label.wordWrap()
        assert label.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored


def test_connect_failure_message_is_still_shown_somewhere(window):
    """Not widening the window is worthless if the fix hid the message — that was
    the original complaint (a failed connect showed nothing the student could see)."""
    tab = window.instrument_tab
    tab.simulated_check.setChecked(False)
    tab.on_connect()   # no hardware in CI -> takes the failure path

    assert "failed" in tab.spec_status.text().lower()
    assert tab.spec_detail.text()          # the reason is on screen, not just logged
