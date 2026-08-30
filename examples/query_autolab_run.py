"""
query_autolab_run.py — Can Python RUN electrochemistry on the Autolab?

The third Autolab probe, and the one that sizes the driver work:

    query_autolab.py          can we connect?                    (answered: yes)
    query_avantes_trigger.py  can we fire the Avantes trigger?    (answered: yes)
    query_autolab_run.py      can we run CV / a hold, and read    <- THIS ONE
                              the data back?

`spec_echem/potentiostat.py` defines a nine-method contract (open, prepare, fire,
finish, stop, pump, last_data, live_data, close). `fire()` is proven — the trigger
probe pulses DIO P1.A. Everything else is unknown, because nothing has yet called
`Ei`, `LoadProcedure`, `Measure` or `Sampler`. This script answers, in one sitting:

  Q1  Does LoadProcedure(.nox) + Measure() work, and does Measure() BLOCK?
      -> decides the threading model.
  Q2  Can a loaded procedure's parameters be OVERRIDDEN from Python?
      -> decides everything. The doping potential increments every cycle
         (potentiostat._chrono_potential), so if a .nox can't be parameterized,
         twelve cycles means twelve procedure files, or no procedure route at all.
  Q3  Can `Ei` hold a potential directly, with no .nox?
      -> if yes, chrono needs no procedure and only CV does.
  Q4  What does the data look like coming back (names, lengths, dtypes)?
      -> must map onto data.EchemData(time, potential, current).
  Q5  Is data readable DURING a run, or only after?   -> live_data()/pump()
  Q6  Can a running measurement be aborted?            -> stop()
  Q7  Is there a liveness check usable mid-run?        -> device_lost()

Most of this is REFLECTION — listing what the SDK actually exposes — which touches
nothing. The parts that energize the cell are behind ENERGIZE_CELL, off by default.

**Everything is written to a report file** (REPORT_PATH, default
`autolab_api_report.txt` beside this script) so the findings can travel back through
git instead of being retyped.

Needs the Autolab SDK + `pip install pythonnet`; see query_autolab_setup.md. Does not
import the spec-echem package.

Usage:
    python query_autolab_run.py
"""
import datetime
import os
import struct
import sys
import time

# ---------------------------------------------------------------------------
# Same three paths as query_autolab.py — copy whatever worked there.
# ---------------------------------------------------------------------------
SDK = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\EcoChemie.Autolab.Sdk"
ADX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Hardware Setup Files\Adk.x"
HDW = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml"

# A NOVA-built procedure to inspect (and optionally run). A chronoamperometry or CV
# procedure is ideal. Leave "" to skip every procedure question (Q1, Q2).
NOX = r""

# ---------------------------------------------------------------------------
# CELL SAFETY. With ENERGIZE_CELL = False (the default) this script only connects
# and REFLECTS — it never switches the cell on, never runs a procedure, and never
# applies a potential. Flip it to True only with a DUMMY CELL or test resistor in
# place, never a real sample: the first run of new instrument-control code is not
# where you want a real electrode.
# ---------------------------------------------------------------------------
ENERGIZE_CELL = False
TEST_POTENTIAL = 0.0      # volts, for the direct-Ei hold. 0.0 V is the gentlest probe.
HOLD_SECONDS = 3.0        # duration of the direct-Ei hold
POLL_SECONDS = 0.2        # how often to look for data mid-run (Q5)

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "autolab_api_report.txt")

_lines = []


def say(text=""):
    """Print and record. The report file is the deliverable."""
    print(text)
    _lines.append(text)


def rule(title):
    say("")
    say("=" * 72)
    say(title)
    say("=" * 72)


def _safe(fn, default="<unavailable>"):
    try:
        return fn()
    except Exception as exc:   # noqa: BLE001 — exploring an API we don't know yet
        return f"<error: {exc}>" if default is None else default


def dump_members(obj, label, max_items=200):
    """List a .NET object's properties and methods. Pure reflection — touches nothing.

    This is the point of the script: we are writing a driver against an API we have
    only read about, and guessing at method names is how the last two days went.
    """
    say("")
    say(f"--- {label} " + "-" * max(0, 68 - len(label)))
    if obj is None:
        say("  (not available)")
        return
    try:
        t = obj.GetType()
    except Exception as exc:  # noqa: BLE001
        say(f"  cannot reflect: {exc}")
        return
    say(f"  .NET type: {t.FullName}")

    try:
        props = sorted({p.Name: p for p in t.GetProperties()}.items())
        say(f"  properties ({len(props)}):")
        for name, p in props[:max_items]:
            ptype = _safe(lambda p=p: p.PropertyType.Name, None)
            rw = ("rw" if p.CanRead and p.CanWrite else "r " if p.CanRead else " w")
            say(f"    [{rw}] {name}: {ptype}")
    except Exception as exc:  # noqa: BLE001
        say(f"  properties: <error: {exc}>")

    try:
        names = sorted({m.Name for m in t.GetMethods()
                        if not m.Name.startswith(("get_", "set_", "add_", "remove_"))})
        say(f"  methods ({len(names)}): {', '.join(names[:max_items])}")
    except Exception as exc:  # noqa: BLE001
        say(f"  methods: <error: {exc}>")


