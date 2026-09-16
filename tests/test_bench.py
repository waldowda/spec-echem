"""Bench defaults: layering, hand-edit tolerance, and the bench/experiment boundary."""
import pytest

from spec_echem.bench import (
    BENCH_KEYS,
    apply_bench_defaults,
    load_bench_defaults,
    read_bench_file,
    read_detector_floor,
    save_bench_defaults,
    save_detector_floor,
)
from spec_echem.settings import DEFAULT_SETTINGS


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_known_keys_with_correct_types(tmp_path):
    ini = _write(tmp_path / "b.ini", """
[spectrometer]
wavelength_min = 400
wavelength_max = 1050
integration_time_ms = 0.088
scan_averages = 200

[bench]
save_dta = false
trigger = yes
data_root = D:\\data
""")
    values, warnings = read_bench_file(ini)

    assert warnings == []
    assert values["wavelength_min"] == 400.0
    assert values["integration_time_ms"] == 0.088
    assert values["scan_averages"] == 200          # int, not str
    assert values["save_dta"] is False             # bool, not the string "false"
    assert values["trigger"] is True
    assert values["data_root"] == "D:\\data"


def test_blank_wavelength_means_full_range():
    """An empty value is how you say 'no crop' — it must become None, not 0.0."""
    from spec_echem.bench import _opt_float
    assert _opt_float("") is None
    assert _opt_float("  ") is None
    assert _opt_float("400") == 400.0


def test_a_hand_edit_typo_warns_and_falls_back_rather_than_crashing(tmp_path):
    """These files are meant to be hand-edited, so a typo is a matter of when, not if.
    A bad value must not take the GUI down on launch."""
    ini = _write(tmp_path / "b.ini", """
[spectrometer]
wavelength_min = 4O0
scan_averages = 200
""")
    values, warnings = read_bench_file(ini)

    assert "wavelength_min" not in values          # the bad one is skipped...
    assert values["scan_averages"] == 200          # ...the good one still lands
    assert any("wavelength_min" in w for w in warnings)


def test_unknown_key_is_reported_not_silently_ignored(tmp_path):
    ini = _write(tmp_path / "b.ini", "[bench]\nsample_name = P3HT\n")
    values, warnings = read_bench_file(ini)

    assert values == {}                            # experiment settings don't belong here
    assert any("sample_name" in w for w in warnings)


def test_missing_file_is_empty_not_an_error(tmp_path):
    values, warnings = read_bench_file(tmp_path / "nope.ini")
    assert values == {} and warnings == []


def test_this_machine_overrides_the_lab_default(tmp_path):
    repo = _write(tmp_path / "defaults.ini",
                  "[spectrometer]\nwavelength_min = 400\nscan_averages = 200\n")
    user = _write(tmp_path / "bench.ini",
                  "[spectrometer]\nwavelength_min = 425\n")

    values, warnings = load_bench_defaults(repo_path=repo, user_path=user)

    assert values["wavelength_min"] == 425.0       # the rig wins
    assert values["scan_averages"] == 200          # lab default still shows through
    assert warnings == []


def test_save_writes_only_bench_keys_never_experiment_ones(tmp_path):
    """Clicking 'Save as defaults' mid-experiment must not turn this run's sample name
    or CV vertices into the rig's defaults."""
    settings = DEFAULT_SETTINGS.copy()
    settings.update({
        "wavelength_min": 400.0, "wavelength_max": 1050.0,
        "integration_time_ms": 0.088,
        "sample_name": "P3HT-secret", "data_folder": "20260714_run", "cv_cycles": 7,
    })
    path = save_bench_defaults(settings, path=tmp_path / "bench.ini")
    text = path.read_text(encoding="utf-8")

    assert "P3HT-secret" not in text
    assert "data_folder" not in text
    assert "cv_cycles" not in text
    assert "wavelength_min = 400.0" in text


def test_round_trip_through_a_file(tmp_path):
    settings = DEFAULT_SETTINGS.copy()
    settings.update({"wavelength_min": 400.0, "wavelength_max": 1050.0,
                     "integration_time_ms": 0.088, "lin_max_fill_pct": 85.0,
                     "lin_steps": 20, "save_dta": False})
    path = save_bench_defaults(settings, path=tmp_path / "bench.ini")

    values, warnings = read_bench_file(path)
    assert warnings == []
    for key in ("wavelength_min", "wavelength_max", "integration_time_ms",
                "lin_max_fill_pct", "lin_steps", "save_dta"):
        assert values[key] == settings[key], key


def test_none_wavelength_round_trips_as_full_range(tmp_path):
    settings = DEFAULT_SETTINGS.copy()
    settings["wavelength_min"] = None
    settings["wavelength_max"] = None
    path = save_bench_defaults(settings, path=tmp_path / "bench.ini")

    values, _ = read_bench_file(path)
    assert values["wavelength_min"] is None
    assert values["wavelength_max"] is None


def test_apply_overlays_only_bench_keys():
    settings = DEFAULT_SETTINGS.copy()
    before = settings["sample_name"]

    apply_bench_defaults(settings, {"integration_time_ms": 0.088, "sample_name": "nope"})

    assert settings["integration_time_ms"] == 0.088
    assert settings["sample_name"] == before       # not a bench key: untouched


def test_every_bench_key_exists_in_default_settings():
    """The bench file can only override settings that actually exist — otherwise a key
    would load, appear to work, and silently do nothing."""
    missing = [k for k in BENCH_KEYS if k not in DEFAULT_SETTINGS]
    assert missing == []


