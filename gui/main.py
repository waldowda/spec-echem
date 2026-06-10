"""
Entry point for the spec-echem GUI.

Run from the repository root:
    python -m gui
    python -m gui.main
"""
import sys
from qtpy.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("spec-echem")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