def connect():
    """Open the instrument. Cell is NOT switched on here."""
    try:
        import clr  # pythonnet
    except Exception as exc:  # noqa: BLE001
        say(f"Could not import 'clr' (pythonnet): {exc}")
        say("Install with:  pip install pythonnet   — see query_autolab_setup.md")
        return None, None

    sdk_dir = os.path.dirname(SDK)
    if sdk_dir and sdk_dir not in sys.path:
        sys.path.append(sdk_dir)
    if not clr.FindAssembly(SDK):
        say(f"Cannot find the SDK assembly at:\n  {SDK}")
        say("Fix SDK at the top of this script (no .dll extension).")
        return None, None
    try:
        clr.AddReference(SDK)
        from EcoChemie.Autolab.Sdk import Instrument
    except Exception as exc:  # noqa: BLE001
        say(f"Found the SDK assembly but could not load it: {exc}")
        say("Usually a bitness mismatch or a missing .NET runtime.")
        return None, None

    inst = Instrument()
    try:
        inst.AutolabConnection.EmbeddedExeFileToStart = ADX
        inst.set_HardwareSetupFile(HDW)
        inst.Connect()
        if not inst.AutolabConnection.IsConnected:
            say("Connect failed — cell untouched. Close NOVA? Right HDW file?")
            return None, None
    except Exception as exc:  # noqa: BLE001
        say(f"Connect failed: {exc}")
        say("Close NOVA / a prior script holding the link, or fix HDW.")
        return None, None

    say("Connected OK — cell NOT switched on.")
    return inst, clr


# --- Q1/Q2: the procedure route -------------------------------------------

def inspect_procedure(inst):
    """Load a .nox and reflect it. Loading does NOT run anything."""
    rule("Q1/Q2 — the procedure route (LoadProcedure / parameters)")
    if not NOX:
        say("NOX is empty — skipped. Set it to a NOVA .nox to answer Q1 and Q2.")
        return None
    if not os.path.isfile(NOX):
        say(f"NOX does not exist: {NOX}")
        return None

    try:
        inst.LoadProcedure(NOX)
    except Exception as exc:  # noqa: BLE001
        say(f"LoadProcedure failed: {exc}")
        return None
    say(f"LoadProcedure OK: {os.path.basename(NOX)}")

    proc = _safe(lambda: inst.Procedure, None)
    dump_members(proc, "Instrument.Procedure")

    # Q2 is the expensive question: can we change a potential before running?
    say("")
    say("Q2 — parameter access. Looking for a command tree we can write into:")
    for attr in ("Commands", "CommandList", "Steps"):
        node = _safe(lambda a=attr: getattr(proc, a), None)
        if node is None or isinstance(node, str):
            continue
        say(f"  Procedure.{attr} exists -> {type(node)}")
        dump_members(node, f"Procedure.{attr}")
        try:
            for i, cmd in enumerate(node):
                if i >= 12:
                    say("    ... (truncated)")
                    break
                name = _safe(lambda c=cmd: str(c.CommandId), None)
                say(f"    [{i}] {name}")
                params = _safe(lambda c=cmd: c.CommandParameters, None)
                if params is not None and not isinstance(params, str):
                    for p in params:
                        pn = _safe(lambda p=p: str(p.ParameterName), None)
                        pv = _safe(lambda p=p: str(p.ValueAsObject), None)
                        say(f"         param {pn} = {pv}")
        except Exception as exc:  # noqa: BLE001
            say(f"    could not iterate: {exc}")
    return proc


def run_procedure(inst):
    """Q1: does Measure() block, and how long does it take? ENERGIZES THE CELL."""
    rule("Q1 (live) — Measure() timing and blocking behaviour")
    if not (ENERGIZE_CELL and NOX):
        say("Skipped (needs ENERGIZE_CELL = True and a NOX path).")
        return
    say("Running the procedure. Cell WILL be energized — dummy cell only.")
    t0 = time.time()
    try:
        inst.Measure()
    except Exception as exc:  # noqa: BLE001
        say(f"Measure() raised: {exc}")
        return
    elapsed = time.time() - t0
    say(f"Measure() returned after {elapsed:.2f} s.")
    say("  If that is ~the procedure's real duration, Measure() BLOCKS -> the driver")
    say("  needs its own thread (like the Gamry). If it returned immediately, poll it.")

    for attr in ("IsMeasuring", "Busy", "IsBusy"):
        v = _safe(lambda a=attr: getattr(inst, a), None)
        say(f"  Instrument.{attr} = {v}")

    dump_members(_safe(lambda: inst.Procedure, None), "Procedure (after Measure)")


