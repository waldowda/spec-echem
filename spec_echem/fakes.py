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
