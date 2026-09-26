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

**External mode never had this problem**, because Gamry Framework sets a range rather
than leaving the instrument on its power-up one.

### A retraction, recorded because it nearly set the default

The 20260616 External `.DTA` files show the range walking 8→1 over the first points at
10 points/s, and that was briefly taken as evidence that Gamry auto-ranging is safe at
our sampling rate. **It is not evidence: those files are not valid measurements.** Across
all 171 points in four files, ZERO are free of overload bits, `Vf` sits at −2.0 to
−2.6 V against a −0.5 to +0.7 V window, and the currents are picoamps. That is an open
cell, and the range walking down to 60 pA is auto-ranging finding nothing to measure.

So there is no good evidence either way, and Gamry's own documentation says auto-ranging
is not recommended above 1 point/s with default filter settings — we sample at 10. **A
deliberate fixed range is therefore the default**, which also makes the Gamry consistent
with the standing Autolab decision instead of contradicting it.

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

`gamry_current_range`, default **6 mA**:

- **A number** — the largest current in amperes the segment should draw. The driver asks
  the instrument to map it (`test_ie_range`) rather than assuming the ladder, then pins
  it with `set_ie_range`. 6 mA is the shipped default because it clips nothing observed
  on these rigs (peaks reach 742 µA) while being ~100× finer than the 600 mA that was
  silently in use. It is deliberately not the *best* range for a given film — the
  advisory below names that.
- **`"auto"`** is accepted, offered last in the dropdown and marked as not recommended
  at 10 points/s.

**Every segment reports what it saw**, mirroring the Autolab's `_advise_current_range`:
the peak current, what fraction of full scale it used, and a finer range if one would fit
with headroom ("peak 2.4e-05 A used 0.40% of the 0.006 A full scale. 6e-05 A would fit
… ~100x finer resolution"). One test run therefore tells you what the sample wants,
which is the range-finding procedure made self-interpreting rather than a habit to
remember.

**An overload warns; it never stops a run and never discards a point.** Requested
2026-09-25: a Gamry that overloads a little still returns usable numbers, so the concern
is raised beside the data and the scientist decides. The warning names the count
("N of M points flagged OVERLOAD") and says the data is kept. The overload field's name
in `acq_data()` is undocumented, so the check tries several and stays silent if none is
present — the `.dta` carries the `Over` column regardless.

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
