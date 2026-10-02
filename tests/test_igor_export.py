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
    # The title is the WINDOW's: a TextBox anchored middle-top is drawn inside the
    # axes and landed on the data (Igor, 2026-10-02).
    title_cmd = next(c for c in commands if "DoWindow/T" in c)
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


# Loaded in Igor for the first time 2026-10-02 and it came out "very very minimal":
# every trace red markers (a bare ModifyGraph styles the WHOLE graph, so the fit was
# drawn as points on top of the data it is a line through), the residual plotted
# beside the data it belongs under, and no axis labels at all.

def test_the_fit_is_a_line_and_the_data_is_points(tmp_path):
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0], "fit": [1.0, 2.0]})
    path = tmp_path / "styled.itx"
    frame_to_itx(path, frame, prefix="seg")
    _waves, commands = parse_itx(path.read_text())

    modify = [c for c in commands if c.startswith("ModifyGraph")]
    assert modify, "no per-trace styling at all"
    # Every ModifyGraph names ITS trace: a bare one sets the whole graph.
    assert all("(" in c for c in modify), modify
    assert any("mode(seg_fit)=0" in c for c in modify), modify      # line
    assert any("mode(seg_y)=3" in c for c in modify), modify        # markers


def test_the_residual_is_written_but_not_plotted(tmp_path):
    """It shares the x axis and nothing else. Beside the data it is a flat line at
    zero that squashes everything, which is why the figure gives it its own panel."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0],
                          "fit": [1.0, 2.0], "residual": [0.01, -0.01]})
    path = tmp_path / "resid.itx"
    frame_to_itx(path, frame, prefix="seg")
    waves, commands = parse_itx(path.read_text())

    assert "seg_residual" in waves, "the residual should still be IN the file"
    display = next(c for c in commands if c.startswith("Display"))
    assert "seg_residual" not in display, display
    assert "seg_y" in display and "seg_fit" in display


def test_the_axes_are_labelled(tmp_path):
    """A bare number axis makes the reader guess seconds versus nanometres."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0]})
    path = tmp_path / "labels.itx"
    frame_to_itx(path, frame, xlabel="Time (s)", ylabel="Absorbance")
    _waves, commands = parse_itx(path.read_text())
    assert any('Label bottom "Time (s)"' in c for c in commands), commands
    assert any('Label left "Absorbance"' in c for c in commands), commands



