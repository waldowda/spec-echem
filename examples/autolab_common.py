"""
autolab_common.py — the Autolab SDK calls that are already PROVEN on hardware.

Shared by `bench_autolab_cv.py` and `bench_autolab_ca.py` so the two bench scripts
contain their experiment and nothing else. The SDK *facts* encoded here were proven on
the UW rig on 2026-08-31 (see docs/autolab-run-api.md and examples/autolab_api_report.txt),
not inferred from documentation — the distinction that cost this project several days.
The *helper functions* wrapping those facts were written the same day but first
exercised against hardware on 2026-09-03; `set_param()` needed a fix then
(CommandParameterList rejects a bare int index — iterate instead).

Does NOT import the spec-echem package — these stay runnable on a bare env with only
pythonnet installed.

The SDK facts that are easy to get wrong, all learned the hard way:
  * LoadProcedure(path) RETURNS the Procedure. There is no inst.Procedure.
  * Measure() is NON-BLOCKING; poll Procedure.IsMeasuring until False.
  * A Command has no name property — names live on the parent list (.Names/.IdNames).
  * A CommandParameter has no name property either — address parameters BY INDEX.
  * Ei.CellOnOff needs the nested enum member EI.EICellOnOff.On/.Off; pythonnet 3.0
    rejects a bare bool or int.
  * Recorded arrays hang off command.Signals, read AFTER the run.
"""
import csv
import os
import sys
import time

# The three install paths — copy whatever worked in query_autolab.py.
SDK = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\EcoChemie.Autolab.Sdk"
ADX = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Hardware Setup Files\Adk.x"
HDW = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml"

_lines = []


def say(text=""):
    """Print and record, so every run leaves a transcript worth committing."""
    print(text)
    _lines.append(text)


def rule(title):
    say("")
    say("=" * 72)
    say(title)
    say("=" * 72)


def write_transcript(path):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines) + "\n")
        print(f"\nTranscript written to: {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not write the transcript: {exc}")


def safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


# --- connect / cell --------------------------------------------------------

def connect():
    """Open the instrument. Does NOT switch the cell on. Returns inst or None."""
    try:
        import clr  # pythonnet
    except Exception as exc:  # noqa: BLE001
        say(f"Could not import 'clr' (pythonnet): {exc}")
        say("Install with:  pip install pythonnet   — see query_autolab_setup.md")
        return None

    sdk_dir = os.path.dirname(SDK)
    if sdk_dir and sdk_dir not in sys.path:
        sys.path.append(sdk_dir)
    if not clr.FindAssembly(SDK):
        say(f"Cannot find the SDK assembly at:\n  {SDK}")
        say("Fix SDK at the top of autolab_common.py (no .dll extension).")
        return None
    try:
        clr.AddReference(SDK)
        from EcoChemie.Autolab.Sdk import Instrument
    except Exception as exc:  # noqa: BLE001
        say(f"Found the SDK assembly but could not load it: {exc}")
        say("Usually a bitness mismatch or a missing .NET runtime.")
        return None

    inst = Instrument()
    try:
        inst.AutolabConnection.EmbeddedExeFileToStart = ADX
        inst.set_HardwareSetupFile(HDW)
        inst.Connect()
        if not inst.AutolabConnection.IsConnected:
            say("Connect failed — cell untouched. Close NOVA? Right HDW file?")
            return None
    except Exception as exc:  # noqa: BLE001
        say(f"Connect failed: {exc}")
        say("Close NOVA / a prior script holding the link, or fix HDW.")
        return None

    say("Connected OK — cell NOT switched on.")
    return inst


def switch_cell(inst, on):
    """The safety-critical path: a run must always be able to de-energize.

    Ei.CellOnOff is the nested enum EI.EICellOnOff (On=1/Off=0) and pythonnet 3.0
    refuses a bare bool/int, so the member has to be assigned.
    """
    from EcoChemie.Autolab.Sdk import EI
    ei = inst.Ei
    ei.CellOnOff = EI.EICellOnOff.On if on else EI.EICellOnOff.Off
    return safe(lambda: bool(ei.Cell))


def cell_off_quietly(inst):
    """Belt and braces for a finally block — never raise on the way out."""
    try:
        switch_cell(inst, False)
        say("Cell switched OFF.")
    except Exception as exc:  # noqa: BLE001
        say(f"WARNING: could not switch the cell off: {exc}")


def disconnect(inst):
    try:
        inst.Disconnect()
        say("Disconnected.")
    except Exception as exc:  # noqa: BLE001
        say(f"Disconnect failed: {exc}")


# --- digital I/O (the Avantes trigger line) --------------------------------

