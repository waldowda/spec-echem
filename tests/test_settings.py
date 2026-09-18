"""
Unit tests for spec_echem.settings — load/save round-trip and defaults.
"""
import json
import pytest
from spec_echem.settings import (load_settings, save_settings, DEFAULT_SETTINGS,
                                 clamp_to_detector_floor, tidy_detector_floor)


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
        "potentiostat_mode", "save_dta",
        "cv_enabled", "cv_cycles",
        "cv_initial_v", "cv_limit1_v", "cv_limit2_v", "cv_final_v",
        "cv_step_size", "cv_scan_rate",
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


def test_a_loaded_file_does_not_revert_bench_values_it_never_mentions(tmp_path):
    """The 20260909_test5 bug. A settings file saved before a bench key existed must
    not drag that key back to the code default — the rig's own value has to survive,
    because nothing in the run says it was dropped."""
    import json
    from spec_echem.bench import apply_bench_defaults

    path = tmp_path / "old_settings.json"
    path.write_text(json.dumps({"sample_name": "from the file"}), encoding="utf-8")

    base = DEFAULT_SETTINGS.copy()
    apply_bench_defaults(base, {"autolab_wait_s": 0.0, "autolab_dio_mask": 1})

    loaded = load_settings(path, base=base)
    assert loaded["sample_name"] == "from the file"    # the file still wins...
    assert loaded["autolab_wait_s"] == 0.0             # ...and the rig survives
    assert loaded["autolab_dio_mask"] == 1


def test_without_a_base_the_code_defaults_still_fill_in(tmp_path):
    """The no-rig caller (tests, reading a file off another machine) is unchanged."""
    import json
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"sample_name": "x"}), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded["sample_name"] == "x"
    assert loaded["autolab_wait_s"] == DEFAULT_SETTINGS["autolab_wait_s"]


# --- The detector's floor: clamp, never overwrite ---------------------------------

# MEASURED, both on hardware: a SensorType 10 part and a SensorType 22 part.
FLOOR_SLOW = 1.04803466796875
FLOOR_FAST = 0.009033203125


def test_a_ramp_below_the_floor_is_raised_to_it():
    """The whole ramp is unusable on the slow detector -- the SDK REJECTS every point
    (code -11) rather than clamping, so the linearity check cannot run at all."""
    settings = {"integration_time_ms": 0.022, "lin_start_ms": 0.022, "lin_stop_ms": 0.15}
    changes = clamp_to_detector_floor(settings, FLOOR_SLOW)

    assert settings["integration_time_ms"] == FLOOR_SLOW
    assert settings["lin_start_ms"] == FLOOR_SLOW
    assert settings["lin_stop_ms"] > settings["lin_start_ms"]
    assert {key for key, _, _ in changes} == {
        "integration_time_ms", "lin_start_ms", "lin_stop_ms"}


def test_a_working_value_above_the_floor_is_left_alone():
    """The killer case. 2.6439 ms was chosen from a linearity check to land ~85% fill;
    pulling it down to the hardware minimum would silently destroy that working point.
    The floor is a hardware constraint, the value above it is a scientific choice."""
    settings = {"integration_time_ms": 2.6439, "lin_start_ms": 1.1, "lin_stop_ms": 5.0}
    changes = clamp_to_detector_floor(settings, FLOOR_SLOW)

    assert changes == []
    assert settings == {"integration_time_ms": 2.6439, "lin_start_ms": 1.1,
                        "lin_stop_ms": 5.0}


def test_the_fast_detector_needs_no_clamping():
    settings = {"integration_time_ms": 0.022, "lin_start_ms": 0.022, "lin_stop_ms": 0.15}
    assert clamp_to_detector_floor(settings, FLOOR_FAST) == []


def test_an_unknown_floor_changes_nothing():
    """No device to ask, or the probe failed. Guessing would be worse than leaving it."""
    settings = {"integration_time_ms": 0.022, "lin_start_ms": 0.022, "lin_stop_ms": 0.15}
    assert clamp_to_detector_floor(settings, None) == []
    assert settings["integration_time_ms"] == 0.022


def test_the_stop_stays_above_the_start():
    settings = {"integration_time_ms": 2.0, "lin_start_ms": 0.02, "lin_stop_ms": 0.15}
    clamp_to_detector_floor(settings, FLOOR_SLOW)
    assert settings["lin_stop_ms"] > settings["lin_start_ms"]


def test_the_floor_is_rounded_up_never_down():
    """Rounding UP is what makes tidying safe: the result is still an exposure the
    detector accepts. Rounding down would hand back a value it rejects."""
    assert tidy_detector_floor(FLOOR_SLOW) == 1.05
    assert tidy_detector_floor(FLOOR_FAST) == 0.00904
    for raw in (FLOOR_SLOW, FLOOR_FAST, 0.022, 7.31):
        assert tidy_detector_floor(raw) >= raw


def test_tidying_drops_precision_the_measurement_never_had():
    """The floor is bisected to 1e-4 ms, so 1.04803466796875 quotes fifteen digits of
    a number known to four."""
    assert tidy_detector_floor(FLOOR_SLOW) == 1.05
    assert tidy_detector_floor(None) is None
    assert tidy_detector_floor(0) is None