def test_each_trace_gets_its_own_colour(tmp_path):
    """Without one Igor draws every trace the same and the fit is indistinguishable
    from the data it runs through -- seen in Igor, where both came out red."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0], "fit": [1.0, 2.0]})
    path = tmp_path / "colour.itx"
    frame_to_itx(path, frame, prefix="seg")
    _waves, commands = parse_itx(path.read_text())

    colours = {}
    for c in commands:
        if c.startswith("ModifyGraph") and "rgb(" in c:
            name = c.split("rgb(")[1].split(")")[0]
            colours[name] = c.split("rgb(" + name + ")=")[1].split(")")[0] + ")"
    assert len(colours) == 2, colours
    assert len(set(colours.values())) == 2, f"two traces, one colour: {colours}"


def test_the_title_does_not_go_inside_the_plot(tmp_path):
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0]})
    path = tmp_path / "title.itx"
    frame_to_itx(path, frame, title="Doping 7 @ 815.2 nm")
    _waves, commands = parse_itx(path.read_text())
    assert not any("TextBox" in c for c in commands), commands
    assert any('DoWindow/T' in c and "Doping 7" in c for c in commands), commands


# 2026-10-02: the spectra view offered neither CSV nor Igor, on one argument -- a
# matrix in a CSV is a worse copy of what the .h5 already holds. That is right for
# CSV and WRONG for Igor, where a 2-D wave is a first-class object and an image or
# waterfall of a doping step is what it is good at.

def _block():
    wl = np.linspace(400.0, 1100.0, 40)
    t = np.linspace(0.0, 60.0, 25)
    values = np.outer(np.exp(-0.5 * ((wl - 800.0) / 60.0) ** 2),
                      1 - np.exp(-t / 5.0))
    return pd.DataFrame(values, index=wl, columns=t)


def test_a_spectra_block_becomes_a_scaled_2d_wave(tmp_path):
    from spec_echem.igor_export import matrix_to_itx

    frame = _block()
    path = tmp_path / "block.itx"
    matrix_to_itx(path, frame, "Doping5_spectra", row_unit="nm", col_unit="s")

    text = path.read_text().replace("\r\n", "\n")
    assert "WAVES/D/N=(40,25)\tDoping5_spectra_A" in text, "not a 2-D wave"
    # The axis waves are written TOO: SetScale assumes a uniform grid and the
    # detector's wavelengths are only nearly uniform.
    assert "Doping5_spectra_wl" in text and "Doping5_spectra_t" in text
    commands = [l[2:] for l in text.split("\n") if l.startswith("X ")]
    assert any(c.startswith("SetScale/I x") and '"nm"' in c for c in commands)
    assert any(c.startswith("SetScale/I y") and '"s"' in c for c in commands)
    assert any(c.startswith("NewImage") for c in commands)


def test_the_scale_is_plain_numbers_igor_can_parse(tmp_path):
    """numpy renders a scalar as "np.float64(380.88)", which Igor cannot parse and
    which fails the whole load."""
    from spec_echem.igor_export import matrix_to_itx

    path = tmp_path / "scale.itx"
    matrix_to_itx(path, _block(), "seg", row_unit="nm", col_unit="s")
    text = path.read_text()
    assert "np.float64" not in text
    assert "np." not in text


def test_the_matrix_is_not_written_at_full_repr_width(tmp_path):
    """repr() spends 17 characters on each of ~760k numbers -- 13 MB of digits the
    float32 storage does not have."""
    from spec_echem.igor_export import matrix_to_itx

    path = tmp_path / "wide.itx"
    matrix_to_itx(path, _block(), "seg")
    rows = [l for l in path.read_text().split("\n") if l.startswith("\t0.")]
    assert rows, "no data rows found"
    assert max(len(v) for v in rows[0].split("\t") if v) < 14


def test_the_residual_gets_its_own_panel_above_the_data(tmp_path):
    """As in the figure these numbers come from -- the convention in the
    spectroscopy this sits beside. On the same axes it is a flat line at zero that
    squashes everything; left out entirely it is the one thing that says whether
    the fit is any good."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0],
                          "fit": [1.0, 2.0], "residual": [0.01, -0.01]})
    path = tmp_path / "resid.itx"
    frame_to_itx(path, frame, prefix="seg")
    _waves, commands = parse_itx(path.read_text())

    assert any("AppendToGraph/L=resid" in c and "seg_residual" in c
               for c in commands), commands
    # ABOVE: its axis band starts higher than the data's ends.
    data_span = next(c for c in commands if "axisEnab(left)" in c)
    resid_span = next(c for c in commands if "axisEnab(resid)" in c)
    data_top = float(data_span.split("{")[1].split("}")[0].split(",")[1])
    resid_bottom = float(resid_span.split("{")[1].split("}")[0].split(",")[0])
    assert resid_bottom > data_top, (data_span, resid_span)
    # ...and the display still only puts data and fit on the main axes.
    display = next(c for c in commands if c.startswith("Display"))
    assert "seg_residual" not in display


def test_the_legend_names_the_data_not_the_wave(tmp_path):
    """Igor's automatic legend reads "Doping6_fit_biexp_y", which is the file's
    business and not the reader's."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0], "fit": [1.0, 2.0]})
    path = tmp_path / "leg.itx"
    frame_to_itx(path, frame, prefix="Doping6_fit_biexp")
    _waves, commands = parse_itx(path.read_text())

    legend = next(c for c in commands if c.startswith("Legend"))
    assert " data" in legend and " fit" in legend, legend
    # the trace symbol is drawn, so the colours in the legend match the plot
    assert "\\s(Doping6_fit_biexp_y)" in legend, legend
