"""
Unit tests for spec_echem.settings — load/save round-trip and defaults.
"""
import json
import pytest
from spec_echem.settings import load_settings, save_settings, DEFAULT_SETTINGS


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(DEFAULT_SETTINGS, path)
    loaded = load_settings(path)
    assert loaded == DEFAULT_SETTINGS


def test_missing_keys_filled_from_defaults(tmp_path):
    path = tmp_path / "partial.json"
    partial = {"scan_averages": 100, "sample_name": "P3HT"}
    path.write_text(json.dumps(partial))
    loaded = load_settings(path)
    # Explicitly set values are preserved
    assert loaded["scan_averages"] == 100
    assert loaded["sample_name"] == "P3HT"
    # Missing keys come from defaults
    assert loaded["integration_time_ms"] == DEFAULT_SETTINGS["integration_time_ms"]
    assert loaded["cv_scan_rate"] == DEFAULT_SETTINGS["cv_scan_rate"]


def test_extra_keys_in_file_are_preserved(tmp_path):
    path = tmp_path / "extra.json"
    data = dict(DEFAULT_SETTINGS)
    data["future_key"] = "placeholder"
    path.write_text(json.dumps(data))
    loaded = load_settings(path)
    assert loaded["future_key"] == "placeholder"


def test_all_default_keys_present():
    required = [
        "integration_time_ms", "scan_averages",
        "data_root", "data_folder",
        "sample_name", "electrolyte", "notes",
        "trigger",
        "cv_enabled", "cv_cycles", "cv_total_voltage", "cv_step_size", "cv_scan_rate",
        "prededoping_enabled", "prededoping_potential", "prededoping_time",
        "doping_enabled", "doping_potential_start", "doping_potential_end",
        "doping_potential_step", "dedoping_potential",
        "chrono_time", "chrono_delta_time",
    ]
    for key in required:
        assert key in DEFAULT_SETTINGS, f"Missing key in DEFAULT_SETTINGS: {key}"


def test_json_file_is_human_readable(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(DEFAULT_SETTINGS, path)
    text = path.read_text()
    # Should be indented JSON, not a one-liner
    assert "\n" in text
    assert "  " in text
