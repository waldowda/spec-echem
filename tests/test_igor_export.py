"""
Igor Text (.itx) export.

Igor is not on this machine, which is the point of choosing .itx over .pxp: it is
plain text and documented, so the file can be checked here. These tests read it back
the way Igor's loader does -- IGOR header, WAVES declarations, BEGIN/END blocks, X
commands -- rather than comparing it to a golden string, so formatting can change
without the tests becoming busywork.
"""
import numpy as np
import pandas as pd
import pytest

from spec_echem.igor_export import MAX_NAME, frame_to_itx, wave_name, write_itx


def parse_itx(text):
    """({name: values}, [X commands]) from an .itx, as Igor's loader would see it."""
    lines = text.replace("\r\n", "\n").split("\n")
    assert lines[0] == "IGOR", "an .itx must begin with the IGOR keyword"
    waves, commands = {}, []
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.startswith("WAVES"):
            name = line.split("\t")[-1]
            assert lines[i + 1] == "BEGIN"
            values, i = [], i + 2
            while lines[i] != "END":
                values.append(float(lines[i].strip()))
                i += 1
            waves[name] = np.array(values)
        elif line.startswith("X "):
            commands.append(line[2:])
        i += 1
    return waves, commands


def test_a_frame_becomes_waves_and_a_display(tmp_path):
    frame = pd.DataFrame({"x": [0.0, 1.0, 2.0],
                          "y": [1.0, 2.0, 3.0],
                          "fit": [1.1, 2.1, 3.1]})
    path = tmp_path / "fig.itx"
    frame_to_itx(path, frame, title="Doping 7")

    waves, commands = parse_itx(path.read_text())
    assert set(waves) == {"x", "y", "fit"}
    np.testing.assert_allclose(waves["y"], [1.0, 2.0, 3.0])
    # The FIRST numeric column is the x axis; the rest are plotted against it.
    assert any(c.startswith("Display ") and " vs x" in c for c in commands), commands


def test_nan_survives_as_a_gap(tmp_path):
    """A failed fit is NaN, and Igor draws NaN as a gap -- which is what it means.
    Writing 0 or dropping the row would both assert something the fit did not."""
    frame = pd.DataFrame({"wl": [800.0, 801.0, 802.0],
                          "tau": [1.0, float("nan"), 3.0]})
    path = tmp_path / "gap.itx"
    frame_to_itx(path, frame)

    assert "NaN" in path.read_text()
    waves, _ = parse_itx(path.read_text())
    assert np.isnan(waves["tau"][1])
    assert waves["tau"].size == 3, "the row was dropped instead of kept as a gap"


@pytest.mark.parametrize("raw, expected", [
    ("tau", "tau"),
    ("mean tau (s), 95% CI", "mean_tau_s_95_CI"),
    ("2theta", "w_2theta"),              # Igor names must start with a letter
    ("", "wave"),
    ("doped_to_V", "doped_to_V"),
])
def test_column_names_become_legal_igor_names(raw, expected):
    assert wave_name(raw) == expected


def test_a_long_name_is_truncated_and_still_unique():
    used = set()
    long = "a_very_long_column_name_that_igor_will_not_accept_at_all"
    first = wave_name(long, used)
    second = wave_name(long, used)
    assert len(first) <= MAX_NAME and len(second) <= MAX_NAME
    assert first != second, "two columns collapsed to one name, losing a wave"


def test_colliding_names_do_not_overwrite_each_other(tmp_path):
    """'tau (s)' and 'tau/s' both reduce to 'tau_s'. Without uniquing, one of the
    two columns is simply missing from the file."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "tau (s)": [1.0, 2.0], "tau/s": [3.0, 4.0]})
    path = tmp_path / "collide.itx"
    frame_to_itx(path, frame)

    waves, _ = parse_itx(path.read_text())
    assert len(waves) == 3, waves
    assert sorted(v[0] for v in waves.values()) == [1.0, 1.0, 3.0]


def test_a_title_with_a_quote_does_not_break_the_file(tmp_path):
    """An unescaped quote ends Igor's string early and the whole load fails."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0]})
    path = tmp_path / "quote.itx"
    frame_to_itx(path, frame, title='Doping 7 "the odd one" \\ back')

    text = path.read_text()
    _waves, commands = parse_itx(text)
    title_cmd = next(c for c in commands if "TextBox" in c)
    # Every quote inside the string is escaped; the only bare ones are the delimiters.
    body = title_cmd[title_cmd.index('"'):]
    assert body.count('"') - body.count('\\"') == 2, title_cmd


def test_provenance_is_written_as_igor_comments(tmp_path):
    frame = pd.DataFrame({"x": [1.0], "y": [2.0]})
    path = tmp_path / "prov.itx"
    frame_to_itx(path, frame, notes=["run: 20250710", "produced by: spec-echem 0.3.1"])
    _waves, commands = parse_itx(path.read_text())
    assert any("run: 20250710" in c for c in commands)


def test_waves_without_a_display_are_still_valid(tmp_path):
    path = tmp_path / "data.itx"
    write_itx(path, {"a": [1.0, 2.0], "b": [3.0, 4.0]})
    waves, commands = parse_itx(path.read_text())
    assert set(waves) == {"a", "b"}
    assert not any(c.startswith("Display") for c in commands)


def test_two_segments_can_live_in_one_igor_experiment(tmp_path):
    """Igor waves are GLOBAL. Without a prefix both exports give waves called x, y
    and fit, and loading the second silently overwrites the first -- which breaks
    the obvious reason for exporting two in the first place."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0], "fit": [1.0, 2.0]})
    names = {}
    for label in ("Doping5_fit", "Doping7_fit"):
        path = tmp_path / f"{label}.itx"
        frame_to_itx(path, frame, prefix=label)
        waves, _commands = parse_itx(path.read_text())
        names[label] = set(waves)

    assert not (names["Doping5_fit"] & names["Doping7_fit"]), names
    assert all(n.startswith("Doping5_fit") for n in names["Doping5_fit"])


def test_a_long_prefix_still_leaves_the_column_readable(tmp_path):
    """Truncating from the right would leave every wave called the same thing with
    the part that distinguishes them cut off."""
    frame = pd.DataFrame({"x": [1.0], "y": [2.0], "fit": [3.0]})
    path = tmp_path / "long.itx"
    frame_to_itx(path, frame, prefix="Doping5_kinetics_800nm_absorbance")

    waves, _commands = parse_itx(path.read_text())
    assert len(waves) == 3, waves
    assert all(len(n) <= MAX_NAME for n in waves)
    # the column part survives, which is what tells the waves apart
    assert any(n.endswith("_fit") for n in waves), waves
    assert any(n.endswith("_y") for n in waves), waves


def test_the_display_uses_the_prefixed_names(tmp_path):
    """A Display naming unprefixed waves would fail to load."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0]})
    path = tmp_path / "disp.itx"
    frame_to_itx(path, frame, prefix="Doping5_fit")
    waves, commands = parse_itx(path.read_text())
    display = next(c for c in commands if c.startswith("Display"))
    for name in waves:
        if name.endswith("_x") or name.endswith("_y"):
            assert name in display, display
