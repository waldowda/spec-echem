# The Gamry ran every Python-mode segment on its 600 mA range

**Measured 2026-09-25 on a Reference 600 (serial 08083) with a 10 kΩ dummy resistor, then
confirmed against two earlier film runs.** Fixed the same day; this page is the evidence
and what it means for data already taken.

## The symptom

A 10 kΩ dummy CV looked "very noisy", and reseating the clips changed nothing.

| | 20260925_test1 | 20260925_test4 |
|---|---|---|
| Fitted R | 9,700 Ω | 10,348 Ω |
| Current at 0.000 V | **+37.4 µA** | **+29.2 µA** |
| Residual noise (sd) | **2,879 nA** | 2,542 nA |
| Potential control (sd) | 0.15 mV | 0.15 mV |

The resistance and the potential control were fine — the holds agree independently
(+0.200 V drew 18.6 µA more than 0.000 V, i.e. 10.75 kΩ). What was wrong was the
**intercept**: at 0.000 V the instrument reported ~35 µA, which should be zero, and it
reproduced across two runs fifteen minutes apart.

## The cause

Every point of every segment recorded `IERange = 11`. From toolkitpy's own table
(`Help/hardwarespecificsettings_ierange.html`), for the Reference 600/620 family
**IERange 11 = 600 mA**. So ±50 µA was being measured on a 600 mA scale — four decades
too coarse. The offset is then 5.8×10⁻⁵ of full scale and the noise 4.3×10⁻⁶ of full
scale, which is ordinary instrument behavior for a range that size. **No overload bit
ever set**, because nothing was overloaded; the range was merely far too coarse.

`initialize_pstat()` set nine hardware parameters and never touched the I/E range, so
every Python-mode run inherited whatever the instrument powered up on.

**External mode never had this problem.** Gamry Framework auto-ranges: its `.DTA` files
from this rig show the range walking 8→1 across the first points and then settling — at
the **same 10 points/s** the Python path uses. (Gamry's documentation cautions against
auto-ranging above 1 point/s; the External baseline on this rig contradicts that in
practice, which is why "auto" is the default rather than a fixed range.)

## What it cost, in data already taken

| Date | Mode | IERange | Settled current | Point-to-point noise |
|---|---|---|---|---|
| 20260709 | Python | 11 on all 3,021 points | −5.4 µA / +1.0 µA | **1,733 / 1,953 nA** |
| 20260710 | Python | 11 on all 291 points | +2.6 µA | **1,415 nA** |
| 20260616 | External | 8→1, settles | — | — |
| 20260925 | Python | 11 on all points | — | 2,542-2,879 nA |

**The noise is 40-200% of the signal** in those film runs. Their settled currents are not
quantitatively trustworthy. Nothing in the files announces it — the only trace is the
`IERange` column of the `.dta`, which is why the fix logs the range every segment.

This is the same conclusion the Autolab reached on 2026-09-11 with `CR09_10mA`, for the
same reason, on the other rig. **The zero offset scales with the range** on both
instruments; it is an attribute of the hardware, not something to correct for in code.
The answer is to match the range to the currents a real film draws.

## The fix

`gamry_current_range`, default `"auto"`:

- **`"auto"`** calls `set_ie_range_mode(True)` — what External mode has always done.
- **A number** is the largest current in amperes the segment should draw. The driver asks
  the instrument to map it (`test_ie_range`) rather than assuming the ladder, then pins
  it with `set_ie_range` and turns auto-ranging off. For a run that should not have the
  range changing inside its transient.

A dropdown on the Parameters tab offers Auto plus the full ladder in absolute currents,
marking anything above 10 mA as high current — the same treatment the Autolab's range
control gets, and for the same reason: an oversized range coarsens every reading and
removes the overload protection that would otherwise stop a fault damaging the sample.

**Every segment now logs the range it used**, because this was wrong for months precisely
because nothing ever said what it was.

## Checking a past run

```
grep -A2 CURVE <run>/dta/steps\(0\).dta | head -3     # column order
```
`IERange` is the 8th column of the `CURVE` table. `11` means 600 mA.
