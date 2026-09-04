"""
Fake hardware for development and testing without instruments.

FakeSpectrometer mirrors the AvantesSpectrometer interface but returns
synthetic data, so the GUI and acquisition code can run on any machine
(e.g. the Mac dev box) without the avaspec SDK or real hardware.
"""
import time
import numpy as np

# Match the real spectrometer's usable pixel window: 1265 points, ~380-1100 nm
N_POINTS = 1265
WL_MIN = 380.0
WL_MAX = 1100.0

# Detector response model (for the linearity check). Counts scale with integration
# time and compress near the ADC ceiling. NOMINAL_INTEGRATION_MS is the default
# integration time: at exactly that value the scale factor is 1.0 and the knee is
# not engaged, so spectra are identical to what this fake produced before the
# response model existed (golden tests stay valid).
FULL_SCALE = 65535.0
SATURATION_KNEE = 0.90 * FULL_SCALE   # soft roll-off begins here
NOMINAL_INTEGRATION_MS = 0.022


class FakeSpectrometer:
    """Drop-in stand-in for AvantesSpectrometer with synthetic spectra."""

    def __init__(self):
        self.dev_handle = "FAKE"
        self.pixels = None
        self.wavelength = None
        self.serial_number = None
        self.measconfig = None
        self._wl = np.linspace(WL_MIN, WL_MAX, N_POINTS)
        self._integration_time = 0.022
        self._scan_averages = 200
        self._trigger_mode = 0
        self._call_count = 0
        self._rng = np.random.default_rng(seed=12345)
        # Synthetic tungsten-lamp intensity: smooth hump peaking in the NIR
        self._lamp = 5000.0 + 45000.0 * np.exp(-((self._wl - 750.0) ** 2) / (2 * 220.0 ** 2))
        # Configurable wavelength window (indices into _wl); full by default.
        self._start_idx = 0
        self._stop_idx = N_POINTS - 1

    def init(self):
        """Mimic AVS init; returns (measconfig, serial_number)."""
        self.pixels = N_POINTS
        self.wavelength = self._wl
        self.serial_number = "FAKE-0001"
        self.measconfig = None
        return self.measconfig, self.serial_number

    def wavelengths(self):
        """Return (full_array, windowed_array) — windowed to the configured range."""
        return self._wl, self._window(self._wl)

    def _window(self, arr):
        """Slice an array to the configured wavelength window."""
        return np.array(arr[self._start_idx:self._stop_idx + 1])

    def set_wavelength_window(self, wl_min, wl_max, measconfig=None):
        """Restrict returned spectra to [wl_min, wl_max] nm (None = full edge),
        mirroring AvantesSpectrometer.set_wavelength_window for headless testing."""
        n = N_POINTS
        start = 0 if wl_min is None else int(np.searchsorted(self._wl, wl_min, side='left'))
        stop = n - 1 if wl_max is None else int(np.searchsorted(self._wl, wl_max, side='right')) - 1
        start = max(0, min(start, n - 1))
        stop = max(0, min(stop, n - 1))
        if stop < start:
            raise ValueError(f"Empty wavelength window: {wl_min}-{wl_max} nm")
        self._start_idx, self._stop_idx = start, stop

    def _saturate(self, spectrum):
        """Soft compression near the ADC ceiling, then a hard clip — so a
        linearity ramp sees a realistic knee instead of a straight line."""
        out = np.array(spectrum, float)
        hot = out > SATURATION_KNEE
        headroom = FULL_SCALE - SATURATION_KNEE
        out[hot] = SATURATION_KNEE + headroom * (
            1.0 - np.exp(-(out[hot] - SATURATION_KNEE) / headroom))
        return np.clip(out, 0.0, FULL_SCALE)

    def _synthetic_spectrum(self):
        """
        Lamp intensity attenuated by an absorption band near 620 nm whose depth
        slowly drifts over successive calls — so absorbance-vs-time plots look
        dynamic during a fake doping/dedoping run.

        Counts scale with integration time and compress near full scale. At the
        nominal integration time the scale is 1.0 and the knee is not reached, so
        the output matches the pre-response-model fake exactly.
        """
        drift = 0.5 + 0.4 * np.sin(self._call_count / 15.0)
        band = drift * np.exp(-((self._wl - 620.0) ** 2) / (2 * 40.0 ** 2))
        noise = self._rng.normal(0.0, 6.0, N_POINTS)
        scale = self._integration_time / NOMINAL_INTEGRATION_MS
        spectrum = self._lamp * (1.0 - 0.6 * band) * scale + noise
        self._call_count += 1
        return self._saturate(spectrum)

    def measure(self, abort_event=None, on_armed=None):
        """Single acquisition; returns (timestamp, spectrum), or None if aborted."""
        if abort_event is not None and abort_event.is_set():
            return None
        if on_armed is not None:
            on_armed()
        timestamp = time.perf_counter() * 1e5  # /1e5 -> seconds downstream
        return timestamp, self._window(self._synthetic_spectrum())

    def measure_timing(self, measconfig=None):
        """Returns (timestamp, spectrum, net_dif_ms, t_dif_s) like the real method."""
        t1 = time.perf_counter()
        timestamp = t1 * 1e5
        spectrum = self._window(self._synthetic_spectrum())
        t_dif = time.perf_counter() - t1
        total_int_time = self._integration_time * self._scan_averages
        net_dif = (t_dif * 1000) - total_int_time
        return timestamp, spectrum, net_dif, t_dif

    def per_spectrum_seconds(self, measconfig=None):
        """Integration x averages, in seconds — same contract as the real class."""
        return (self._integration_time * self._scan_averages) / 1000.0

    def integration_and_averages(self, measconfig=None):
        """The two factors behind per_spectrum_seconds — same contract as the real class."""
        return float(self._integration_time), int(self._scan_averages)

    def set_integration_time(self, duration, measconfig=None):
        self._integration_time = duration

    def set_scan_averages(self, scans, measconfig=None):
        self._scan_averages = scans

    def set_trigger_mode(self, mode, measconfig=None):
        self._trigger_mode = mode

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ---------------------------------------------------------------------------
# FakeAutolab — a stand-in for the Metrohm Autolab SDK.
#
# Shaped to match what the SDK actually did on the UW rig on 2026-08-31, recorded
# in examples/autolab_api_report.txt and docs/autolab-run-api.md. The awkward parts
# are deliberate, because they are the parts a driver gets wrong:
#
#   * LoadProcedure(path) RETURNS the Procedure; there is no inst.Procedure.
#   * Measure() returns immediately and IsMeasuring goes False later.
#   * Commands and CommandParameters have NO name property — a command is fetched
#     from the list by IdName, a parameter only by INDEX.
#   * Recorded arrays hang off command.Signals and are only readable after the run.
#   * The cell is switched with an enum member, not a bool.
#
# IMPORTANT: this fake encodes our UNDERSTANDING of the SDK. A test passing against
# it proves the driver is internally consistent, not that the SDK behaves this way.
# Only the bench can settle that.
# ---------------------------------------------------------------------------

