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

from spec_echem.igor_export import (MAX_NAME, frame_to_itx, spectra_to_itx,
                                    wave_name, write_itx)


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
            # N=(rows) or N=(rows,cols) -- a 2-D wave writes one ROW per line, which
            # is why this splits on tabs rather than calling float() on the line.
            shape = tuple(int(n) for n in
                          line[line.index("N=(") + 3:line.index(")")].split(","))
            assert lines[i + 1] == "BEGIN"
            rows, i = [], i + 2
            while lines[i] != "END":
                rows.append([float(v) for v in lines[i].strip().split("\t")])
                i += 1
            block = np.array(rows)
            waves[name] = block.reshape(-1) if block.shape[1] == 1 else block
            assert waves[name].shape == shape, f"{name} declared {shape}"
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


def test_the_residual_axis_sits_at_the_edge_not_on_the_data(tmp_path):
    """freePos(axis)=0 means x=0 in DATA units, which put the residual axis and its
    label on top of the trace. {0,kwFraction} is the left edge of the plot area."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0],
                          "fit": [1.0, 2.0], "residual": [0.01, -0.01]})
    path = tmp_path / "axis.itx"
    frame_to_itx(path, frame, prefix="seg")
    _waves, commands = parse_itx(path.read_text())
    free = next(c for c in commands if "freePos" in c)
    assert "kwFraction" in free, free


def test_the_graph_is_boxed_like_the_figure(tmp_path):
    """matplotlib draws all four spines; Igor draws two. Without mirroring, the
    Igor graph and the saved PNG do not read as the same plot."""
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0],
                          "fit": [1.0, 2.0], "residual": [0.0, 0.0]})
    path = tmp_path / "box.itx"
    frame_to_itx(path, frame, prefix="seg")
    _waves, commands = parse_itx(path.read_text())
    assert any("mirror(bottom)=1" in c and "mirror(left)=1" in c for c in commands)
    # the residual panel is boxed too, or the two panels do not match
    assert any("mirror(resid)=1" in c for c in commands), commands


def test_the_symbols_match_the_figure(tmp_path):
    from spec_echem.igor_export import FIT_WIDTH, MARKER_SIZE

    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0], "fit": [1.0, 2.0]})
    path = tmp_path / "sym.itx"
    frame_to_itx(path, frame, prefix="seg")
    _waves, commands = parse_itx(path.read_text())
    assert any(f"msize(seg_y)={MARKER_SIZE}" in c for c in commands), commands
    assert any(f"lsize(seg_fit)={FIT_WIDTH}" in c for c in commands), commands


# ---------------------------------------------------------------------------
# The spectra block: wavelength x time
# ---------------------------------------------------------------------------

def spectra_frame(n_wl=40, n_t=721):
    """A block shaped like a real CV: many more times than a fan would show."""
    wl = np.linspace(400.0, 1100.0, n_wl)
    t = np.round(np.arange(n_t) * 0.1, 3)
    # Absorbance that rises then falls, so the LAST spectrum resembles the first --
    # as it does in a CV, where the sweep returns to where it started.
    ramp = 1.0 - np.abs(np.linspace(-1.0, 1.0, n_t))
    return pd.DataFrame(np.outer(np.exp(-((wl - 520.0) / 90.0) ** 2), ramp),
                        index=wl, columns=t)


def test_every_spectrum_is_exported_not_a_thinned_fan(tmp_path):
    """The figure draws all 721 spectra as a dense band. An export that keeps 25 of
    them is a different plot -- which is what it looked like in Igor (2026-10-02)."""
    frame = spectra_frame()
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, frame, "cv_spectra")
    waves, _ = parse_itx(path.read_text())
    assert waves["cv_spectra_a"].shape == frame.shape
    assert waves["cv_spectra_t"].size == frame.shape[1] == 721


def test_the_block_round_trips(tmp_path):
    frame = spectra_frame(n_wl=12, n_t=30)
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, frame, "cv_spectra")
    waves, _ = parse_itx(path.read_text())
    assert np.allclose(waves["cv_spectra_a"], frame.to_numpy(), rtol=1e-6)
    assert np.allclose(waves["cv_spectra_wl"], frame.index.values)
    assert np.allclose(waves["cv_spectra_t"], frame.columns.values)


def test_every_column_reaches_the_graph_and_gets_its_own_colour(tmp_path):
    frame = spectra_frame(n_wl=8, n_t=50)
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, frame, "cv_spectra")
    _, commands = parse_itx(path.read_text())
    plotted = [c for c in commands
               if c.startswith("Display ") or c.startswith("AppendToGraph ")]
    assert len(plotted) == 50
    # Igor names the first trace after the wave and the rest wave#1, wave#2, ...
    coloured = [c for c in commands if c.startswith("ModifyGraph rgb(")]
    targets = [c[len("ModifyGraph rgb("):c.index(")")] for c in coloured]
    assert targets == ["cv_spectra_a"] + [f"cv_spectra_a#{j}" for j in range(1, 50)]


def test_no_command_is_longer_than_igor_will_take(tmp_path):
    """The reason the block is one 2-D wave: a single Display naming 721 traces is a
    14 KB command line, past what Igor accepts. 400 is the most conservative limit."""
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, spectra_frame(), "cv_spectra")
    _, commands = parse_itx(path.read_text())
    longest = max(commands, key=len)
    assert len(longest) <= 400, longest


def test_a_gap_in_the_block_survives(tmp_path):
    frame = spectra_frame(n_wl=6, n_t=10)
    frame.iloc[2, 3] = np.nan
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, frame, "cv_spectra")
    waves, _ = parse_itx(path.read_text())
    assert np.isnan(waves["cv_spectra_a"][2, 3])
    assert np.isfinite(waves["cv_spectra_a"]).sum() == frame.size - 1


def test_the_cap_still_thins_and_keeps_both_ends(tmp_path):
    frame = spectra_frame(n_wl=6, n_t=721)
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, frame, "cv_spectra", traces=25)
    waves, _ = parse_itx(path.read_text())
    assert waves["cv_spectra_a"].shape == (6, 25)
    assert waves["cv_spectra_t"][0] == frame.columns[0]
    assert waves["cv_spectra_t"][-1] == frame.columns[-1]


def test_the_axes_and_title_are_set(tmp_path):
    path = tmp_path / "cv.itx"
    spectra_to_itx(path, spectra_frame(n_wl=5, n_t=4), "cv_spectra",
                   title='CV  (-0.499 to +0.699 V)',
                   xlabel="Wavelength (nm)", ylabel="Absorbance")
    _, commands = parse_itx(path.read_text())
    assert 'Label bottom "Wavelength (nm)"' in commands
    assert 'Label left "Absorbance"' in commands
    assert any(c.startswith("DoWindow/T kwTopWin") and "+0.699 V" in c
               for c in commands)


# 2026-10-03 review: Igor wave names are global, and cutting a long basename to fit
# kept its start and dropped what told two exports apart -- so loading the second
# silently overwrote the first.

@pytest.mark.parametrize("a, b", [
    ("Doping7_absorbance_exp", "Doping7_absorbance_biexp"),                  # the model
    ("Doping10_kinetics_800nm_absorbance", "Doping10_kinetics_520nm_absorbance"),  # the wavelength
])
def test_two_exports_that_differ_late_in_their_names_do_not_share_waves(tmp_path, a, b):
    frame = pd.DataFrame({"time": [0.0, 1.0], "y": [0.1, 0.2], "fit": [0.1, 0.2]})
    names = []
    for prefix in (a, b):
        path = tmp_path / f"{prefix}.itx"
        frame_to_itx(path=path, frame=frame, prefix=prefix)
        names.append(set(parse_itx(path.read_text())[0]))
    assert not names[0] & names[1], names[0] & names[1]
    assert all(len(n) <= MAX_NAME for n in names[0] | names[1])


def test_a_short_prefix_is_left_readable(tmp_path):
    """The checksum is only for names that would otherwise be cut."""
    path = tmp_path / "f.itx"
    frame_to_itx(path=path, frame=pd.DataFrame({"t": [0.0, 1.0], "y": [1.0, 2.0]}),
                 prefix="Doping7_tau")
    assert set(parse_itx(path.read_text())[0]) == {"Doping7_tau_t", "Doping7_tau_y"}


def test_the_same_basename_always_gets_the_same_waves(tmp_path):
    """Deterministic, so re-exporting a figure REPLACES its own waves in Igor rather
    than piling up copies -- which is what Python's salted hash() would have done."""
    frame = pd.DataFrame({"t": [0.0, 1.0], "y": [1.0, 2.0]})
    runs = []
    for i in range(2):
        path = tmp_path / f"{i}.itx"
        frame_to_itx(path=path, frame=frame, prefix="Doping10_kinetics_800nm_absorbance")
        runs.append(set(parse_itx(path.read_text())[0]))
    assert runs[0] == runs[1]


def test_spectra_exports_that_differ_late_do_not_share_waves(tmp_path):
    names = []
    for name in ("Doping10_spectra_window_a", "Doping10_spectra_window_b"):
        path = tmp_path / f"{name}.itx"
        spectra_to_itx(path, spectra_frame(n_wl=4, n_t=3), name)
        names.append(set(parse_itx(path.read_text())[0]))
    assert not names[0] & names[1]
