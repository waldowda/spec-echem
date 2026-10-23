"""
probe_autolab_gitt_eis.py -- what would GITT and ER-EIS need from the Autolab?

Both are planned as siblings of the PITT staircase (see TODO.md). Before any of it is
built, this answers the instrument questions the plan rests on, on a DUMMY cell.

Part 1 (always; read-only, the cell is never switched on):
  * the SDK's FRA objects -- Instrument.Fra (a FraManualControlCommand) and
    GetFraModule() -- with every property and method, since no FRA call has been
    tried from Python yet
  * the Ei modes this SDK offers (galvanostatic is what GITT needs)
  * every standard NOVA procedure with FRA or impedance in its name: its commands,
    its FraCommands and their parameters. LoadProcedure does not energize anything.

Part 2, --gitt (galvanostatic, as GITT would run it, in Ei mode like the PITT):
  Ei.Mode = Galvanostatic with the cell still OPEN, then 0, +I, 0, -I, 0 A for 3 s
  each, the cell switched on once and off once. Does E follow I x R on the dummy,
  does the measured current match the setpoint, and what does Ei.Setpoint read back
  in this mode (its units in galvanostatic mode are unconfirmed)? A Python-side
  |E| limit stops it -- Ei mode has no instrument StopAt.

Part 3, --fra (impedance via the PROVEN procedure route):
  runs the first standard FRA procedure found (or --nox) exactly as NOVA ships it,
  with its own defaults, which part 1 lists first. Reports how long it took (the
  number an ER-EIS duration estimate needs), every recorded channel, and Z against
  the dummy's expected Z where the frequency and Z columns can be found.

    >> A DUMMY CELL ONLY, never a film. Give the dummy's values: --rs, --rp, --c
       for a Randles circuit, or --rs 0 --rp R --c 0 for a plain resistor. <<

    python examples/probe_autolab_gitt_eis.py                           # part 1 only
    python examples/probe_autolab_gitt_eis.py --energize --gitt --rp 10000 --rs 0 --c 0
    python examples/probe_autolab_gitt_eis.py --energize --fra

Writes examples/probe_autolab_gitt_eis_report.txt (+ _fra.csv after --fra).
"""
import argparse
import cmath
import glob
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import autolab_common as ac                                        # noqa: E402
from spec_echem import potentiostat                                # noqa: E402
from spec_echem.bench import load_bench_defaults                   # noqa: E402

PROCEDURES = os.path.join(os.path.dirname(ac.SDK), "Standard Nova Procedures")
LEVEL_S = 3.0
TICK_S = 0.1
MAX_PROCEDURES = 3


def dump_members(obj, label, max_items=200):
    """A .NET object's properties (with values where readable) and methods.
    Pure reflection plus property reads -- touches nothing."""
    ac.say("")
    ac.say("--- %s %s" % (label, "-" * max(0, 68 - len(label))))
    if obj is None:
        ac.say("  (not available)")
        return
    t = ac.safe(lambda: obj.GetType())
    if t is None:
        ac.say("  cannot reflect: %r" % (obj,))
        return
    ac.say("  .NET type: %s" % t.FullName)
    props = ac.safe(lambda: sorted({p.Name: p for p in t.GetProperties()}.items()), [])
    ac.say("  properties (%d):" % len(props))
    for name, p in props[:max_items]:
        rw = "rw" if p.CanRead and p.CanWrite else ("r " if p.CanRead else " w")
        ptype = ac.safe(lambda p=p: p.PropertyType.Name, "?")
        val = ac.safe(lambda n=name: getattr(obj, n), "<unreadable>") if p.CanRead else ""
        text = str(val)
        ac.say("    [%s] %s: %s = %s" % (rw, name, ptype, text[:80]))
    names = ac.safe(lambda: sorted({m.Name for m in t.GetMethods()
                                    if not m.Name.startswith(("get_", "set_", "add_",
                                                              "remove_"))}), [])
    ac.say("  methods (%d): %s" % (len(names), ", ".join(names[:max_items])))


