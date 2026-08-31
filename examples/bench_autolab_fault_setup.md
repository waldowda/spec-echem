# Running the Autolab fault tests

`bench_autolab_fault.py` answers one question: **when a run goes wrong, can you tell?**

The reason it exists is that the likely answer is *not by itself*. An open cell or a current
overload doesn't make `Measure()` fail — the instrument does what it was told, `IsMeasuring` goes
False, `.Signals` fills up, and the segment is written looking perfect while carrying no
electrochemistry. The script provokes three faults deliberately and compares each against a clean
run, so we know which observable actually moves.

> ⚠️ **10 kΩ dummy resistor only — never a real sample.**
> 2-electrode wiring: **W + WS** on one leg, **RE + CE** on the other.

---

## Before you start

- `git pull`, then run from the repo with the 64-bit `SpecEchem` env active.
- **Close NOVA.** A held link is the usual connect failure.
- The three SDK paths live in `examples/autolab_common.py` now, not in each script — copy whatever
  worked in `query_autolab.py`.
- Check `NOX` at the top of the script points at your standard CV procedure.

---

## Pass 1 — nothing is energized

```
python examples\bench_autolab_fault.py
```

With `ENERGIZE_CELL = False` (the default) it connects, prints what state can be observed, and
stops. Two things to take from the output:

- **`EI.EICurrentRange members: [...]`** — pick a **small** range, small enough that the ~100 µA
  flowing through the 10 kΩ will overload it, and put its exact spelling in `CURRENT_RANGE_NAME`.
- **The `Procedure.*` lines** — whether any status/result field exists at all. If they all read
  `<absent>`, the flags are the only thing distinguishing a fault, which is itself the answer.

---

## Pass 2 — the runs

Resistor wired, then set:

```python
ENERGIZE_CELL = True
CURRENT_RANGE_NAME = "..."      # from pass 1
```

The runs happen in this order, and the order is deliberate.

| Run | What happens | You do |
|---|---|---|
| 1. baseline | clean CV on the resistor | nothing |
| 2. overload | current range set too small | nothing — software only |
| 3. open_circuit | cell opened mid-run | **open the cell** at the prompt, then reseat at the second prompt |
| 4. usb_pull | connection lost mid-run | opt in first (`RUN_USB_PULL = True`), then pull the cable when prompted |

Runs 3 and 4 **prompt and wait for Enter**, so you're not racing the instrument.

For run 3, "open the cell" means the cell switch or unclipping **one** lead — leave everything else
wired. This is the realistic failure: it's what a loose alligator clip did on 2026-08-31.

### About the USB pull

It's off by default. It leaves the cell energized with no software control — harmless on a resistor,
never acceptable on a sample — and recovery may need a reconnect or a power cycle. That's why it
runs last. It's also answering a different question from the other three: item 7, whether
`IsConnected` flips in time for `device_lost()` to catch it.

---

## Reading the result

The script ends with a comparison table — one row per observable, one column per run:

```
observable                 baseline        overload        open_circuit
points                     1640            1640            1640
is_measuring_after         False           False           False
current_overload           False           True            False
max_abs_current            9.9e-05         ...             ~0
```

**Any row that differs from baseline is something the driver can check.** The likely outcome is that
`points` and `is_measuring_after` are identical everywhere — which would mean the SDK reports
nothing, and polling `PotentialOverload` / `CurrentOverload` during the run *is* fault detection.
A near-zero `max_abs_current` is the independent signature of an open cell.

If a fault turns out to be invisible in every row, say so — that's a finding, and it means the
driver cannot detect that case and the SOP has to.

---

## What to bring back

```
git add examples/bench_autolab_fault_report.txt examples/bench_autolab_fault_*.csv
git commit -m "Autolab fault-test results"
git push origin gui-dev
```

The transcript carries the comparison table; the CSVs let the traces be checked afterwards. Then
items 4 and 7 in `docs/autolab-run-api.md` §4 can be closed with real answers.

---

## If something goes wrong

- **Won't connect** — close NOVA; check the paths in `autolab_common.py`; power-cycle the Autolab.
- **Setting the current range fails** — the member name must match `EI.EICurrentRange` exactly, as
  printed in pass 1. Like `CellOnOff`, pythonnet needs the enum member, not a string or a number.
- **A run hangs** — `run()` times out at 600 s and calls `Abort()`. Ctrl-C is safe too: the `finally`
  switches the cell off and disconnects.
- **After a USB pull the instrument is unresponsive** — reconnect the cable, power-cycle, and
  restart the script. Expected, not a bug.