# --- Q3/Q4/Q5: direct control ---------------------------------------------

def hold_potential(inst):
    """Q3/Q4/Q5: hold a potential via Ei and watch for data. ENERGIZES THE CELL."""
    rule("Q3/Q4/Q5 — direct Ei hold, data shape, data-during-run")
    ei = _safe(lambda: inst.Ei, None)
    dump_members(ei, "Instrument.Ei")
    if not ENERGIZE_CELL:
        say("")
        say("Hold skipped (ENERGIZE_CELL = False). Reflection above still answers")
        say("what Ei exposes; flip the flag with a dummy cell to answer Q3/Q4/Q5.")
        return
    if ei is None:
        say("No Ei — cannot hold a potential this way.")
        return

    say("")
    say(f"Holding {TEST_POTENTIAL} V for {HOLD_SECONDS} s. DUMMY CELL ONLY.")
    try:
        try:
            ei.Setpoint = TEST_POTENTIAL
        except Exception:  # noqa: BLE001
            ei.set_Setpoint(TEST_POTENTIAL)
        ei.CellOnOff = True
        t0 = time.time()
        samples = 0
        while time.time() - t0 < HOLD_SECONDS:
            v = _safe(lambda: ei.PotentialApplied, None)
            i = _safe(lambda: ei.Current, None)
            if samples < 5:
                say(f"  t={time.time() - t0:5.2f}s  E={v}  I={i}")
            samples += 1
            time.sleep(POLL_SECONDS)
        say(f"Held OK; polled {samples} times.")
        say("  Q4: if E and I read back as numbers above, a software-timed chrono is")
        say("      possible with no .nox at all.")
        say("  Q5: values changing across polls == data IS available during a run.")
    except Exception as exc:  # noqa: BLE001
        say(f"Hold failed: {exc}")
    finally:
        try:
            ei.CellOnOff = False
            say("Cell switched OFF.")
        except Exception as exc:  # noqa: BLE001
            say(f"WARNING: could not switch the cell off: {exc}")


def inspect_sampler(inst):
    """Q4: what array does the SDK hand back?"""
    rule("Q4 — Sampler / signal arrays")
    dump_members(_safe(lambda: inst.Sampler, None), "Instrument.Sampler")
    say("")
    say("We need per-sample TIME, POTENTIAL and CURRENT to build a")
    say("data.EchemData(time, potential, current). Note which members give arrays.")


def inspect_abort_and_liveness(inst):
    """Q6/Q7: aborting a run, and noticing a vanished instrument."""
    rule("Q6/Q7 — abort and liveness")
    for attr in ("Abort", "Stop", "StopMeasurement", "IsMeasuring"):
        say(f"  Instrument.{attr}: {'present' if hasattr(inst, attr) else 'ABSENT'}")
    say("")
    say("  AutolabConnection.IsConnected is the obvious liveness check for")
    say("  device_lost(); whether it goes False on a yanked USB cable is worth one")
    say("  deliberate test (pull the cable mid-hold) — the Gamry equivalent was")
    say("  hard-won and caught a silently truncated data file.")
    dump_members(_safe(lambda: inst.AutolabConnection, None), "AutolabConnection")


def main():
    rule("AUTOLAB — CAN PYTHON RUN ELECTROCHEMISTRY?")
    say(f"when          : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    bits = struct.calcsize("P") * 8
    say(f"python        : {sys.version.split()[0]} ({bits}-bit)")
    say(f"ENERGIZE_CELL : {ENERGIZE_CELL}")
    say(f"NOX           : {NOX or '(none)'}")

    inst, _clr = connect()
    if inst is None:
        say("")
        say("Stopped: no connection. Nothing was energized.")
        return 0

    try:
        dump_members(inst, "Instrument")
        inspect_procedure(inst)
        inspect_sampler(inst)
        inspect_abort_and_liveness(inst)
        hold_potential(inst)
        run_procedure(inst)
    finally:
        try:
            ei = getattr(inst, "Ei", None)
            if ei is not None:
                ei.CellOnOff = False      # belt and braces: never leave the cell on
        except Exception:  # noqa: BLE001
            pass
        try:
            inst.Disconnect()
            say("")
            say("Disconnected.")
        except Exception as exc:  # noqa: BLE001
            say(f"Disconnect failed: {exc}")

    rule("NEXT")
    say("Commit autolab_api_report.txt and push it — that file is what the driver")
    say("gets written from. The decisive answers are Q2 (can a .nox be")
    say("parameterized) and Q1 (does Measure() block).")
    return 0


if __name__ == "__main__":
    code = main()
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines) + "\n")
        print(f"\nReport written to: {REPORT_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not write the report: {exc}")
    sys.exit(code)
