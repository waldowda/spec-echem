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
from avaspec import *
from . import globals


class AvantesSpectrometer:
    """
    Avantes spectrometer control class for spectroelectrochemistry experiments.
    
    This class provides an interface to control Avantes spectrometers,
    optimized for spectroelectrochemistry measurements with wavelength
    range approximately 380 to 1100 nm.
    """
    
    def __init__(self):
        """Initialize the Avantes spectrometer instance."""
        self.dev_handle = None
        self.pixels = None
        self.wavelength = None
        self.serial_number = None
        self.measconfig = None
        
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
        
        # Get device list and activate first device
        mylist = AVS_GetList(1)
        self.serial_number = str(mylist[0].SerialNumber.decode("utf-8"))
        print(f"Found Serial number: {self.serial_number}")
        
        # Activate device
        globals.dev_handle = AVS_Activate(mylist[0])
        self.dev_handle = globals.dev_handle
        print(f"AVS_Activate returned: {self.dev_handle}")
        
        # Get device configuration
        devcon = AVS_GetParameter(self.dev_handle, 63484)
        globals.pixels = devcon.m_Detector_m_NrPixels
        self.pixels = globals.pixels
        
        # Get wavelength calibration
        globals.wavelength = AVS_GetLambda(self.dev_handle)
        self.wavelength = globals.wavelength
        
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
        return wavelength, np.array(wavelength[395:1660])
    
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
            
        return timestamp, np.array(spectral_data[395:1660]), net_dif, t_dif
    
    def measure(self):
        """
        Perform a single measurement and return spectral data.
        
        Returns:
            tuple: (timestamp, spectral_data_array)
        """
        # Start measurement
        ret = AVS_Measure(self.dev_handle, 0, 1)
        
        # Wait for data ready
        dataready = False
        while not dataready:
            dataready = AVS_PollScan(self.dev_handle)
            time.sleep(0.001)
        
        # Get spectral data
        ret = AVS_GetScopeData(self.dev_handle)
        timestamp = ret[0]
        spectral_data = ret[1]
        
        return timestamp, np.array(spectral_data[395:1660])
    
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