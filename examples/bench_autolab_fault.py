"""
bench_autolab_fault.py — what does a FAILED Autolab run look like?

Closes open items 4 and 7 of docs/autolab-run-api.md §4.

The premise, which is worth stating because it changes what the driver has to do:
**the dangerous fault is probably not one the SDK reports.** An open cell or a
current overload doesn't make Measure() fail — the instrument does exactly what it
was told, IsMeasuring goes False like any successful run, and .Signals fills with
meaningless numbers. If nothing samples the overload flags DURING the run, that
segment is written to disk looking perfect.

That is the same shape as the Gamry bug reproduced on 2026-07-27: a truncated echem
file written silently beside complete spectra, with nothing saying so.

So this script builds four fingerprints and compares them:

    BASELINE     a clean run on the dummy resistor
    OVERLOAD     current range set too small for the current being drawn
                 (software only — no hardware risk, and a guaranteed fault)
    OPEN CIRCUIT you open the cell mid-run (the cell switch, or unclip a lead) —
                 the most realistic failure, and exactly what a loose connector did
                 on 2026-08-31
    USB PULL     you pull the USB mid-run — this is item 7, device_lost(), and a
                 different question from the other three

For each: does IsMeasuring go False, how many points land, do PotentialOverload /
CurrentOverload ever read True, does IsConnected stay True, and does any
Procedure-level status differ. Whatever distinguishes them is what the driver checks.

    >> 10 kOhm dummy resistor, never a real sample. <<

Order matters. The USB pull runs LAST: it leaves the cell energized with no software
control (harmless on a resistor, never on a sample) and recovery may need a
reconnect or a power cycle.

Usage:
    python bench_autolab_fault.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab_common as ac      # noqa: E402
from autolab_common import say, rule, safe   # noqa: E402

# --- what to run -----------------------------------------------------------
NOX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox"

ENERGIZE_CELL = False     # False = phase 0 only (reflection; nothing energized)
RUN_BASELINE = True
RUN_OVERLOAD = False      # dropped 2026-09-03: stock Cyclic voltammetry.nox is
                         # self-protecting — command [1] FHGetSetValues sets
                         # CurrentRange=CR10_1mA and command [5] FHPreCurrentRangingCV
                         # auto-ranges, so a pre-set small range never reaches the
                         # staircase. A software range error is not a reachable fault
                         # mode for this procedure. (see docs/autolab-run-api.md §4.4)
RUN_OPEN_CIRCUIT = True   # item 4, physical — prompts you. Answered 2026-09-03.
RUN_USB_PULL = False      # item 7 — DO NOT re-run casually. On 2026-09-03 a mid-run
                         # USB pull wedged the SDK: proc.IsMeasuring stuck True, the
                         # Adk.x process flooded the console with uncatchable native
                         # errors, reconnecting did not recover, Ctrl-C required, box
                         # power-cycled. Finding recorded in docs/autolab-run-api.md §4.7.

# Only used by RUN_OVERLOAD (now off) and as a pre-set before RUN_OPEN_CIRCUIT.
# Left None so the open-circuit run uses the procedure's normal auto-range and the
# only variable is the open lead.
CURRENT_RANGE_NAME = None   # exact spelling would come from phase 0

# CV staircase settings (same indices as bench_autolab_cv.py).
CV_ID = "FHCyclicVoltammetry2"
IDX_START, IDX_UPPER, IDX_LOWER = 0, 1, 2
IDX_STEP, IDX_CROSSINGS, IDX_SCANRATE = 3, 4, 6
START_V, UPPER_V, LOWER_V = 0.0, 1.0, -1.0
STEP_V, SCAN_RATE_VS, CROSSINGS = 0.00244, 0.5, 2   # 0.5 V/s: short runs, less waiting

HERE = os.path.dirname(os.path.abspath(__file__))
_results = {}


def enum_names(clr, type_name):
    """Member names of a nested SDK enum, e.g. EI.EICurrentRange."""
    try:
        from EcoChemie.Autolab.Sdk import EI
        from System import Enum
        t = clr.GetClrType(getattr(EI, type_name))
        return list(Enum.GetNames(t))
    except Exception as exc:  # noqa: BLE001
        say(f"  could not read {type_name} members: {exc}")
        return []


def set_current_range(inst, name):
    """Assign the nested enum member, exactly as CellOnOff needs."""
    from EcoChemie.Autolab.Sdk import EI
    inst.Ei.CurrentRange = getattr(EI.EICurrentRange, name)
    return safe(lambda: str(inst.Ei.CurrentRange))


def make_watcher(record):
    """Sample the overload flags every poll — the only way an overloaded run is
    distinguishable from a clean one, since both finish normally.

    Seed the observables to False so the comparison table reads unambiguously: a
    blank means "never sampled", False means "sampled, never True".

    For item 7 the distinction that matters is HOW a lost link shows up —
    `IsConnected` returning False, or the call throwing on a dead handle. The driver's
    device_lost() has to handle whichever it is, so record both.
    """
    record.setdefault("potential_overload", False)
    record.setdefault("current_overload", False)
    record.setdefault("disconnected_during", False)
    record.setdefault("isconnected_threw", False)

    def watch(inst, proc, elapsed):
        if safe(lambda: bool(inst.Ei.PotentialOverload), False):
            record["potential_overload"] = True
        if safe(lambda: bool(inst.Ei.CurrentOverload), False):
            record["current_overload"] = True
        try:
            if bool(inst.AutolabConnection.IsConnected) is False:
                record["disconnected_during"] = True
        except Exception:  # noqa: BLE001 — a throw IS the disconnect signal here
            record["isconnected_threw"] = True
            record["disconnected_during"] = True
        record["polls"] = record.get("polls", 0) + 1
    return watch


def fresh_procedure(inst):
    proc = ac.load(inst, NOX)
    cv, _ = ac.command(proc, CV_ID, "CV staircase")
    if cv is None:
        return None, None
    ac.set_param(cv, IDX_START, START_V, "start V")
    ac.set_param(cv, IDX_UPPER, UPPER_V, "upper V")
    ac.set_param(cv, IDX_LOWER, LOWER_V, "lower V")
    ac.set_param(cv, IDX_STEP, STEP_V, "step V")
    ac.set_param(cv, IDX_CROSSINGS, CROSSINGS, "crossings")
    ac.set_param(cv, IDX_SCANRATE, SCAN_RATE_VS, "scan rate V/s")
    return proc, cv


def fingerprint(inst, label, proc, cv, record):
    """Everything observable after a run. The comparison table is the deliverable."""
    sig = ac.read_signals(cv)
    cur = sig.get("EI_0.CalcCurrent", [])
    record.update({
        "points": len(cur),
        "is_measuring_after": safe(lambda: proc.IsMeasuring),
        "connected_after": safe(lambda: bool(inst.AutolabConnection.IsConnected)),
        "potential_overload_after": safe(lambda: bool(inst.Ei.PotentialOverload)),
        "current_overload_after": safe(lambda: bool(inst.Ei.CurrentOverload)),
        "max_abs_current": max((abs(c) for c in cur), default=None),
    })
    _results[label] = record
    say("")
    say(f"  fingerprint [{label}]:")
    for k, v in record.items():
        say(f"    {k:<26} {v}")
    ac.write_csv(sig, os.path.join(HERE, f"bench_autolab_fault_{label}.csv"))
    return record


def ask(prompt):
    """Bench scripts are run by a human standing at the instrument."""
    say("")
    say(f"  >>> {prompt}")
    try:
        input("      press Enter when done... ")
    except (EOFError, KeyboardInterrupt):
        say("      (no console — continuing)")


def one_run(inst, label, before=None, during=None):
    proc, cv = fresh_procedure(inst)
    if proc is None:
        say(f"  {label}: could not load the CV command — skipped.")
        return None
    record = {}
    if before is not None:
        before()
    ac.switch_cell(inst, True)
    if during is not None:
        during()
    ac.run(proc, inst, watch=make_watcher(record))
    ac.switch_cell(inst, False)
    return fingerprint(inst, label, proc, cv, record)


def main():
    rule("AUTOLAB — WHAT DOES A FAILED RUN LOOK LIKE? (items 4 and 7)")
    say(f"NOX           : {NOX}")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    say(f"current range : {CURRENT_RANGE_NAME}")
    if ENERGIZE_CELL:
        say("")
        say("*** The cell WILL be energized. 10 kOhm dummy resistor only. ***")

    inst = ac.connect()
    if inst is None:
        say("")
        say("Stopped: no connection. Nothing was energized.")
        return 0

    try:
        # --- phase 0: what can even be observed? (safe) -------------------
        rule("PHASE 0 — observable state and the current-range members")
        import clr  # already loaded by connect()
        names = enum_names(clr, "EICurrentRange")
        say(f"  EI.EICurrentRange members: {names}")
        say("  Put a SMALL one in CURRENT_RANGE_NAME — small enough that ~100 uA")
        say("  through the 10 kOhm overloads it.")

        proc, cv = fresh_procedure(inst)
        if proc is not None:
            ac.dump_parameters(cv, CV_ID)
            say("")
            say("  Procedure-level state worth watching for a status field:")
            for attr in ("IsMeasuring", "Status", "State", "Result", "IsFinished",
                         "Aborted", "HasError", "Error"):
                say(f"    Procedure.{attr:<12} = "
                    f"{safe(lambda a=attr: getattr(proc, a), '<absent>')}")
        say("")
        say("  Ei flags: "
            f"PotentialOverload={safe(lambda: inst.Ei.PotentialOverload)} "
            f"CurrentOverload={safe(lambda: inst.Ei.CurrentOverload)} "
            f"Cell={safe(lambda: inst.Ei.Cell)}")

        if not ENERGIZE_CELL:
            say("")
            say("ENERGIZE_CELL is False — no runs. Set CURRENT_RANGE_NAME from the list")
            say("above, put the 10 kOhm in, and re-run with it True.")
            return 0

        # --- 1. baseline ---------------------------------------------------
        if RUN_BASELINE:
            rule("RUN 1 — BASELINE (clean run on the dummy resistor)")
            one_run(inst, "baseline")

        # --- 2. overload (software) ----------------------------------------
        if RUN_OVERLOAD:
            rule("RUN 2 — OVERLOAD (current range set too small)")
            if not CURRENT_RANGE_NAME:
                say("  CURRENT_RANGE_NAME is None — skipped. Pick one from phase 0.")
            else:
                def shrink():
                    got = set_current_range(inst, CURRENT_RANGE_NAME)
                    say(f"  current range set to {CURRENT_RANGE_NAME} (reads {got})")
                one_run(inst, "overload", before=shrink)
                say("")
                say("  If this finished with IsMeasuring False and a full point count,")
                say("  then an overloaded run is INDISTINGUISHABLE from a good one except")
                say("  by the flags — which is exactly why the driver must poll them.")

        # --- 3. open circuit (physical) -------------------------------------
        if RUN_OPEN_CIRCUIT:
            rule("RUN 3 — OPEN CIRCUIT (the realistic failure)")
            if CURRENT_RANGE_NAME:
                safe(lambda: set_current_range(inst, CURRENT_RANGE_NAME))
            ask("OPEN the cell now — the cell switch, or unclip ONE lead. "
                "Leave the resistor otherwise wired.")
            one_run(inst, "open_circuit")
            ask("Re-CLOSE the cell (reseat the lead) before continuing.")
            say("  A near-zero max_abs_current above is the signature of an open cell:")
            say("  a complete, successful-looking run carrying no electrochemistry.")

        # --- 4. usb pull (physical, last) -----------------------------------
        if RUN_USB_PULL:
            rule("RUN 4 — USB PULL (item 7: device_lost)")
            say("  This leaves the cell energized with no software control. Dummy")
            say("  resistor only. Recovery may need a reconnect or a power cycle.")
            proc, cv = fresh_procedure(inst)
            if proc is not None:
                record = {}
                ac.switch_cell(inst, True)
                ask("Start the run, then PULL THE USB CABLE partway through. "
                    "Press Enter FIRST, then pull.")
                ac.run(proc, inst, timeout=120.0, watch=make_watcher(record))
                fingerprint(inst, "usb_pull", proc, cv, record)
                say("")
                say("  The question: did IsConnected flip to False DURING the run")
                say("  (disconnected_during above)? That is what device_lost() reads.")
                ask("Reconnect the USB cable (and power-cycle if needed).")

        # --- the comparison -------------------------------------------------
        rule("COMPARISON — what distinguishes a failed run from a good one?")
        keys = ["points", "is_measuring_after", "connected_after",
                "potential_overload", "current_overload", "disconnected_during",
                "isconnected_threw", "max_abs_current"]
        say(f"  {'observable':<26} " + "".join(f"{k:<16}" for k in _results))
        for k in keys:
            row = "".join(f"{str(_results[lbl].get(k, '-')):<16}" for lbl in _results)
            say(f"  {k:<26} {row}")
        say("")
        say("  Any row that differs between baseline and a fault is something the")
        say("  driver can check. If ONLY the overload flags differ, then polling them")
        say("  during the run is the whole of fault detection — and a segment that")
        say("  overloads must be reported, not written as if it were fine.")
    finally:
        ac.cell_off_quietly(inst)
        ac.disconnect(inst)

    rule("NEXT")
    say("Record the comparison table in docs/autolab-run-api.md §4 items 4 and 7.")
    return 0


if __name__ == "__main__":
    code = main()
    ac.write_transcript(os.path.join(HERE, "bench_autolab_fault_report.txt"))
    sys.exit(code)
