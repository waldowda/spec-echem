"""The user manual, inside the app (requested 2026-11-08: there was no way to reach it).

docs/manual.md rendered by Qt's own Markdown support (Qt 5.14+), so it needs no
browser, no network and no extra package, and always matches the code it ships with.
Links to the other docs/*.md files open in the same window; web links in the browser.
"""
from pathlib import Path

from qtpy.QtCore import QUrl
from qtpy.QtWidgets import (QDialog, QHBoxLayout, QPushButton, QTextBrowser,
                            QVBoxLayout)

MANUAL_PATH = Path(__file__).resolve().parents[2] / "docs" / "manual.md"
MANUAL_WEB = "https://github.com/waldowda/spec-echem/blob/gui-dev/docs/manual.md"


class ManualDialog(QDialog):
    """Non-modal: read the manual beside the tab it describes."""

    def __init__(self, parent=None, path=MANUAL_PATH):
        super().__init__(parent)
        self.setWindowTitle("spec-echem manual")
        self.resize(900, 750)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser)
        row = QHBoxLayout()
        back = QPushButton("Back", self)
        back.clicked.connect(self.browser.backward)
        self.browser.backwardAvailable.connect(back.setEnabled)
        back.setEnabled(False)
        row.addWidget(back)
        row.addStretch()
        close = QPushButton("Close", self)
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        path = Path(path)
        if path.is_file():
            self.browser.setSearchPaths([str(path.parent)])
            self.browser.setSource(QUrl.fromLocalFile(str(path)))
        else:
            self.browser.setMarkdown(
                f"# Manual not found\n\nExpected at `{path}`.\n\n"
                f"Online copy: <{MANUAL_WEB}>")


def open_manual(parent):
    """Show the manual, reusing the window if it is already open."""
    dialog = getattr(parent, "_manual_dialog", None)
    if dialog is None:
        dialog = ManualDialog(parent)
        parent._manual_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
