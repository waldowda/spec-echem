"""
Entry point for the spec-echem GUI.

Run from the repository root:
    python -m gui
    python -m gui.main
"""
import sys
from qtpy.QtWidgets import QApplication

from spec_echem.bench import load_bench_defaults
from spec_echem.logging_config import configure_app_logging
from spec_echem.settings import DEFAULT_SETTINGS
from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("spec-echem")

    # Open the app log BEFORE the window exists, so instrument connections and any
    # startup failure are on record. data_root comes from the bench file, which is
    # read here rather than from the window for exactly that ordering reason.
    bench_values, _ = load_bench_defaults()
    configure_app_logging(bench_values.get("data_root", DEFAULT_SETTINGS["data_root"]))

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
