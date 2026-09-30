"""One definition of how a segment is named in a dropdown, shared by the Results,
Analysis and Band Fits tabs.

There were three near-copies of this, and they drifted: the Band Fits tab showed a
bare "Doping 7" while the other two named its potential, and only the Results tab
carried the long-ladder popup fixes. The same segment has to read the same way
wherever it is selected -- a potential seen on one tab and not another is the kind of
mismatch that gets attributed to the data.

Kept out of gui/main_window.py deliberately: main_window imports the tabs, so a
constant living there could not be imported back by them.
"""

# A 0.2-0.7 V ladder in 0.1 V steps is 14 reviewable segments, and Qt's default
# maxVisibleItems is 10 -- so on 2026-09-11 the Results tab appeared to be missing
# Doping/Dedoping 4 and 5. The data was all there; the 10th entry was simply the last
# one visible. A run that looks like it lost the end of its ladder is exactly the
# wrong thing for a results view to imply.
SEGMENT_COMBO_VISIBLE = 26

# Wide enough for the longest label the formatter produces -- a dedoping step reads
# "Dedoping 12  (-0.500 V after +0.700 V)". Set on all three combos so a tab does not
# elide what its neighbour shows in full.
SEGMENT_COMBO_MIN_WIDTH = 300


def segment_display(win, label):
    """'Doping 4' -> 'Doping 4  (+0.600 V)'.

    Falls back to the bare label when the segment is not in this run's map -- a
    folder loaded from another session, say -- rather than guessing a potential from
    the current settings, which would be worse than none.
    """
    seg = win.segments_by_label.get(label)
    if seg is None:
        return label
    text = win.segment_potential_text(seg)
    return f"{label}  ({text})" if text else label


def prepare_segment_combo(combo):
    """Apply the shared sizing/popup behaviour to a segment dropdown."""
    combo.setMaxVisibleItems(SEGMENT_COMBO_VISIBLE)
    # Qt ignores maxVisibleItems when a style uses a NATIVE popup (Windows does).
    # This forces the list-view popup, which honors it and scrolls beyond it.
    combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
    combo.setMinimumWidth(SEGMENT_COMBO_MIN_WIDTH)
