# Can Python *run* electrochemistry on the Autolab? — running `query_autolab_run.py`

The third and last Autolab probe. All three are now answered:

| Probe | Question | Answer |
|---|---|---|
| `query_autolab.py` | can Python connect? | ✅ yes, under 64-bit |
| `query_avantes_trigger.py` | can Python fire the Avantes trigger? | ✅ yes, DIO P1.A |
| **`query_autolab_run.py`** | **can Python run CV / a hold and read the data back?** | ✅ yes — see §3.5 |

`spec_echem/potentiostat.py` defines a nine-method driver contract. `fire()` is proven. The other
eight are unknown, because nothing has yet called `Ei`, `LoadProcedure`, `Measure` or `Sampler` —
we have only *read about* them. This probe converts that reading into facts.

**The design it is testing.** Use the vendor's standard CV and CA the way the Gamry driver uses
toolkitpy's own `signal_r_up_dn_new` / `signal_d_step_new` — NOVA owns the waveform, we supply the
parameters. The staircase and its sampling stay firmware-timed, which a Python loop cannot match.

---

## 0. Before you start

```
cd path\to\spec-echem
git pull
python -c "from spec_echem.build_info import build_id; print(build_id())"
```

You want `0.2.0+34.g8262b08` or later. An older build won't have the parameter-write test.

Use the **64-bit `SpecEchem`** env — the same one `query_autolab.py` connected from. Nothing here
needs numpy or Qt.

---

## 1. Set the paths

Open `examples/query_autolab_run.py` and copy the three paths that already worked in
`query_autolab.py`:

| Constant | What it is |
|---|---|
| `SDK` | the `EcoChemie.Autolab.Sdk` assembly, **no `.dll`** |
| `ADX` | the `Adk.x` hardware driver |
| `HDW` | the hardware-setup XML for **this** instrument |

Then set one more:

| Constant | What to point it at |
|---|---|
| `NOX` | a **standard** CV or chronoamperometry `.nox` |

