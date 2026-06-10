"""
Main application window: holds shared state and the 4-tab layout.

Shared state lives here (the Qt-side coordinator):
    - settings: canonical experiment settings dict (single source of truth)
    - spec: connected spectrometer instance (real or fake), set by the Instrument tab
    - dark / ref / wavelengths: per-run calibration, set by the Instrument tab
The Qt-free orchestration (Experiment class) is added when the Run tab is wired.
"""
from qtpy.QtWidgets import QMainWindow, QTabWidget

from spec_echem.settings import DEFAULT_SETTINGS
from gui.tabs.instrument_tab import InstrumentTab
from gui.tabs.parameters_tab import ParametersTab
from gui.tabs.run_tab import RunTab
from gui.tabs.results_tab import ResultsTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("spec-echem — Spectroelectrochemistry Control")
        self.resize(1000, 700)

        # --- shared state ---
        self.settings = DEFAULT_SETTINGS.copy()
        self.spec = None
        self.dark = None
        self.ref = None
        self.wavelengths = None
        self.results = {}   # segment label -> absorbance DataFrame (populated during a run)

        # --- tabs ---
        self.tabs = QTabWidget()
        self.instrument_tab = InstrumentTab(self)
        self.parameters_tab = ParametersTab(self)
        self.run_tab = RunTab(self)
        self.results_tab = ResultsTab(self)

        self.tabs.addTab(self.instrument_tab, "1. Instrument")
        self.tabs.addTab(self.parameters_tab, "2. Parameters")
        self.tabs.addTab(self.run_tab, "3. Run")
        self.tabs.addTab(self.results_tab, "4. Results")

        self.setCentralWidget(self.tabs)

        # Populate parameter widgets from the default settings
        self.parameters_tab.populate_from(self.settings)
        self.instrument_tab.populate_from(self.settings)

    # --- settings coordination across the input tabs ---

    def collect_settings(self):
        """Read every input tab's widgets into the canonical settings dict."""
        self.instrument_tab.collect_into(self.settings)
        self.parameters_tab.collect_into(self.settings)
        return self.settings

    def apply_settings(self, settings):
        """Push a settings dict into every input tab's widgets."""
        self.settings = settings
        self.instrument_tab.populate_from(settings)
        self.parameters_tab.populate_from(settings)
