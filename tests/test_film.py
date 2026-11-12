"""The film-size history beside a run (2026-11-11)."""
import json

import pytest

from spec_echem import film

RUN = {"film_thickness_nm": 150.0, "film_area_cm2": 1.6, "film_thickness_u_nm": 0.0,
       "film_area_u_cm2": 0.0, "film_size_status": "estimate"}


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "20261111_run"
    f.mkdir()
    meta = f / "20261111_run_metadata.json"
    meta.write_text(json.dumps({"settings": RUN}))
    return f


def test_without_a_file_the_run_metadata_is_used_and_nothing_is_written(folder):
    entry, history = film.current(folder, RUN, "2026-11-11T09:00:00")
    assert entry["thickness_nm"] == 150.0 and entry["status"] == "estimate"
    assert entry["source"] == "run metadata" and len(history) == 1
    assert not film.film_path(folder).exists()


def test_the_first_edit_records_the_estimate_first_and_the_latest_wins(folder):
    before = (folder / "20261111_run_metadata.json").read_text()
    film.add_entry(folder, film.new_entry(142.0, 1.55, "measured", 5.0, 0.02,
                                          note="profilometer"), RUN)
    entry, history = film.current(folder, RUN)
    assert [h["status"] for h in history] == ["estimate", "measured"]
    assert history[0]["thickness_nm"] == 150.0
    assert entry["thickness_nm"] == 142.0 and entry["note"] == "profilometer"
    # The run's own record is never touched.
    assert (folder / "20261111_run_metadata.json").read_text() == before

    film.add_entry(folder, film.new_entry(140.0, 1.55, "measured"), RUN)
    entry, history = film.current(folder, RUN)
    assert len(history) == 3 and entry["thickness_nm"] == 140.0
    assert history[1]["thickness_nm"] == 142.0          # nothing overwritten


def test_an_old_run_without_a_status_says_so(folder):
    old = {k: v for k, v in RUN.items() if k != "film_size_status"}
    entry, _ = film.current(folder, old)
    assert entry["status"] == "estimate"
    assert "status not recorded" in entry["source"]


@pytest.mark.parametrize("bad", [
    dict(thickness_nm=0.0, area_cm2=1.6, status="measured"),
    dict(thickness_nm=150.0, area_cm2=-1.0, status="measured"),
    dict(thickness_nm=150.0, area_cm2=1.6, status="guess"),
    dict(thickness_nm=150.0, area_cm2=1.6, status="measured", thickness_u_nm=-1.0),
])
def test_impossible_entries_are_refused(bad):
    with pytest.raises(ValueError):
        film.new_entry(**bad)


def test_an_unreadable_file_is_reported_not_ignored(folder):
    film.film_path(folder).write_text("{not json")
    with pytest.raises(film.FilmFileError):
        film.current(folder, RUN)


def test_a_measured_film_is_copied_from_another_run(tmp_path, folder):
    other = tmp_path / "20261110_cv"
    other.mkdir()
    film.add_entry(other, film.new_entry(138.0, 1.5, "measured", note="profilometer"),
                   RUN)
    copy = film.copied_entry(other, RUN)
    film.add_entry(folder, copy, RUN)
    entry, history = film.current(folder, RUN)
    assert entry["thickness_nm"] == 138.0 and entry["status"] == "measured"
    assert entry["source"].startswith("copied from 20261110_cv (Charge tab)")
    assert len(history) == 2


def test_describe_flags_an_estimate_as_provisional():
    est = film.entry_from_settings(RUN)
    assert film.describe(est) == "150 nm x 1.6 cm2, ESTIMATE (provisional)"
    meas = film.new_entry(142.0, 1.55, "measured", note="profilometer")
    assert film.describe(meas).startswith("142 nm x 1.55 cm2, measured 20")
    assert film.describe(meas).endswith("(profilometer)")


def test_a_run_that_recorded_no_film_starts_its_history_at_the_first_edit(folder):
    film.add_entry(folder, film.new_entry(120.0, 1.2, "measured"), {})
    entry, history = film.current(folder, {})
    assert len(history) == 1 and entry["thickness_nm"] == 120.0