# Command IdNames, as reported by the rig (bench_autolab_cv.py / _ca.py phase 0).
CV_COMMAND_ID = "FHCyclicVoltammetry2"
WAIT_COMMAND_ID = "FHWait"
CA_RECORDER_ID = "FHLevel"                 # "Record signals (>1 ms)"
CA_SETPOINT_ID = "FHSetSetpointPotential"  # "Set potential" — holds the hold potential

# CV staircase parameter defaults, in SDK index order (autolab-run-api.md §1):
#   0 start V, 1 upper V, 2 lower V, 3 step V, 4 crossings (int), 5 stop V,
#   6 scan rate V/s
_CV_DEFAULTS = [0.0, 1.0, -1.0, 0.00244, 2, 0.0, 0.1]
_WAIT_DEFAULTS = [5.0]
# FHLevel: 0 interval s, 1 duration s, 2 bool. FHSetSetpointPotential: 0 potential V.
_LEVEL_DEFAULTS = [0.01, 5.0, False]
_SETPOINT_DEFAULTS = [0.0]


class _FakeParameter:
    """A CommandParameter: a value, addressed by index. No name — that is the point."""

    def __init__(self, value):
        self.ValueAsObject = value


class _FakeList:
    """Stands in for CommandParameterList / CommandParameterSignalList / the command
    list: indexable, iterable, and carrying Names/IdNames on the LIST rather than on
    the items."""

    def __init__(self, items, names=None, idnames=None):
        self._items = list(items)
        self.Names = list(names) if names else []
        self.IdNames = list(idnames) if idnames else []

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._items[key]
        for i, idn in enumerate(self.IdNames):      # by IdName
            if idn == key:
                return self._items[i]
        for i, nm in enumerate(self.Names):         # then by display name
            if nm == key:
                return self._items[i]
        raise KeyError(key)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class _FakeSignal:
    def __init__(self, values):
        self.ValueAsObject = list(values)


class _FakeCommand:
    def __init__(self, values):
        self.CommandParameters = _FakeList([_FakeParameter(v) for v in values])
        self.Signals = _FakeList([], idnames=[])

    def _publish(self, channels):
        """Fill .Signals the way a completed run does."""
        self.Signals = _FakeList([_FakeSignal(v) for v in channels.values()],
                                 idnames=list(channels))


