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
from spec_echem.data import (echem_txt_path, DATA_TYPE_CV, DATA_TYPE_DOPING,
                             DATA_TYPE_DEDOPING, segment_potential,
                             segment_potential_text as nominal_potential_text)
from spec_echem.gamry_data import measured_potential, measured_sweep_range
from spec_echem.settings import DEFAULT_SETTINGS
from gui.tabs.instrument_tab import InstrumentTab
from gui.tabs.parameters_tab import ParametersTab
from gui.tabs.run_tab import RunTab
from gui.tabs.results_tab import ResultsTab
from gui.tabs.analysis_tab import AnalysisTab
from gui.tabs.band_tab import BandTab


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
        # Which Gamry Connect chose. Connection state, not an experiment setting: it
        # is written into the settings at every collect, so loading a settings file
        # or resetting to bench defaults cannot quietly put a run back on the
        # toolkit's default instrument -- which is how a CV meant for the Interface
        # 1010E ran on the Reference 600 (2026-10-09).
        self.gamry_section = ""
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
        # How and when the reference was taken, and the sample name at the time, so
        # Start can remind the user to retake it for a new sample (2026-10-07).
        self.ref_info = None
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
        self.band_tab = BandTab(self)

        self.tabs.addTab(self.instrument_tab, "1. Instrument")
        self.tabs.addTab(self.parameters_tab, "2. Parameters")
        self.tabs.addTab(self.run_tab, "3. Run")
        self.tabs.addTab(self.results_tab, "4. Results")
        self.tabs.addTab(self.analysis_tab, "5. Analysis")
        self.tabs.addTab(self.band_tab, "6. Band Fits")

        self.setCentralWidget(self.tabs)

        # Populate parameter widgets from the default settings
        self._populate_tabs(self.settings)

    # --- settings coordination across the input tabs ---

    def collect_settings(self):
        """Read every input tab's widgets into the canonical settings dict.

        Not while the tabs are being FILLED: a widget's change signal calls this
        part-way through, and reading the boxes not yet filled wrote their empty
        values (the box minimums) over the settings being loaded. Linearity start
        came up as 1e-05 ms on every launch and every Load Settings (2026-10-09).
        """
        if getattr(self, "_populating", False):
            return self.settings
        self.instrument_tab.collect_into(self.settings)
        self.parameters_tab.collect_into(self.settings)
        self.settings["gamry_section"] = self.gamry_section
        return self.settings

    def _populate_tabs(self, settings, instrument_first=False):
        # The two callers fill the tabs in different orders, kept as they were.
        tabs = [self.parameters_tab, self.instrument_tab]
        if instrument_first:
            tabs.reverse()
        self._populating = True
        try:
            for tab in tabs:
                tab.populate_from(settings)
        finally:
            self._populating = False

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

    def doped_to(self, seg):
        """The potential the film was doped to before this segment, or None.

        For a doping step that is its own potential. For the dedoping step that
        follows it, it is that doping step's -- matched by run number. Every
        dedoping step is held at the same potential, so this is the only thing that
        tells them apart. Pre-dedoping and CV return None.
        """
        if seg is None:
            return None
        if seg.data_type == DATA_TYPE_DOPING:
            return self.segment_potential(seg)
        if seg.data_type == DATA_TYPE_DEDOPING:
            for other in self.segments_by_label.values():
                if (other.data_type == DATA_TYPE_DOPING
                        and other.run_number == seg.run_number):
                    return self.segment_potential(other)
        return None

    def _cv_range_text(self, seg, decimals=3):
        """'-0.499 to +0.699 V' -- the range the CV actually swept, measured from
        its file, else the nominal range for this run. Cached like the step
        potentials: the file is read once, not on every redraw.

        The SPAN is cached, not the formatted string, so the dropdowns can ask for
        2 decimals and the titles for 3 off the same single read.
        """
        key = (str(self.run_folder), seg.data_type, seg.run_number)
        if key not in self._potential_cache:
            span = None
            if self.run_folder is not None:
                span = measured_sweep_range(
                    echem_txt_path(self.run_folder, seg.data_type, seg.run_number))
            self._potential_cache[key] = span
        span = self._potential_cache[key]
        if span is None:
            return nominal_potential_text(self.label_settings(), seg.data_type,
                                          seg.run_number, decimals=decimals)
        return f"{span[0]:+.{decimals}f} to {span[1]:+.{decimals}f} V"

    def segment_potential_text(self, seg, decimals=3):
        """'+0.700 V' for a graph title, or '' when nothing can vouch for a value.

        Dedoping reads '-0.500 V after +0.600 V'. Requested: the dedoping steps
        were indistinguishable, all showing the one potential they share.

        `decimals` is 2 only in the segment dropdowns -- see the note on
        data.segment_potential_text. The value itself is never rounded here; only
        its rendering changes, so titles, tables and exports still carry 3.
        """
        if seg is not None and seg.data_type == DATA_TYPE_CV:
            return self._cv_range_text(seg, decimals=decimals)
        v = self.segment_potential(seg)
        if v is None:
            return ""
        text = f"{v:+.{decimals}f} V"
        if seg.data_type == DATA_TYPE_DEDOPING:
            before = self.doped_to(seg)
            if before is not None:
                text += f" after {before:+.{decimals}f} V"
        return text

    def apply_settings(self, settings):
        """Push a settings dict into every input tab's widgets."""
        self.settings = settings
        self._populate_tabs(settings, instrument_first=True)
