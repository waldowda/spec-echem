"""
Main application window: holds shared state and the 4-tab layout.

Shared state lives here (the Qt-side coordinator):
    - settings: canonical experiment settings dict (single source of truth)
    - spec: connected spectrometer instance (real or fake), set by the Instrument tab
    - dark / ref / wavelengths: per-run calibration, set by the Instrument tab
The Qt-free orchestration (Experiment class) is added when the Run tab is wired.
"""
from qtpy.QtWidgets import QMainWindow, QTabWidget

from spec_echem.bench import (
    apply_bench_defaults, load_bench_defaults, user_bench_path,
)
from spec_echem.build_info import build_id
from spec_echem.data import echem_txt_path, DATA_TYPE_CV, segment_potential
from spec_echem.gamry_data import measured_potential
from spec_echem.settings import DEFAULT_SETTINGS
from gui.tabs.instrument_tab import InstrumentTab
from gui.tabs.parameters_tab import ParametersTab
from gui.tabs.run_tab import RunTab
from gui.tabs.results_tab import ResultsTab
from gui.tabs.analysis_tab import AnalysisTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Build id in the title: a student sending a screenshot tells you their exact build.
        self.setWindowTitle(
            "spec-echem {} — Spectroelectrochemistry Control".format(build_id()))
        self.resize(1000, 700)

        # --- shared state ---
        # Code defaults, then the repo-tracked lab defaults, then THIS machine's bench
        # file. An experiment settings JSON (loaded explicitly) still overrides all of it.
        self.settings = DEFAULT_SETTINGS.copy()
        # Settings belonging to a run LOADED from disk, read from its metadata JSON.
        # Graph titles and the analysis ladder must describe the run on screen, not
        # whatever is currently typed into the Parameters tab for the next one.
        self.loaded_run_settings = None
        self._potential_cache = {}   # (folder, type, n) -> volts
        bench_values, self.bench_warnings = load_bench_defaults()
        apply_bench_defaults(self.settings, bench_values)
        self.bench_values = bench_values      # kept: bench_base() rebuilds from these
        self.bench_loaded = sorted(bench_values)
        self.spec = None
        self.dark = None
        self.ref = None
        self.wavelengths = None
        self.results = {}   # segment label -> absorbance DataFrame (populated during a run)
        self.run_folder = None       # Path to the active/last run folder (echem file lookup)
        self.segments_by_label = {}  # segment label -> Segment (echem file type/number)
        # Which physical instruments are attached, as reported by their Connect buttons.
        # Recorded into the run log + metadata at Start so a data folder names its
        # hardware, not just its settings. None = never connected this session.
        self.spec_identity = None
        # Shortest exposure the connected detector accepts, in ms. Filled in at
        # Connect; travels into the run metadata so a data folder records what its
        # detector could actually do, not just what was asked of it.
        self.spec_min_integration_ms = None
        # The raw bisect result behind it, kept for provenance.
        self.spec_min_integration_measured_ms = None
        self.pstat_identity = None

        # --- tabs ---
        self.tabs = QTabWidget()
        self.instrument_tab = InstrumentTab(self)
        self.parameters_tab = ParametersTab(self)
        self.run_tab = RunTab(self)
        self.results_tab = ResultsTab(self)
        self.analysis_tab = AnalysisTab(self)

        self.tabs.addTab(self.instrument_tab, "1. Instrument")
        self.tabs.addTab(self.parameters_tab, "2. Parameters")
        self.tabs.addTab(self.run_tab, "3. Run")
        self.tabs.addTab(self.results_tab, "4. Results")
        self.tabs.addTab(self.analysis_tab, "5. Analysis")

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

    def bench_base(self):
        """Code defaults + lab defaults + THIS machine — the layer a loaded
        experiment JSON is supposed to sit on top of, per the precedence at the top
        of __init__. Rebuilt fresh rather than reusing self.settings, so loading a
        file is a clean state and not the current edits with the file smeared over.
        """
        base = DEFAULT_SETTINGS.copy()
        apply_bench_defaults(base, self.bench_values)
        return base

    def label_settings(self):
        """The settings that DESCRIBE what is in the Results/Analysis tabs.

        A loaded run's own metadata when there is one, else the live settings (which
        are correct for a run this session just performed). An empty dict when a
        loaded run had no metadata: segment_potential then returns None and the
        labels say nothing rather than naming a potential that was never applied.
        """
        if self.loaded_run_settings is not None:
            return self.loaded_run_settings
        return self.settings

    def segment_potential(self, seg):
        """What a segment was held at: MEASURED from its echem file when there is
        one, else the nominal ladder, else None.

        Measured wins because it is the only source that cannot disagree with the
        experiment. The metadata records what was REQUESTED, and the live Parameters
        tab may describe a different run entirely -- which is how a segment held at
        +0.700 V came to be titled "+0.400 V".
        """
        if seg is None or seg.data_type == DATA_TYPE_CV:
            return None
        key = (str(self.run_folder), seg.data_type, seg.run_number)
        if key not in self._potential_cache:
            v = None
            if self.run_folder is not None:
                v = measured_potential(
                    echem_txt_path(self.run_folder, seg.data_type, seg.run_number))
            if v is None:
                v = segment_potential(self.label_settings(), seg.data_type,
                                      seg.run_number)
            self._potential_cache[key] = v
        return self._potential_cache[key]

    def segment_potential_text(self, seg):
        """'+0.700 V' for a graph title, or '' when nothing can vouch for a value."""
        v = self.segment_potential(seg)
        return "" if v is None else f"{v:+.3f} V"

    def apply_settings(self, settings):
        """Push a settings dict into every input tab's widgets."""
        self.settings = settings
        self.instrument_tab.populate_from(settings)
        self.parameters_tab.populate_from(settings)
