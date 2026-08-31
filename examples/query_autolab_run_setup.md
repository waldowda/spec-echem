# Can Python *run* electrochemistry on the Autolab? — running `query_autolab_run.py`

The third and last Autolab probe. The first two are answered:

| Probe | Question | Answer |
|---|---|---|
| `query_autolab.py` | can Python connect? | ✅ yes, under 64-bit |
| `query_avantes_trigger.py` | can Python fire the Avantes trigger? | ✅ yes, DIO P1.A |
| **`query_autolab_run.py`** | **can Python run CV / a hold and read the data back?** | **this session** |

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
| `NOX` | a NOVA **standard CV or chronoamperometry** procedure |

`NOX` is the important one. Leave it empty and the probe skips the questions that matter. Worth a
second run pointed at one of the rig's `PC_Spectral*` procedures too — those already contain the
P1.A trigger pulse, which is Q8.

**Close NOVA before running.** A held link is the most common failure.

---

## 2. First run — nothing is energized

```
python examples\query_autolab_run.py
```

Out of the box `ENERGIZE_CELL = False`: the probe connects, reflects the SDK, loads the procedure,
tests whether a parameter can be **written**, and disconnects. It never switches the cell on, never
applies a potential, never calls `Measure()`.

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

We need per-sample **time, potential, current** to build a `data.EchemData`. Note which `Sampler`
members return arrays.

---

## 4. Optional — with a dummy cell only

To answer Q1 (does `Measure()` block?) and Q3/Q5 (direct `Ei` hold, data during the run), set:

```python
ENERGIZE_CELL = True
```

> ⚠️ **Dummy cell or test resistor only — never a real sample.** The first run of new
> instrument-control code is not where you want a real electrode. The probe switches the cell off
> in a `finally`, but that is a backstop, not a substitute for a dummy load.

`TEST_POTENTIAL` defaults to 0.0 V and `HOLD_SECONDS` to 3 s. Leave them unless you have a reason.

**Q1's answer:** if `Measure()` returns after roughly the procedure's real duration, it blocks and
the driver needs its own thread, as the Gamry does. If it returns immediately, we poll instead —
which would make the Autolab driver simpler than the Gamry's.

---

## 5. Bring the answers back

The probe writes **`examples/autolab_api_report.txt`** — every line it printed, including the full
API listing. That file is what the driver gets written from.

```
git add examples/autolab_api_report.txt
git commit -m "Autolab API report from the UW rig"
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