def expected_z(f, rs, rp, c):
    if c <= 0:
        return complex(rs + rp, 0.0)
    w = 2 * math.pi * f
    return rs + rp / complex(1.0, w * rp * c)


def fra_procedures():
    found = []
    for path in sorted(glob.glob(os.path.join(PROCEDURES, "*.nox"))):
        name = os.path.basename(path).lower()
        if "fra" in name or "imped" in name:
            found.append(path)
    return found


def show_procedure(inst, path):
    proc = ac.safe(lambda: ac.load(inst, path))
    if proc is None:
        ac.say("  could not load %s" % os.path.basename(path))
        return None
    idnames = ac.list_commands(proc)
    for idn in idnames:
        cmd = ac.safe(lambda i=idn: proc.Commands[i])
        if cmd is not None and ("fra" in str(idn).lower() or "imp" in str(idn).lower()):
            ac.dump_parameters(cmd, str(idn))
    fras = ac.safe(lambda: proc.FraCommands)
    dump_members(fras, "%s .FraCommands" % os.path.basename(path), max_items=60)
    for k, fc in enumerate(ac.safe(lambda: list(fras), []) or []):
        dump_members(fc, "FraCommands[%d]" % k, max_items=80)
        ac.dump_parameters(fc, "FraCommands[%d]" % k)
    return proc


# --- part 1 -----------------------------------------------------------------

def part1(inst):
    ac.rule("PART 1 -- THE SDK'S FRA AND Ei MODES (read-only, cell never on)")
    dump_members(ac.safe(lambda: inst.Fra), "Instrument.Fra")
    dump_members(ac.safe(inst.GetFraModule), "Instrument.GetFraModule()")
    try:
        import clr
        from EcoChemie.Autolab.Sdk import EI
        from System import Enum
        modes = [str(n) for n in Enum.GetNames(clr.GetClrType(EI.EIMode))]
    except Exception as exc:  # noqa: BLE001
        modes = ["<could not list: %s>" % exc]
    ac.say("")
    ac.say("  Ei modes:          %s" % ", ".join(modes))
    ac.say("  Ei.Mode now:       %s" % ac.safe(lambda: inst.Ei.Mode))
    ac.say("  Ei.CurrentRange:   %s" % ac.safe(lambda: inst.Ei.CurrentRange))
    ac.say("")
    ac.say("  Standard procedures folder: %s" % PROCEDURES)
    found = fra_procedures()
    ac.say("  FRA / impedance procedures: %s"
           % (", ".join(os.path.basename(p) for p in found) or "NONE FOUND"))
    for path in found[:MAX_PROCEDURES]:
        ac.rule("PROCEDURE: %s" % os.path.basename(path))
        show_procedure(inst, path)
    return found


# --- part 2 -----------------------------------------------------------------

def read_ei(inst):
    if not potentiostat.sample_ei(inst):
        return float("nan"), float("nan")
    return (ac.safe(lambda: float(inst.Ei.Potential), float("nan")),
            ac.safe(lambda: float(inst.Ei.Current), float("nan")))


