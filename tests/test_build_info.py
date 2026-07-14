"""Build identity: parsing `git describe`, and the version reaching the data folder."""
import json

from spec_echem.build_info import __version__, _format_build_id, build_id
from spec_echem.data import write_run_metadata
from spec_echem.settings import DEFAULT_SETTINGS


def test_exactly_on_a_clean_tag_is_just_the_version():
    assert _format_build_id("v0.2.0") == __version__


def test_commits_past_the_tag_carry_the_count_and_sha():
    assert _format_build_id("v0.2.0-5-gaadf15a") == __version__ + "+5.gaadf15a"


def test_a_dirty_tree_says_so():
    """Data written from an edited working tree is not reproducible from any commit —
    the build id has to admit that."""
    assert _format_build_id("v0.2.0-5-gaadf15a-dirty") == __version__ + "+5.gaadf15a.dirty"
    assert _format_build_id("v0.2.0-dirty") == __version__ + "+dirty"


def test_a_repo_with_no_tags_still_identifies_the_commit():
    assert _format_build_id("1c0ffee") == __version__ + "+g1c0ffee"


def test_no_git_at_all_falls_back_to_the_bare_version():
    """Installed from an archive: no .git, maybe no git binary. Not an error."""
    assert _format_build_id(None) == __version__
    assert _format_build_id("") == __version__


def test_build_id_never_raises_and_starts_with_the_version():
    got = build_id()
    assert got.startswith(__version__)


def test_the_run_folder_records_which_code_wrote_it(tmp_path):
    """The whole point: settings alone don't identify the code, and behaviour has changed
    across versions (the wavelength crop). A data folder must say what produced it."""
    path = write_run_metadata(DEFAULT_SETTINGS.copy(), tmp_path, "20260714_Test")
    meta = json.loads(path.read_text(encoding="utf-8"))

    assert meta["spec_echem_version"] == build_id()
    assert meta["spec_echem_version"].startswith(__version__)
