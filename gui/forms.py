"""
One definition of how a labelled form behaves, because Qt's defaults do not survive
the trip between machines.

A QFormLayout takes BOTH its field-growth policy and its label alignment from the
STYLE, not from the layout, and the two platforms disagree on both:

    style        labelAlignment   fieldGrowthPolicy      formAlignment
    macintosh    Right            FieldsStayAtSizeHint   HCenter | Top
    Windows      Left             AllNonFixedFieldsGrow  Left | Top
    Fusion       Left             AllNonFixedFieldsGrow  Left | Top

Side by side on 2026-10-01 that made the same tab look like two different programs:
a "0.050 V" field was ~110 px on the Mac and the full width of the group on Win11
with its stepper pushed off the right edge; the labels sat against their fields on
one and far from them on the other; and on the Mac a whole group floated to the
middle of its box with a wide empty strip down the left.

All three are set here, and NOT all three come from the same platform -- each was
chosen on its merits rather than inherited:

  labelAlignment     Right, as macOS did. A right-aligned label sits next to the
                     field it names instead of being pushed away from it by however
                     wide the longest label in the group happens to be.
  fieldGrowthPolicy  ExpandingFieldsGrow, which is neither default. A field grows
                     only if it ASKS to: QLineEdit does -- a sample name, a folder,
                     a path all want the room -- while QDoubleSpinBox and QComboBox
                     do not, so a potential stays the width of a potential.
  formAlignment      Left, as Windows did. Centring the block leaves a gap down the
                     left of every group and moves the labels when an unrelated row
                     changes width, which reads as a layout bug rather than a style.
"""
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QFormLayout, QSizePolicy


def form_layout(parent=None):
    """A QFormLayout that lays out the same way on every platform."""
    layout = QFormLayout(parent)
    layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    layout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    return layout


def fill_width(widget):
    """Let a field use the whole row rather than the field column's width.

    A wrapping QLabel asks for no width of its own, so under ExpandingFieldsGrow it
    sits at whatever the widest OTHER field in the form happens to be -- a combo box,
    usually -- and an explanatory note then wraps to that width with the rest of the
    row left empty beside it. Reported 2026-10-01: "the text below the input field is
    only as wide as the input field. Wastes space?"

    Returns the widget, so it can wrap a constructor call.
    """
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Expanding)
    widget.setSizePolicy(policy)
    return widget
