"""
Avantes Spectrometer Class for Spectroelectrochemistry

Author: Dean Waldow
Updated: 07-02-2025
"""

import os
import platform
import sys
import time
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import math
from pathlib import Path
import pickle
import warnings
import json
from datetime import datetime
try:
    from avaspec import *
    AVASPEC_AVAILABLE = True
except ImportError:
    AVASPEC_AVAILABLE = False


# Calibrated usable pixel window (~380-1100 nm, 1265 pts) — the fixed slice this
# code has always applied. It is now the DEFAULT of a configurable window; a
# narrower user window (set_wavelength_window) restricts within it.
CAL_START_PX = 395
CAL_STOP_PX = 1659   # inclusive; the old slice was [395:1660]
CAL_WINDOW_LEN = CAL_STOP_PX - CAL_START_PX + 1   # 1265 calibrated pixels


class AvantesSpectrometer:
    """
    Avantes spectrometer control class for spectroelectrochemistry experiments.
    
    This class provides an interface to control Avantes spectrometers,
    optimized for spectroelectrochemistry measurements with wavelength
    range approximately 380 to 1100 nm.
    """

    # User wavelength crop, as indices into the calibrated [395:1660] window
    # (a pure software crop). Full window by default → output identical to before.
    # Class-level so it's defined even if an instance is built without __init__.
    _lo_i = 0
    _hi_i = CAL_WINDOW_LEN - 1

    def __init__(self):
        """Initialize the Avantes spectrometer instance."""
        self.dev_handle = None
        self.pixels = None
        self.wavelength = None
        self.serial_number = None
        self.measconfig = None
        # Wavelength crop (indices into the calibrated window); full by default.
        self._lo_i = 0
        self._hi_i = CAL_WINDOW_LEN - 1

    def init(self):
        """
        Initialize and configure the Avantes spectrometer.
        
        Returns:
            tuple: (measconfig, serial_number) - Measurement configuration and device serial number
        """
        # Initialize AVS library
        ret = AVS_Init(0)    
        print(f"AVS_Init returned: {ret}")
        
        # Get number of devices
        ret = AVS_GetNrOfDevices()
        print(f"AVS_GetNrOfDevices returned: {ret}")
        if ret < 1:
            raise RuntimeError("Invalid index (forget to plug in the spectrometer?)")

        # Get device list and activate first device
        mylist = AVS_GetList(1)
        self.serial_number = str(mylist[0].SerialNumber.decode("utf-8"))
        print(f"Found Serial number: {self.serial_number}")
        
        # Activate device
        self.dev_handle = AVS_Activate(mylist[0])
        print(f"AVS_Activate returned: {self.dev_handle}")

        # Get device configuration
        devcon = AVS_GetParameter(self.dev_handle, 63484)
        self.pixels = devcon.m_Detector_m_NrPixels

        # Get wavelength calibration
        self.wavelength = AVS_GetLambda(self.dev_handle)
        
        # Enable high resolution ADC
        ret = AVS_UseHighResAdc(self.dev_handle, True)
        
        # Configure measurement settings
        self.measconfig = self._create_measurement_config()
        ret = AVS_PrepareMeasure(self.dev_handle, self.measconfig)
        
        return self.measconfig, self.serial_number
    
    def _create_measurement_config(self):
        """
        Create default measurement configuration.
        
        Returns:
            MeasConfigType: Configured measurement settings
        """
        measconfig = MeasConfigType()
        measconfig.m_StartPixel = 0
        measconfig.m_StopPixel = self.pixels - 1
        measconfig.m_IntegrationTime = 0.022  # Initial integration time (ms)
        measconfig.m_IntegrationDelay = 0
        measconfig.m_NrAverages = 200  # Default number of averages
        measconfig.m_CorDynDark_m_Enable = 0
        measconfig.m_CorDynDark_m_ForgetPercentage = 0
        measconfig.m_Smoothing_m_SmoothPix = 0
        measconfig.m_Smoothing_m_SmoothModel = 0
        measconfig.m_SaturationDetection = 0
        measconfig.m_Trigger_m_Mode = 0
        measconfig.m_Trigger_m_Source = 0
        measconfig.m_Trigger_m_SourceType = 0
        measconfig.m_Control_m_StrobeControl = 0
        measconfig.m_Control_m_LaserDelay = 0
        measconfig.m_Control_m_LaserWidth = 0
        measconfig.m_Control_m_LaserWaveLength = 0.0
        measconfig.m_Control_m_StoreToRam = 0
        
        return measconfig
    
    def wavelengths(self):
        """
        Get wavelength calibration data.
        
        Returns:
            tuple: (full_wavelength_array, trimmed_numpy_array)
                - full_wavelength_array: Complete wavelength calibration
                - trimmed_numpy_array: Numpy array for ~380-1100 nm range
        """
        wavelength = AVS_GetLambda(self.dev_handle)
        # Calibrated [395:1660] window (the proven slice), then the user crop.
        return wavelength, self._crop(wavelength)

    def _crop(self, spectral_data):
        """Take the calibrated ~380-1100 nm window ([395:1660], the historical
        slice) from a raw AVS array, then apply the user wavelength crop. Pure
        software — no dependence on how the SDK windows pixels, so wavelengths()
        and measure() always agree. Full crop by default = the historical output.
        """
        cal = np.array(spectral_data[CAL_START_PX:CAL_STOP_PX + 1])
        return cal[self._lo_i:self._hi_i + 1]

    def set_wavelength_window(self, wl_min, wl_max):
        """Restrict the returned spectrum to [wl_min, wl_max] nm.

        A pure software crop of the calibrated ~380-1100 nm window — it does NOT
        touch the hardware measconfig, so it is independent of how the SDK returns
        pixels. Stores indices into the calibrated window; pass wl_min=None and/or
        wl_max=None to reset that edge to the full window.
        """
        cal_wl = np.asarray(self.wavelength, float)[CAL_START_PX:CAL_STOP_PX + 1]
        n = len(cal_wl)
        lo = 0 if wl_min is None else int(np.searchsorted(cal_wl, wl_min, side='left'))
        hi = n - 1 if wl_max is None else int(np.searchsorted(cal_wl, wl_max, side='right')) - 1
        lo = max(0, min(lo, n - 1))
        hi = max(0, min(hi, n - 1))
        if hi < lo:
            raise ValueError(f"Empty wavelength window: {wl_min}-{wl_max} nm")
        self._lo_i, self._hi_i = lo, hi
        print(f"Wavelength window: {cal_wl[lo]:.1f}-{cal_wl[hi]:.1f} nm ({hi - lo + 1} px)")

    def measure_timing(self, measconfig=None):
        """
        Perform a timed measurement to assess acquisition timing.
        
        Args:
            measconfig: Measurement configuration (uses self.measconfig if None)
            
        Returns:
            tuple: (timestamp, spectral_data, net_difference_ms, total_time_s)
        """
        if measconfig is None:
            measconfig = self.measconfig
            
        nummeas = 1
        scans = 0
        stopscanning = False
        
        while not stopscanning:
            t1 = time.time()
            
            # Start measurement
            ret = AVS_Measure(self.dev_handle, 0, 1)
            
            # Poll for data ready
            dataready = False
            while not dataready:
                dataready = AVS_PollScan(self.dev_handle)
                time.sleep(0.001)
            
            if dataready:
                scans += 1
                
            if scans >= nummeas:
                stopscanning = True
                
            # Get spectral data
            ret = AVS_GetScopeData(self.dev_handle)
            t2 = time.time()
            t_dif = t2 - t1
            
            timestamp = ret[0]
            spectral_data = ret[1]

            # Calculate timing difference
            total_int_time = measconfig.m_IntegrationTime * measconfig.m_NrAverages
            net_dif = (t_dif * 1000) - total_int_time

        return timestamp, self._crop(spectral_data), net_dif, t_dif
    
    def measure(self, abort_event=None, on_armed=None):
        """
        Perform a single measurement and return spectral data.

        Args:
            abort_event: optional threading.Event — if set while waiting for the
                trigger / data, the measurement is abandoned and None is returned.
            on_armed: optional callable invoked right after AVS_Measure() has
                armed the device and before polling. Python-mode co-acquisition
                raises DIGOUT0 / starts the Gamry here, so the trigger edge lands
                while the device is armed and waiting.

        Returns:
            tuple (timestamp, spectral_data_array), or None if aborted.
        """
        # Start measurement (in trigger mode this arms the device to wait for the edge)
        ret = AVS_Measure(self.dev_handle, 0, 1)
        if ret < 0:
            # Arm failed: do NOT fire the trigger — otherwise the Gamry would run
            # while the spectrometer captures nothing (a silent time-zero desync).
            raise RuntimeError(
                f"AVS_Measure failed (code {ret}); spectrometer not armed — trigger not fired.")

        # Device is now armed and waiting; fire the trigger here if asked.
        if on_armed is not None:
            on_armed()

        # Wait for data ready
        dataready = False
        while not dataready:
            if abort_event is not None and abort_event.is_set():
                return None
            dataready = AVS_PollScan(self.dev_handle)
            time.sleep(0.001)

        # Get spectral data
        ret = AVS_GetScopeData(self.dev_handle)
        timestamp = ret[0]
        spectral_data = ret[1]

        return timestamp, self._crop(spectral_data)
    
    def plot_data(self, wavelength, spectral_data):
        """
        Plot spectral data.
        
        Args:
            wavelength: Wavelength array
            spectral_data: Intensity array
        """
        plt.plot(wavelength, spectral_data)
        plt.xlabel('Wavelength (nm)')
        plt.ylabel('Intensity')
        plt.title('Avantes Spectrum')
        plt.grid(True, alpha=0.3)
        plt.show()
    
    def set_integration_time(self, duration, measconfig=None):
        """
        Set integration time.
        
        Args:
            duration: Integration time in milliseconds
            measconfig: Measurement configuration (uses self.measconfig if None)
        """
        if measconfig is None:
            measconfig = self.measconfig
            
        measconfig.m_IntegrationTime = duration
        ret = AVS_PrepareMeasure(self.dev_handle, measconfig)
        print(f"Integration time set to {duration} ms")
    
    def set_trigger_mode(self, mode, measconfig=None):
        """
        Set trigger mode.
        
        Args:
            mode: 0 for no trigger, 1 for edge trigger
            measconfig: Measurement configuration (uses self.measconfig if None)
        """
        if measconfig is None:
            measconfig = self.measconfig
            
        measconfig.m_Trigger_m_Mode = mode
        ret = AVS_PrepareMeasure(self.dev_handle, measconfig)
        mode_str = "No trigger" if mode == 0 else "Edge trigger"
        print(f"Trigger mode set to: {mode_str}")
    
    def set_source_type(self, mode, measconfig=None):
        """
        Set trigger source type.
        
        Args:
            mode: 0 for edge trigger, 1 for level trigger
            measconfig: Measurement configuration (uses self.measconfig if None)
        """
        if measconfig is None:
            measconfig = self.measconfig
            
        measconfig.m_Trigger_m_SourceType = mode
        ret = AVS_PrepareMeasure(self.dev_handle, measconfig)
        mode_str = "Edge trigger" if mode == 0 else "Level trigger"
        print(f"Source type set to: {mode_str}")
    
    def set_scan_averages(self, scans, measconfig=None):
        """
        Set number of scans to average.
        
        Args:
            scans: Number of scans to average
            measconfig: Measurement configuration (uses self.measconfig if None)
        """
        if measconfig is None:
            measconfig = self.measconfig
            
        measconfig.m_NrAverages = scans
        ret = AVS_PrepareMeasure(self.dev_handle, measconfig)
        print(f"Number of averages set to {scans}")
    
    def close(self):
        """Close the connection to the spectrometer."""
        if self.dev_handle:
            # Add any cleanup code here
            print("Spectrometer connection closed")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()