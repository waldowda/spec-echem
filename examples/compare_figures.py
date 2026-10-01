#!/usr/bin/env python
"""
Compare two saved figures pixel by pixel.

The figure-export work exists so that a figure saved on one machine is the SAME
figure saved on another -- same inches, same fonts, same layout, whatever the window
it was drawn in. "It looks fine on both" does not establish that; this does.

    python examples/compare_figures.py mac/Doping5_absorbance.png \
                                       win11/Doping5_absorbance.png

Save the same segment at the same size preset on each machine, then run this on one
of them with both files to hand. A size mismatch means the preset did not take; a
small number of differing pixels usually means a font substitution (the two machines
resolved a different face for the same family); a large number means the layout
itself differs, which is the thing that must not happen.
"""
import sys

import numpy as np

try:
    from PIL import Image
except ImportError:                                  # pragma: no cover
    sys.exit("Pillow is needed: pip install pillow")


def compare(path_a, path_b):
    a = np.asarray(Image.open(path_a).convert("RGB"), dtype=int)
    b = np.asarray(Image.open(path_b).convert("RGB"), dtype=int)
    if a.shape != b.shape:
        print(f"DIFFERENT SIZE: {a.shape[1]}x{a.shape[0]} vs {b.shape[1]}x{b.shape[0]}")
        print("The size preset did not take on one of them, or the dpi differed.")
        return 1

    diff = np.abs(a - b).max(axis=2)
    differing = int((diff > 0).sum())
    total = a.shape[0] * a.shape[1]
    print(f"size            {a.shape[1]}x{a.shape[0]}")
    print(f"differing px    {differing}  ({100 * differing / total:.3f}%)")
    print(f"max delta       {diff.max()}")
    if differing == 0:
        print("\nIDENTICAL.")
        return 0
    # Where they differ says what differed: scattered pixels are glyph rendering,
    # whole columns or rows are a layout shift.
    rows = np.flatnonzero(diff.any(axis=1))
    cols = np.flatnonzero(diff.any(axis=0))
    print(f"rows affected   {rows.min()}-{rows.max()} of {a.shape[0]}")
    print(f"cols affected   {cols.min()}-{cols.max()} of {a.shape[1]}")
    print("\nNOT identical. A few scattered pixels is usually a font substitution;"
          "\ndifferences spanning most of the image mean the layout moved.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    sys.exit(compare(sys.argv[1], sys.argv[2]))