def open_dio(inst, index=0):
    """Set DioPortsP1[index] to Output, driven low, and return the port.

    index 0 = P1.A, which is the line NOVA's own spectro-EC procedures pulse on
    this rig and the one query_avantes_trigger.py proved reaches the Avantes.
    """
    import clr
    from EcoChemie.Autolab.Sdk import DIO
    from System import Enum

    dio = inst.Dio
    # DioPortDirection lives in EcoChemie100; reach the enum type through the DIO
    # property rather than importing that namespace directly.
    dir_type = clr.GetClrType(DIO).GetProperty("DioPortDirection").PropertyType
    output = Enum.Parse(dir_type, "Output")

    ports = dio.DioPortsP1
    if index >= len(ports):
        say(f"  DioPortsP1 has {len(ports)} ports; index {index} is out of range.")
        return None
    port = ports[index]
    try:
        port.PortDirection = output
    except Exception:  # noqa: BLE001 — some builds set direction at the DIO level
        dio.DioPortDirection = output
    port.Value = 0
    name = safe(lambda: str(port.PortName), f"index {index}")
    say(f"  DIO port {name} (DioPortsP1[{index}]) set to Output, driven low")
    return port


def describe_dio(inst):
    """List every DIO port the instrument exposes, read-only.

    Nothing has ever enumerated these — `autolab_dio_port = 0` has been an assumption
    since the first trigger probe, and the 2026-08-31 API report did not capture it.
    It matters because a DIO48 connector carries THREE independent 8-bit sections on
    one 25-pin shell (NOVA 16.3.1.3.1: A = pins 1-8, B = 17-24, C = 9-16, pin 25
    ground). A splitter can take one section to the Avantes and another to the
    AvaLight shutter, so "same connector" does not mean "same port".

    Reads names, directions and current values. Writes nothing.
    """
    say("")
    say("--- DIO ports " + "-" * 58)
    for attr in ("DioPortsP1", "DioPortsP2"):
        ports = safe(lambda a=attr: inst.Dio.__getattribute__(a))
        if ports is None:
            say(f"  {attr}: not available")
            continue
        n = safe(lambda p=ports: len(p), "?")
        say(f"  {attr}: {n} port(s)")
        try:
            for i, port in enumerate(ports):
                name = safe(lambda p=port: str(p.PortName), "?")
                direction = safe(lambda p=port: str(p.PortDirection), "?")
                value = safe(lambda p=port: int(p.Value), None)
                shown = "?" if value is None else f"0x{value:02X} (0b{value:08b})"
                say(f"    [{i}] {name:<10} direction={direction:<8} value={shown}")
        except Exception as exc:  # noqa: BLE001
            say(f"    could not iterate: {exc}")
    say("")
    say("  autolab_dio_port indexes THIS list. If the trigger and the AvaLight")
    say("  shutter sit on different ports here, they cannot interfere.")


def pulse(port, width_s=0.002, mask=0xFF):
    """low -> high -> low. The RISING edge is what the armed Avantes catches.

    `mask` selects which of the port's eight pins go high. 0xFF drives all of them,
    which is why the wired pin has never had to be identified — and why it is unsafe
    once anything else (the AvaLight shutter TTL) shares the port.
    """
    port.Value = 0
    time.sleep(0.001)
    port.Value = int(mask) & 0xFF
    time.sleep(width_s)
    port.Value = 0


def release_dio(port):
    try:
        port.Value = 0
        port.Release()
    except Exception:  # noqa: BLE001
        pass


# --- procedures ------------------------------------------------------------

def load(inst, nox):
    """LoadProcedure RETURNS the Procedure handle — keep it, there is no
    inst.Procedure on this SDK."""
    proc = inst.LoadProcedure(nox)
    say(f"Loaded: {os.path.basename(nox)}")
    return proc


def command(proc, *idnames):
    """Fetch a command by IdName, trying alternatives (templates differ)."""
    for idname in idnames:
        cmd = safe(lambda i=idname: proc.Commands[i])
        if cmd is not None:
            return cmd, idname
    return None, None


def list_commands(proc):
    """Print the command list. Names come from the LIST, not the Command."""
    names = safe(lambda: list(proc.Commands.Names), [])
    idnames = safe(lambda: list(proc.Commands.IdNames), [])
    say("")
    say("  Commands:")
    for i in range(max(len(names), len(idnames))):
        n = names[i] if i < len(names) else "?"
        idn = idnames[i] if i < len(idnames) else "?"
        say(f"    [{i}] {n:<34} {idn}")
    return idnames