def part2(inst, settings, args):
    r_dc = args.rs + args.rp
    amps = args.amps if args.amps else 0.1 / r_dc
    e_expect = amps * r_dc
    ac.rule("PART 2 -- GALVANOSTATIC (GITT) in Ei mode on %.0f Ohm DC, I = %+.3e A"
            " -> %+.4f V expected" % (r_dc, amps, e_expect))
    if abs(e_expect) > 0.5 * args.e_limit:
        ac.say("  REFUSED: I x R = %.3f V is more than half the --e-limit %.3f V."
               % (e_expect, args.e_limit))
        return
    ei = inst.Ei
    rng = args.range or settings.get("autolab_current_range")
    rows = []
    tripped = False
    try:
        potentiostat._set_ei_mode(ei, potentiostatic=False)       # cell still OPEN
        ac.say("  Ei.Mode now:     %s" % ac.safe(lambda: ei.Mode))
        if rng:
            potentiostat._set_current_range(ei, rng)
        ac.say("  current range:   %s" % (ac.safe(lambda: ei.CurrentRange) or rng))
        ei.Setpoint = 0.0
        ac.say("  Setpoint 0 A read back: %s" % ac.safe(lambda: ei.Setpoint))
        ac.switch_cell(inst, True)
        levels = [0.0, amps, 0.0, -amps, 0.0]
        for k, lev in enumerate(levels):
            ei.Setpoint = float(lev)
            back = ac.safe(lambda: float(ei.Setpoint), float("nan"))
            t0 = time.perf_counter()
            nxt = t0
            while time.perf_counter() - t0 < LEVEL_S:
                e, i = read_ei(inst)
                rows.append((k, lev, back, time.perf_counter() - t0, e, i))
                if not math.isnan(e) and abs(e) > args.e_limit:
                    tripped = True
                    break
                nxt += TICK_S
                time.sleep(max(0.0, nxt - time.perf_counter()))
            if tripped:
                break
    except Exception as exc:  # noqa: BLE001
        ac.say("  *** STOPPED: %s: %s" % (type(exc).__name__, exc))
    finally:
        ac.cell_off_quietly(inst)
        ac.safe(lambda: potentiostat._set_ei_mode(ei, potentiostatic=True))
        ac.say("  back to potentiostatic.")
    if tripped:
        ac.say("  *** Python |E| guard tripped at %.3f V -- stopped. ***" % args.e_limit)
    ac.say("")
    ac.say("  level   I set (A)    Setpoint read back   I meas (A)    E meas (V)   E/(I x R)")
    for k in range(5):
        mine = [r for r in rows if r[0] == k and r[3] >= LEVEL_S / 2]
        if not mine:
            continue
        lev, back = mine[0][1], mine[0][2]
        e = sorted(r[4] for r in mine)[len(mine) // 2]
        i = sorted(r[5] for r in mine)[len(mine) // 2]
        ratio = ("%.3f" % (e / (lev * r_dc))) if lev else "  --"
        ac.say("  %5d   %+.3e   %+.6e        %+.3e    %+.5f     %s"
               % (k, lev, back, i, e, ratio))
    ac.say("  (settled half of each level; %d samples in all)" % len(rows))


# --- part 3 -----------------------------------------------------------------

def find_column(sig, *needles, exclude=None):
    for key in sig:
        low = key.lower()
        if all(n in low for n in needles) and not (exclude and exclude in low):
            return key
    return None


def part3(inst, args, found):
    path = args.nox or (found[0] if found else None)
    ac.rule("PART 3 -- IMPEDANCE THROUGH A STANDARD NOVA PROCEDURE, run as shipped")
    if not path:
        ac.say("  no FRA procedure found; give one with --nox.")
        return
    proc = show_procedure(inst, path)
    if proc is None:
        return
    ac.say("")
    ac.say("  *** Running %s with ITS OWN defaults on the dummy. ***" % os.path.basename(path))
    try:
        elapsed = ac.run(proc, inst, poll=0.5, timeout=args.fra_timeout)
    finally:
        ac.cell_off_quietly(inst)
    ac.say("  -> %.0f s for the whole sweep" % elapsed)

    sig = {}
    for idn in ac.safe(lambda: list(proc.Commands.IdNames), []) or []:
        cmd = ac.safe(lambda i=idn: proc.Commands[i])
        for key, vals in (ac.read_signals(cmd) if cmd is not None else {}).items():
            sig["%s.%s" % (idn, key)] = vals
    for k, fc in enumerate(ac.safe(lambda: list(proc.FraCommands), []) or []):
        for key, vals in ac.read_signals(fc).items():
            sig["Fra[%d].%s" % (k, key)] = vals
    ac.report_signals(sig)
    ac.write_csv(sig, os.path.join(HERE, "probe_autolab_gitt_eis_fra.csv"))

    f_key = find_column(sig, "freq")
    re_key = find_column(sig, "z'", exclude="z''") or find_column(sig, "zreal") or find_column(sig, "real")
    im_key = (find_column(sig, "-z''") or find_column(sig, "z''")
              or find_column(sig, "zimag") or find_column(sig, "imag"))
    ac.say("")
    ac.say("  columns used: f=%s  Z'=%s  Z''=%s" % (f_key, re_key, im_key))
    if not (f_key and re_key and im_key):
        ac.say("  could not identify all three; compare by hand from the CSV.")
        return
    neg = im_key.lower().count("-z") > 0
    ac.say("      f (Hz)     Z' meas    Z'' meas    |Z| expected   |Z| err   phase err")
    for f, zr, zi in zip(sig[f_key], sig[re_key], sig[im_key]):
        z = complex(zr, -zi if neg else zi)
        ze = expected_z(f, args.rs, args.rp, args.c)
        ac.say("    %9.2f  %9.1f  %9.1f     %9.1f     %+6.1f%%   %+6.1f deg"
               % (f, z.real, z.imag, abs(ze), (abs(z) - abs(ze)) / abs(ze) * 100,
                  math.degrees(cmath.phase(z) - cmath.phase(ze))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--energize", action="store_true",
                    help="allow parts 2/3 to switch the cell on (DUMMY CELL ONLY)")
    ap.add_argument("--gitt", action="store_true", help="part 2: galvanostatic pulses")
    ap.add_argument("--fra", action="store_true", help="part 3: one impedance sweep")
    ap.add_argument("--rs", type=float, default=200.0, help="dummy series R (Ohm)")
    ap.add_argument("--rp", type=float, default=3010.0, help="dummy parallel R (Ohm)")
    ap.add_argument("--c", type=float, default=1e-6, help="dummy parallel C (F); 0 = none")
    ap.add_argument("--amps", type=float, default=0.0,
                    help="GITT pulse current (A); default gives I x R = 0.1 V")
    ap.add_argument("--e-limit", type=float, default=0.5,
                    help="|E| at which part 2 stops (V)")
    ap.add_argument("--range", default=None,
                    help="current range for part 2, e.g. CR11_100uA (else bench.ini)")
    ap.add_argument("--nox", default=None, help="the FRA procedure for part 3")
    ap.add_argument("--fra-timeout", type=float, default=1800.0,
                    help="seconds before part 3's sweep is aborted")
    args = ap.parse_args()

    ac.rule("GITT / ER-EIS ON THE AUTOLAB -- what the plan needs to know")
    if (args.gitt or args.fra) and not args.energize:
        ac.say("Parts 2/3 need --energize, with a DUMMY cell in. Running part 1 only.")
    settings, warnings = load_bench_defaults()
    for w in warnings:
        ac.say("  bench.ini: %s" % w)
    inst = ac.connect()
    if inst is None:
        ac.write_transcript(os.path.join(HERE, "probe_autolab_gitt_eis_report.txt"))
        return 1
    try:
        found = part1(inst)
        if args.energize and args.gitt:
            ac.say("\n*** Dummy only: %.0f + (%.0f || %.2g F). ***"
                   % (args.rs, args.rp, args.c))
            part2(inst, settings, args)
        if args.energize and args.fra:
            part3(inst, args, found)
    except Exception as exc:  # noqa: BLE001
        ac.say("\n*** STOPPED: %s: %s" % (type(exc).__name__, exc))
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)
    ac.say("\nSend this report back.")
    ac.write_transcript(os.path.join(HERE, "probe_autolab_gitt_eis_report.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