def test_the_shipped_lab_defaults_parse_cleanly():
    """config/defaults.ini is tracked in git — it must never ship with a typo."""
    from spec_echem.bench import REPO_DEFAULTS
    values, warnings = read_bench_file(REPO_DEFAULTS)
    assert warnings == []
    assert values["wavelength_min"] == 400.0
    assert values["lin_max_fill_pct"] == 85.0
    # Lab-wide Gamry preferences belong here: they're the same on every rig.
    assert values["save_dta"] is True
    assert values["trigger"] is True


def test_lab_defaults_exclude_machine_specific_settings():
    """These two differ between the Windows instrument box and a dev Mac:
      data_root         — a machine path; committing it makes every pull a conflict.
      potentiostat_mode — "python" needs 32-bit EchemToolkitPy; a 64-bit env can only
                          do "external", so asserting either would be false somewhere.
    They live only in each rig's own config/bench.ini."""
    from spec_echem.bench import REPO_DEFAULTS
    values, _ = read_bench_file(REPO_DEFAULTS)
    assert "data_root" not in values
    assert "potentiostat_mode" not in values


def test_the_repo_defaults_cover_every_machine_independent_key():
    """A key that's in the schema but nowhere in the lab defaults is easy to forget —
    which is exactly how save_dta and trigger got left out the first time."""
    from spec_echem.bench import REPO_DEFAULTS
    # Machine-specific keys are deliberately absent from the tracked lab defaults:
    # a path or a mode that is true on one rig is false on the next, and committing
    # one makes every pull a conflict. The autolab_* keys are all install paths and
    # NOVA procedure templates, so they belong here for the same reason data_root does.
    machine_specific = {"data_root", "potentiostat_mode"} | {
        k for k in BENCH_KEYS if k.startswith("autolab_")}
    values, _ = read_bench_file(REPO_DEFAULTS)
    missing = set(BENCH_KEYS) - machine_specific - set(values)
    assert missing == set(), f"not in config/defaults.ini: {sorted(missing)}"


# --- The bench file is hand-maintained: a write must not eat its comments ---------

REAL_WORLD_INI = """# this rig, set up 2026-09-11
[spectrometer]
# lands ~85% fill on the reference with the halogen source
integration_time_ms = 2.6439
scan_averages = 20

[autolab]
# MEASURED: bit 0 / pin 1 of P1.Port_A is the only line that fires the Avantes.
# The other seven gave no scan, so those are real negatives.
autolab_dio_mask = 1
autolab_ca_mode = ei
"""


def test_save_as_defaults_keeps_every_comment(tmp_path):
    """A configparser round-trip drops comments silently. These comments carry
    measured findings -- why a pin was chosen, what a value was proven to do -- so
    losing them to a 'Save as defaults' click destroys the record behind the rig."""
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    save_bench_defaults({"integration_time_ms": 3.0, "scan_averages": 25}, ini)

    text = ini.read_text(encoding="utf-8")
    assert "# MEASURED: bit 0 / pin 1 of P1.Port_A" in text
    assert "# lands ~85% fill on the reference" in text
    assert "# this rig, set up 2026-09-11" in text


def test_save_as_defaults_updates_in_place_and_leaves_the_rest_alone(tmp_path):
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    save_bench_defaults({"integration_time_ms": 3.0}, ini)

    values, warnings = read_bench_file(ini)
    assert values["integration_time_ms"] == 3.0     # changed
    assert values["scan_averages"] == 20            # not mentioned -> untouched
    assert values["autolab_dio_mask"] == 1
    assert values["autolab_ca_mode"] == "ei"
    assert warnings == []


def test_a_new_key_lands_in_its_own_section(tmp_path):
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    save_bench_defaults({"lin_start_ms": 1.05, "lin_stop_ms": 8.4}, ini)

    values, _ = read_bench_file(ini)
    assert values["lin_start_ms"] == 1.05
    assert values["lin_stop_ms"] == 8.4
    assert "# MEASURED: bit 0 / pin 1" in ini.read_text(encoding="utf-8")


# --- Per-detector records --------------------------------------------------------

def test_detector_floor_roundtrips_and_is_keyed_by_serial(tmp_path):
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    save_detector_floor("SN-TEST-0001", 1.048, ini)

    assert read_detector_floor("SN-TEST-0001", ini) == 1.048
    assert read_detector_floor("SOME-OTHER-UNIT", ini) is None


def test_recording_the_same_floor_again_writes_nothing(tmp_path):
    """Connect probes every time. Rewriting the file on every connect would churn a
    hand-maintained file for no reason."""
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    assert save_detector_floor("SN-TEST-0001", 1.048, ini) is not None
    before = ini.read_text(encoding="utf-8")
    assert save_detector_floor("SN-TEST-0001", 1.048, ini) is None
    assert ini.read_text(encoding="utf-8") == before


def test_a_detector_section_is_not_a_bench_setting(tmp_path):
    """It is a measured hardware fact, not a preference -- so it must not appear as a
    bench key, and must not warn as an unknown one either."""
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    save_detector_floor("SN-TEST-0001", 1.048, ini)

    values, warnings = read_bench_file(ini)
    assert "min_integration_ms" not in values
    assert warnings == []


def test_the_floor_survives_save_as_defaults(tmp_path):
    """Nothing in save_bench_defaults knows what a detector section means, so nothing
    in it may discard one."""
    ini = _write(tmp_path / "bench.ini", REAL_WORLD_INI)
    save_detector_floor("SN-TEST-0001", 1.048, ini)
    save_bench_defaults({"integration_time_ms": 3.0}, ini)

    assert read_detector_floor("SN-TEST-0001", ini) == 1.048
