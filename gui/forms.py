"""
One definition of how a labelled form behaves, because Qt's defaults do not survive
the trip between machines.

A QFormLayout takes BOTH its field-growth policy and its label alignment from the
STYLE, not from the layout, and the two platforms disagree on both:

    style        labelAlignment   fieldGrowthPolicy
    macintosh    Right            FieldsStayAtSizeHint
    Windows      Left             AllNonFixedFieldsGrow
    Fusion       Left             AllNonFixedFieldsGrow

Side by side on 2026-10-01 that made the same tab look like two different programs:
a "0.050 V" field was ~110 px on the Mac and the full width of the group on Win11
with its stepper pushed off the right edge, and the labels sat against their fields
on one and far from them on the other.

Both are now set here, to what the Mac was doing, because that is the one that reads
as a form: a right-aligned label sits next to the field it names instead of being
separated from it by however wide the longest label in the group happens to be.

ExpandingFieldsGrow rather than FieldsStayAtSizeHint is the one deliberate
difference. A field then grows only if it ASKS to: QLineEdit does -- a sample name,
a folder, a path all want the room -- while QDoubleSpinBox and QComboBox do not, so
a potential stays the width of a potential.
"""
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QFormLayout


def form_layout(parent=None):
    """A QFormLayout that lays out the same way on every platform."""
    layout = QFormLayout(parent)
    layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return layout
