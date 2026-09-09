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