class FakeProcedure:
    """One loaded .nox. Measure() is non-blocking; IsMeasuring clears after
    `duration` seconds of wall clock, or immediately when duration is 0."""

    def __init__(self, path, duration=0.0, points=64, wait_s=5.0, fail=False,
                 ca_levels=1, current_scale=1.0, dio_step=False):
        self.path = path
        self._duration = duration
        self._points = points
        self._fail = fail
        self._current_scale = current_scale   # <1 models an open cell (near-zero I)
        self._started = None
        self._aborted = False
        cv = _FakeCommand(list(_CV_DEFAULTS))
        wait = _FakeCommand(list(_WAIT_DEFAULTS))
        wait.CommandParameters[0].ValueAsObject = wait_s
        # The CA template is 3 (setpoint -> FHLevel -> plot) blocks on the rig;
        # ca_levels lets a test model that so _neutralise_extra_ca_steps has extras
        # to zero. Interleaved setpoint/level so IdName POSITIONS look like the rig.
        items = [cv, wait]
        names = ["CV staircase", "Wait time (s)"]
        idnames = [CV_COMMAND_ID, WAIT_COMMAND_ID]
        self._levels = []
        for _ in range(max(1, ca_levels)):
            items.append(_FakeCommand(list(_SETPOINT_DEFAULTS)))
            names.append("Set potential")
            idnames.append(CA_SETPOINT_ID)
            lvl = _FakeCommand(list(_LEVEL_DEFAULTS))
            items.append(lvl)
            names.append("Record signals (>1 ms)")
            idnames.append(CA_RECORDER_ID)
            self._levels.append(lvl)
        if dio_step:
            # A hand-added digital-output step, as NOVA's own spectro-EC procedures
            # carry (rendered there as Dio_0 / HDio, writing P1.A). Its presence is
            # what lets the driver leave the trigger to the procedure.
            items.append(_FakeCommand([0.0]))
            names.append("P1.A:Write")
            idnames.append("HDio")
        self.Commands = _FakeList(items, names=names, idnames=idnames)

    # --- the SDK surface ---
    def Measure(self):
        if self._fail:
            raise RuntimeError("fake: Measure() refused")
        self._started = time.time()
        self._aborted = False

    @property
    def IsMeasuring(self):
        if self._started is None or self._aborted:
            return False
        if time.time() - self._started >= self._duration:
            self._finish()
            return False
        return True

    def Abort(self):
        self._aborted = True
        self._finish(partial=True)

    # --- what a finished run leaves behind ---
    def _finish(self, partial=False):
        n = max(1, self._points // 2) if partial else self._points
        wait = float(self.Commands[WAIT_COMMAND_ID].CommandParameters[0].ValueAsObject)
        # CalcTime is wall-clock from procedure start and begins at ~the wait value,
        # which is exactly the offset the driver has to remove.
        channels = {
            "CalcTime": [wait + i * 0.024414 for i in range(n)],
            "EI_0.CalcPotential": [0.001 * i for i in range(n)],
            "EI_0.CalcCurrent": [1e-7 * i * self._current_scale for i in range(n)],
            "SetpointApplied": [0.001 * i for i in range(n)],
            "ScanNumber": [1] * n,
            "Index": list(range(1, n + 1)),
        }
        # The driver reads the CV staircase for a CV segment and the first FHLevel
        # for a chrono hold — publish on both so either path finds its trace.
        self.Commands[CV_COMMAND_ID]._publish(channels)
        if self._levels:
            self._levels[0]._publish(dict(channels))


class _FakeEi:
    def __init__(self):
        self.Cell = False
        self.CellOnOff = None
        self.PotentialOverload = False
        self.CurrentOverload = False
        self.Setpoint = 0.0
        self.Potential = 0.0
        self.Current = 0.0


class _FakePort:
    """Records every write, so a test can assert an actual rising edge happened
    rather than just that some method was called."""

    def __init__(self):
        self._value = 0
        self.history = []
        self.PortDirection = None
        self.PortName = "P1.A"
        self.released = False

    @property
    def Value(self):
        return self._value

    @Value.setter
    def Value(self, v):
        self._value = v
        self.history.append(v)

    @property
    def rising_edges(self):
        return sum(1 for a, b in zip(self.history, self.history[1:])
                   if a == 0 and b != 0)

    def Release(self):
        self.released = True


class _FakeConnection:
    def __init__(self):
        self.IsConnected = True
        self.EmbeddedExeFileToStart = None


class FakeAutolab:
    """Stand-in for EcoChemie.Autolab.Sdk.Instrument."""

    def __init__(self, duration=0.0, points=64, wait_s=5.0, fail_measure=False,
                 ca_levels=1, current_scale=1.0, dio_step=False):
        self.AutolabConnection = _FakeConnection()
        self.Ei = _FakeEi()
        self.port = _FakePort()
        self._duration = duration
        self._points = points
        self._wait_s = wait_s
        self._fail_measure = fail_measure
        self._dio_step = dio_step
        self._ca_levels = ca_levels        # 3 models the stock Chrono amperometry.nox
        self._current_scale = current_scale  # <1 models an open cell
        self.loaded = []              # every .nox path handed to LoadProcedure
        self.disconnected = False

    def LoadProcedure(self, path):
        self.loaded.append(path)
        return FakeProcedure(path, dio_step=self._dio_step,
                             duration=self._duration, points=self._points,
                             wait_s=self._wait_s, fail=self._fail_measure,
                             ca_levels=self._ca_levels,
                             current_scale=self._current_scale)

    def Disconnect(self):
        self.AutolabConnection.IsConnected = False
        self.disconnected = True

    # --- helpers the tests drive the fake with ---
    def lose_connection(self):
        self.AutolabConnection.IsConnected = False
