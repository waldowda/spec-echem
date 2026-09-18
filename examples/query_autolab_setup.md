# Talking to a Metrohm Autolab from Python — setup

A short guide for running `query_autolab.py`, a **read-only, cell-safe** check that
answers one question: *can this PC open the Autolab from Python?* It connects through
the Autolab SDK, reports whether the link came up, and disconnects. It does **not**
turn the cell on, apply a potential, or run any procedure.

This is the potentiostat counterpart to `query_avantes.py` (the spectrometer check).
Tested target: a **PGSTAT302N**, but it works for any model the SDK supports — you
just point it at that model's hardware-setup file.

---

## 1. What you need installed

1. **The Metrohm AUTOLAB SDK (v1.11).** This is the piece that matters, and it is
   *separate* from NOVA. It's a free download from Metrohm
   (metrohm-autolab.com → Products → Echem → Software → SDK), or on the media that
   came with the instrument. Installing it creates the folder the three paths below
   point into (typically `C:\Program Files\Metrohm Autolab\autolabsdk\`).

2. **pythonnet** — the bridge from Python to the SDK's .NET assembly:

   ```
   pip install pythonnet
   ```

3. **Python** — **match Python's bitness to the SDK.** This is the one thing to
   confirm on your box: if the SDK / its `Adk.x` hardware driver are 32-bit, use
   32-bit Python; if 64-bit, use 64-bit Python. A mismatch shows up as an assembly
   "could not load it" error. (No numpy/pandas/Qt needed for this check.)

---

## 2. Set the three paths at the top of `query_autolab.py`

Open the script and edit these to match your install and **your model**:

| Constant | What it is | Typical value |
|----------|-----------|---------------|
| `SDK` | The `EcoChemie.Autolab.Sdk` assembly, **no `.dll`** | `…\autolabsdk\EcoChemie.Autolab.Sdk` |
| `ADX` | The `Adk.x` hardware driver the SDK launches | `…\autolabsdk\Hardware Setup Files\Adk.x` |
| `HDW` | The hardware-setup **XML for your model** | `…\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml` |

> `HDW` is **model-specific** — the folder name is the model (`PGSTAT302N`), and the
> `.xml` names the module (e.g. `FRA32M`). Browse `…\Hardware Setup Files\` and pick
> the one that matches your instrument and installed modules.

---

## 3. It does not touch the cell

Connecting and turning the cell on are **separate** operations in the SDK. This probe
only connects, reads `IsConnected`, and disconnects — it never calls the cell-power or
measurement functions. So it's safe to run with a cell attached.

That said, the **first** time you run any new instrument-control code, good practice is
to run it with the working electrode disconnected (or a dummy cell / test resistor) as
a belt-and-suspenders check — the same precaution you'd take before any first run.

---

## 4. Run it

```
python query_autolab.py
```

A good result:

```
================================================================
METROHM AUTOLAB  (read-only, cell-safe connect probe)
================================================================
Connected OK — Python can talk to the Autolab. [OK]
Hardware setup file : C:\Program Files\Metrohm Autolab\autolabsdk\Hardware Setup Files\PGSTAT302N\HardwareSetup.FRA32M.xml

(The cell was NOT turned on and no procedure was run — this is a connection check only.)
```

---

## 5. If it won't connect

In rough order of likelihood:

1. **Another program holds the link.** NOVA, or a previous script that didn't
   disconnect cleanly, can keep the instrument bound. Close NOVA and retry; if it
   persists, power-cycle the Autolab.
2. **Wrong `HDW` file.** The hardware-setup XML must match this instrument and its
   modules. Pick the right model folder / module `.xml`.
3. **Bitness mismatch** (§1.3) — shows up as "could not load it" when referencing the
   assembly.
4. **Wrong `SDK` path** — point it at the real `EcoChemie.Autolab.Sdk` (no `.dll`).

---

## 6. What this does and doesn't prove

A green result means Python can **open and talk to** the Autolab — the hard part of
getting the SDK + pythonnet + driver stack working. It does **not** run an
experiment. Actually driving a measurement (CV, chronoamperometry, …) is done by
loading a NOVA-built `.nox` **procedure** and running it — a separate step, not
covered here.
