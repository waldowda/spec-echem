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

    def init(self):
        """Mimic AVS init; returns (measconfig, serial_number)."""
        self.pixels = N_POINTS
        self.wavelength = self._wl
        self.serial_number = "FAKE-0001"
        self.measconfig = None
        return self.measconfig, self.serial_number

    def wavelengths(self):
        """Return (full_array, trimmed_array) — both the 1265-pt window here."""
        return self._wl, np.array(self._wl)

    def _synthetic_spectrum(self):
        """
        Lamp intensity attenuated by an absorption band near 620 nm whose depth
        slowly drifts over successive calls — so absorbance-vs-time plots look
        dynamic during a fake doping/dedoping run.
        """
        drift = 0.5 + 0.4 * np.sin(self._call_count / 15.0)
        band = drift * np.exp(-((self._wl - 620.0) ** 2) / (2 * 40.0 ** 2))
        noise = self._rng.normal(0.0, 6.0, N_POINTS)
        spectrum = self._lamp * (1.0 - 0.6 * band) + noise
        self._call_count += 1
        return np.clip(spectrum, 0.0, None)

    def measure(self, abort_event=None, on_armed=None):
        """Single acquisition; returns (timestamp, spectrum), or None if aborted."""
        if abort_event is not None and abort_event.is_set():
            return None
        if on_armed is not None:
            on_armed()
        timestamp = time.perf_counter() * 1e5  # /1e5 -> seconds downstream
        return timestamp, self._synthetic_spectrum()

    def measure_timing(self, measconfig=None):
        """Returns (timestamp, spectrum, net_dif_ms, t_dif_s) like the real method."""
        t1 = time.perf_counter()
        timestamp = t1 * 1e5
        spectrum = self._synthetic_spectrum()
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
