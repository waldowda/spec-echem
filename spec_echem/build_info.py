"""
Version and build identity — the single source of truth for "which code is this?"

`__version__` lives here (setup.py reads it out by regex, so the two can't drift), and
`build_id()` sharpens it with the git commit when there is one.

Why bother: a run folder records every *setting* it used but, until now, nothing about the
code that applied them. Two runs a release apart looked identical on disk even though one
of them could crop the wavelength axis and the other couldn't. Most runs happen on commits
*between* tags, so a bare "0.2.0" wouldn't have distinguished them either.

No Qt. No hardware. No heavy imports — this is called at run start and on GUI launch.
"""
import subprocess
from pathlib import Path

__version__ = "0.2.0"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_cached_build_id = None


def _git_describe():
    """`git describe` for the repo this file lives in, or None if that's not possible.

    Never raises: a released copy has no .git, git may not be installed, and neither is
    an error — you just fall back to the bare version.
    """
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty", "--match", "v*"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 — no git, not a repo, timeout: all mean "no build id"
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def build_id():
    """
    A build identifier, e.g.

        "0.2.0"                    exactly at the v0.2.0 tag, clean tree
        "0.2.0+5.gaadf15a"         5 commits past the tag
        "0.2.0+5.gaadf15a.dirty"   ...with uncommitted changes
        "0.2.0+g1c0ffee"           a git checkout with no tags yet
        "0.2.0"                    no git at all (installed from an archive)

    Cached: git is only consulted once per process.
    """
    global _cached_build_id
    if _cached_build_id is not None:
        return _cached_build_id

    _cached_build_id = _format_build_id(_git_describe())
    return _cached_build_id


def _format_build_id(described):
    """Turn `git describe` output into a build id. Pure — the unit tests drive this."""
    if not described:
        return __version__

    local = []
    if described.endswith("-dirty"):
        described = described[: -len("-dirty")]
        local.append("dirty")

    # "v0.2.0-5-gaadf15a" -> tag v0.2.0, 5 commits ahead, at gaadf15a
    parts = described.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].startswith("g"):
        local.insert(0, parts[2])           # gaadf15a
        local.insert(0, parts[1])           # 5
    elif not described.startswith("v"):
        local.insert(0, "g" + described)    # no tags: describe fell back to a bare sha

    if not local:
        return __version__                  # sitting exactly on the tag, clean
    return "{}+{}".format(__version__, ".".join(local))
