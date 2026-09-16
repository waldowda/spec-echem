"""
probe_zero_offset.py -- the current-range zero offset, measured across the ranges.

MEASURED so far, in passing rather than on purpose:

    CR09_10mA   +1.605 uA   (2026-09-11, the film session)
    CR11_100uA    ~+22 nA   (2026-09-16, probe_overload's cell-off baselines)

Both are ~0.02% of that range's full scale, across ranges 100x apart, which suggests
the offset is PROPORTIONAL to the range rather than fixed. Two points do not establish
a rule, and the reason it matters is quantitative: the settled currents in the
2026-09-11 film runs were 0.34-33.5 uA against a +1.6 uA offset, so the offset was
10-100% of the measurement.

This walks the ranges deliberately and reports the offset as an absolute current AND
as a fraction of full scale, which is the form that says whether the proportionality
holds.

Two conditions per range, because they answer different questions:

  CELL OFF   the current amplifier's own zero, nothing connected to the cell.
  CELL ON at 0.000 V   the whole measurement path, with the potentiostat actively
             holding zero across the dummy. True current is 0 either way, so any
             difference between the two is the cell-control path rather than the
             amplifier.

It also enumerates whatever the SDK exposes under calibration, because the offsets
might be a calibration question rather than something to subtract. That part reads
only -- it runs no calibration and changes no instrument state.

    >> 10 kOhm dummy resistor, never a real sample. <<
    W + WS on one leg, RE + CE on the other (2-electrode).

Usage:
    python probe_zero_offset.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Coarse to fine. Full scale in amps, parsed from the member name elsewhere in the
# project; written out here so this script stays standalone.
RANGES = [
    ("CR09_10mA", 1.0e-2),
    ("CR10_1mA", 1.0e-3),
    ("CR11_100uA", 1.0e-4),
    ("CR12_10uA", 1.0e-5),
    ("CR13_1uA", 1.0e-6),
    ("CR14_100nA", 1.0e-7),
]
SAMPLES = 25
INTERVAL_S = 0.05


def mean_sd(values):
    n = len(values)
    if not n:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, var ** 0.5


def sample_zero(inst, range_name, cell_on):
    """Mean current at a 0.000 V setpoint, where the true current is zero."""
    from EcoChemie.Autolab.Sdk import EI
    inst.Ei.Mode = EI.EIMode.Potentiostatic
    inst.Ei.CurrentRange = getattr(EI.EICurrentRange, range_name)
    inst.Ei.Setpoint = 0.0
    if cell_on:
        ac.switch_cell(inst, True)
        time.sleep(0.3)             # let the control loop settle before sampling
    currents, potentials, flagged = [], [], 0
    for _ in range(SAMPLES):
        try:
            inst.Ei.Sampler.Sample()
        except Exception:  # noqa: BLE001
            pass
        currents.append(float(inst.Ei.Current))
        potentials.append(float(inst.Ei.Potential))
        if bool(inst.Ei.CurrentOverload) or bool(inst.Ei.PotentialOverload):
            flagged += 1
        time.sleep(INTERVAL_S)
    if cell_on:
        ac.switch_cell(inst, False)
    return currents, potentials, flagged


def report_calibration_api(inst):
    """What does the SDK expose under calibration? READ ONLY -- runs nothing."""
    rule("What the SDK exposes under calibration (read only, runs nothing)")
    say("On a Gamry the potentiostat and cables are calibrated routinely. If these")
    say("offsets are a calibration artifact, subtracting them treats a symptom.")
    say("")
    hits = []
    for holder, label in ((inst, "Instrument"), (getattr(inst, "Ei", None), "Ei"),
                          (getattr(inst, "AutolabConnection", None), "AutolabConnection")):
        if holder is None:
            continue
        try:
            names = sorted(dir(holder))
        except Exception as exc:  # noqa: BLE001
            say("  %s: could not enumerate (%s)" % (label, exc))
            continue
        for name in names:
            low = name.lower()
            if any(k in low for k in ("calib", "offset", "gain", "zero", "trim",
                                      "adjust", "diagnos")):
                try:
                    value = getattr(holder, name)
                    shown = "<callable>" if callable(value) else repr(value)[:60]
                except Exception as exc:  # noqa: BLE001
                    shown = "<unreadable: %s>" % exc
                hits.append("  %-20s %-34s %s" % (label, name, shown))
    if hits:
        for line in hits:
            say(line)
    else:
        say("  Nothing matching calib/offset/gain/zero/trim/adjust/diagnos on")
        say("  Instrument, Ei or AutolabConnection.")
    say("")
    say("  Absence here is NOT evidence the instrument cannot be calibrated -- NOVA")
    say("  may own that, and the SDK expose only measurement. It does mean spec-echem")
    say("  cannot trigger or verify a calibration itself.")


def main():
    rule("probe_zero_offset -- offset vs current range, 10 kOhm dummy at 0.000 V")

    inst = ac.connect()
    if inst is None:
        return
    try:
        ac.cell_off_quietly(inst)
        report_calibration_api(inst)

        rule("Zero offset by range")
        say("  True current is 0 at a 0.000 V setpoint, so whatever is read IS the")
        say("  offset. 'as %% FS' is the number that says whether it scales.")
        say("")
        say("  %-12s %10s   %-26s %-26s" % ("range", "full scale",
                                            "CELL OFF  mean +- sd", "CELL ON   mean +- sd"))
        rows = []
        for name, full_scale in RANGES:
            try:
                off_c, _off_p, off_flag = sample_zero(inst, name, cell_on=False)
                on_c, on_p, on_flag = sample_zero(inst, name, cell_on=True)
            except Exception as exc:  # noqa: BLE001
                say("  %-12s  FAILED: %s" % (name, exc))
                continue
            off_mean, off_sd = mean_sd(off_c)
            on_mean, on_sd = mean_sd(on_c)
            rows.append((name, full_scale, off_mean, off_sd, on_mean, on_sd,
                         off_flag, on_flag, mean_sd(on_p)[0]))
            say("  %-12s %10.3g   %11.4g +- %-10.3g %11.4g +- %-10.3g"
                % (name, full_scale, off_mean, off_sd, on_mean, on_sd))

        rule("Does the offset scale with the range?")
        say("  %-12s %12s %12s %12s %12s"
            % ("range", "off (A)", "off %FS", "on (A)", "on %FS"))
        for (name, full_scale, off_mean, _osd, on_mean, _nsd, _of, _nf, _p) in rows:
            say("  %-12s %12.4g %11.4f%% %12.4g %11.4f%%"
                % (name, off_mean, off_mean / full_scale * 100.0,
                   on_mean, on_mean / full_scale * 100.0))
        say("")
        say("  A constant %FS column means the offset is proportional to the range,")
        say("  and can be predicted for a range nobody has measured. A column that")
        say("  drifts means it cannot, and each range needs measuring on its own.")

        rule("Cell control at zero")
        say("  %-12s %14s %14s" % ("range", "held E (V)", "flags on/off"))
        for (name, _fs, _om, _osd, _nm, _nsd, off_flag, on_flag, held) in rows:
            say("  %-12s %14.6f %9d / %-4d" % (name, held, on_flag, off_flag))
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
        ac.write_transcript(os.path.join(HERE, "probe_zero_offset_report.txt"))


if __name__ == "__main__":
    main()