def dump_parameters(cmd, label):
    """Print every parameter of a command with its index, type and value.

    Parameters have NO name property on this SDK — the index IS the address, so
    this listing is the only map there is. Reading changes nothing.
    """
    say("")
    say(f"  {label}.CommandParameters:")
    params = safe(lambda: cmd.CommandParameters)
    if params is None:
        say("    (none)")
        return []
    out = []
    try:
        for i, prm in enumerate(params):
            val = safe(lambda p=prm: p.ValueAsObject, "<unreadable>")
            say(f"    [{i}] {type(val).__name__:<10} = {val}")
            out.append(val)
    except Exception as exc:  # noqa: BLE001
        say(f"    could not iterate: {exc}")
    return out


def set_param(cmd, index, value, label=""):
    """Write one parameter by index and read it back. Returns True if it stuck.

    Reach the parameter by iterating, not `CommandParameters[index]` — this SDK's
    CommandParameterList rejects a bare Python int in get_Item ("No method matches
    given arguments"). Iteration is the access pattern query_autolab_run.py proved on
    hardware; dump_parameters() uses it too.
    """
    try:
        prm = list(cmd.CommandParameters)[index]
        prm.ValueAsObject = value
        back = prm.ValueAsObject
        ok = abs(float(back) - float(value)) < 1e-9
        say(f"    set [{index}] {label} = {value}  (read back {back}) "
            f"{'OK' if ok else 'MISMATCH'}")
        return ok
    except Exception as exc:  # noqa: BLE001
        say(f"    set [{index}] {label} = {value} FAILED: {exc}")
        return False


def run(proc, inst, poll=0.25, timeout=600.0, live=False, watch=None):
    """Measure() then poll IsMeasuring to completion. Returns elapsed seconds.

    Measure() is non-blocking, so the caller owns timing — which is exactly what
    lets the driver pulse the trigger after arming the spectrometer.

    `watch` is called once per poll with (inst, proc, elapsed). The fault bench uses
    it to sample the overload flags, and the driver will need the same hook: an
    overloaded run finishes looking exactly like a clean one, so whatever notices
    has to notice WHILE it runs.
    """
    t0 = time.time()
    proc.Measure()
    say(f"  Measure() returned in {time.time() - t0:.2f} s; "
        f"IsMeasuring={safe(lambda: proc.IsMeasuring)}")
    shown = 0
    while safe(lambda: proc.IsMeasuring, False) is True:
        if time.time() - t0 > timeout:
            say(f"  TIMEOUT after {timeout:.0f} s — aborting.")
            safe(lambda: proc.Abort())
            break
        if live and shown < 6:
            e = safe(lambda: inst.Ei.Sampler.GetSignal("WE(1).Potential").Value)
            i = safe(lambda: inst.Ei.Sampler.GetSignal("WE(1).Current").Value)
            say(f"    t={time.time() - t0:6.2f}s  E={e}  I={i}")
            shown += 1
        if watch is not None:
            try:
                watch(inst, proc, time.time() - t0)
            except Exception:  # noqa: BLE001 — a watcher must never break the run
                pass
        time.sleep(poll)
    elapsed = time.time() - t0
    say(f"  Run finished in {elapsed:.2f} s "
        f"(IsMeasuring={safe(lambda: proc.IsMeasuring)})")
    return elapsed


def read_signals(cmd):
    """command.Signals -> {IdName: [floats]}, read AFTER the run."""
    sigs = safe(lambda: cmd.Signals)
    if sigs is None:
        say("  no .Signals on this command")
        return {}
    idnames = safe(lambda: list(sigs.IdNames), [])
    out = {}
    try:
        for i, sg in enumerate(sigs):
            key = idnames[i] if i < len(idnames) else f"signal{i}"
            out[key] = safe(lambda s=sg: list(s.ValueAsObject), [])
    except Exception as exc:  # noqa: BLE001
        say(f"  could not iterate .Signals: {exc}")
    return out


def report_signals(sig):
    """One line per channel: length and endpoints — enough to spot a stale or
    truncated array without opening the CSV."""
    say("")
    say("  Recorded channels:")
    for key, vals in sig.items():
        if not vals:
            say(f"    {key:<24} 0 pts")
            continue
        say(f"    {key:<24} {len(vals):>6} pts   "
            f"first={vals[0]:.6g}  last={vals[-1]:.6g}")


def write_csv(sig, path):
    """Columns in .Signals order; rows to the shortest channel present."""
    cols = [k for k, v in sig.items() if v]
    if not cols:
        say("  nothing to write (no populated channels)")
        return None
    n = min(len(sig[c]) for c in cols)
    try:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in range(n):
                w.writerow([sig[c][r] for c in cols])
        say(f"  CSV written: {path}  ({n} rows x {len(cols)} cols)")
        return path
    except Exception as exc:  # noqa: BLE001
        say(f"  CSV write failed: {exc}")
        return None