`NOX` is the important one. Leave it empty and the probe skips the questions that matter (Q1, Q2,
Q8). Use one of the procedures the SDK ships in its **`Standard Nova Procedures\`** folder, e.g.

```
C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Cyclic voltammetry.nox
C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\Standard Nova Procedures\Chrono amperometry.nox
```

— not a hand-built procedure from `Documents\NOVA 2.1\`. The design intent is to drive the vendor's
*standard* measurement (the way the Gamry driver uses toolkitpy's own signal constructors), so the
probe should characterise a standard procedure.

For **Q8 only** (does the `.nox` already pulse P1.A?), a second **non-energized** run pointed at one
of the rig's `PC_Spectral*` procedures is worth it — those contain the trigger pulse. Do **not**
point `NOX` at a `PC_Spectral*` file for the energized run in §4: it also embeds `AvantesStart` /
`SpectroTriggered` commands that seize the spectrometer over USB.

**Close NOVA before running.** A held link is the most common failure.

---

## 2. First run — nothing is energized

```
python examples\query_autolab_run.py
```

With `NOX` set and `ENERGIZE_CELL = False` (its default): the probe connects, reflects the SDK, loads
the procedure, tests whether a parameter can be **written**, and disconnects. It never switches the
cell on, never applies a potential, never calls `Measure()`. (With `NOX` left empty it still
connects and reflects, but skips Q1/Q2/Q8 entirely — so set `NOX`.)

This run alone answers the two questions that size the whole job. **It is worth doing even if you
have twenty minutes and no dummy cell.**

---

## 3. Reading the answers

The probe prints a verdict for each. What each one means:

### Q2 — can a procedure's parameters be written?

The decisive one. It writes a value, reads it back, and restores the original.

- **YES** → one standard procedure per experiment type, re-parameterized per cycle. This is what
  the incrementing doping potential needs (`potential = start + run_number × step`), and it makes
  the Autolab driver structurally the same as the Gamry one — probably smaller, since NOVA owns
  the waveform.
- **NO** → the procedure route needs one `.nox` per potential, which isn't practical. Fallback is a
  hybrid: CV from a procedure, chrono from direct `Ei`. That works, but chrono sampling becomes
  software-timed, which is a real downgrade on the current trace. Worth knowing before it's built.

### Q8 — does the procedure already pulse DIO?

- **YES** → the trigger can live inside the `.nox`, exactly as DIGOUT0 lives inside a `.GSequence`,
  and `fire()` collapses to "start the procedure". The pulse then lands on an already-armed
  spectrometer — late is safe, only early is fatal.
- **NO** → either add a P1.A pulse in NOVA, or have Python pulse DIO before starting the run.

### Q4 — what the data looks like

We need per-sample **time, potential, current** to build a `data.EchemData`. On SDK 2.1 the recorded
arrays are **not** on `Sampler` (its `GetSignal(name)` returns a *scalar* `Signal.Value`, useful for
live polling only) — they hang off the **command's `.Signals`** list (`CommandParameterSignalList`),
read after the run. Note which channels come back with a full array.

---

## 3.5 What the probe found (PGSTAT302N + SDK 2.1, 2026-08-31)

Full listing in `examples/autolab_api_report.txt`. Headlines:

- **Q2 — YES.** `LoadProcedure()` *returns* the `Procedure` object (there is no `inst.Procedure`).
  `Procedure.Commands` is a named list (`.Names` / `.IdNames`); numeric params expose `ValueAsObject`
  that takes a write and reads back. The standard CV's vertices, step, scan rate, conditioning
  potential and wait are all writable. → one standard `.nox` per experiment type, re-parameterized
  per cycle.
- **Q1 — `Measure()` is NON-BLOCKING.** `Procedure.Measure()` returns in ~0.3 s with
  `IsMeasuring = True`; poll `Procedure.IsMeasuring` to completion. No dedicated driver thread
  needed — simpler than the Gamry.
- **Q4 — clean mapping.** After the run, `Commands['CV staircase'].Signals` gives 1640-point arrays
  including `CalcTime` (s), `EI_0.CalcPotential` (V) and `EI_0.CalcCurrent` (A) — i.e. there **is** a
  time channel. `sg.ValueAsObject` is a `List<Double>`. Maps straight onto
  `data.EchemData(time, potential, current)`.
- **Q6/Q7 — on the `Procedure` object:** `Abort()`, `Hold()`, `Continue()`, `Skip()`,
  `IsMeasuring`; `AutolabConnection.IsConnected` for device-lost.
- **Q8 — NO** for the SDK standard CV: no DIO command. `fire()` pulses `Dio.DioPortsP1[0]` from
  Python (as `query_avantes_trigger.py` already did), or a P1.A command is added to the `.nox`.
- **Cell switching:** `Ei.CellOnOff` is the nested enum `EI.EICellOnOff` (`On` / `Off`) — pythonnet
  3.0 rejects a bare bool/int.

---

## 4. Optional — with a dummy or empty cell

To confirm Q1 timing and Q3/Q5 (direct `Ei` hold, data during the run), set:

```python
ENERGIZE_CELL = True
```

> ⚠️ **Dummy cell, test resistor, or empty cell — never a real sample.** An empty cell (leads open)
> is an open circuit: no current path, no hazard — you just get railed potentials and
> `PotentialOverload` flags. A real electrode is not where you want the first run of new
> instrument-control code. The probe switches the cell off in a `finally` and verifies `Ei.Cell`,
> but that is a backstop, not a substitute for keeping a real sample out of the loop.

`TEST_POTENTIAL` defaults to 0.0 V and `HOLD_SECONDS` to 3 s. Leave them unless you have a reason.
The probe polls `Procedure.IsMeasuring` to completion (150 s cap, then `Procedure.Abort()`), so an
energized run takes about as long as the procedure itself (~50 s for the standard CV).

---

## 5. Bring the answers back

The probe writes **`examples/autolab_api_report.txt`** — every line it printed, including the full
API listing. That file is what the driver gets written from.

```
git add examples/autolab_api_report.txt
git commit -m "Autolab API report from the Metrohm rig"
git push origin gui-dev
```

Pushing it beats retyping API listings between machines, and it means the next session starts from
what the SDK actually does rather than from documentation.

---

## 6. If it won't connect

Same order as `query_autolab_setup.md`:

1. **NOVA (or a previous script) still holds the link** — close it; power-cycle the Autolab if it
   persists.
2. **Wrong `HDW`** — the XML must match this instrument and its modules.
3. **Bitness** — pythonnet and the SDK must agree; a mismatch shows up as "could not load it".
4. **Wrong `SDK` path** — point at the real assembly, no `.dll` extension.

If `LoadProcedure` fails but the connect succeeded, check that `NOX` is a full path to a `.nox` the
NOVA on this box can open.
