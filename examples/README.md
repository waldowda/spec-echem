# examples/ — Gamry bench scripts (Phase 2)

Small, standalone diagnostics that drive **one technique at a time** via
`toolkitpy` — **no spectrometer, no GUI**. They isolate the Gamry/EchemToolkitPy
side so the API and timing can be confirmed before the full integrated run.

**Where to run:** the 32-bit Win11 box, `SpecEchem32` conda env, Gamry connected.
They `import toolkitpy` and exit with a clear message anywhere else (e.g. the Mac).

| Script | What it does | Answers |
|--------|--------------|---------|
| `bench_gamry_chrono.py` | One constant-potential hold (doping/dedoping building block) | Does `curve.run()` block or return + poll? (times it) |
| `bench_gamry_cv.py` | One CV scan, defaults = the `.GSequence` CV vertices | Vertex / scan-rate / sample-time mapping |
| `bench_digital_out.py` | Pulses DIGOUT0 high/low | Can Python fire the Avantes trigger line? |

Each has an editable parameter block at the top (defaults match
`gamry/Spec_Echem_20250714.GSequence`). They build the same signals
`spec_echem/potentiostat.py` uses, so confirming them here de-risks the GUI path.

These are diagnostics, not unit tests — automated mock-hardware tests live in
`tests/`.

---

## Instrument self-test probes — "can Python talk to it?"

Standalone, no `spec_echem` import, each with its own setup guide. Written for bringing the
software up on a machine or a rig it has never run on. The Autolab ones are 64-bit.

| Script | Guide | Answers |
|--------|-------|---------|
| `query_avantes.py` | `query_avantes_setup.md` | Can Python open the spectrometer? Serial, pixels, wavelength span. |
| `query_autolab.py` | `query_autolab_setup.md` | Can Python connect to the Autolab? Read-only, **cell-safe**. |
| `query_avantes_trigger.py` | — (same SDKs) | Does the Autolab DIO → Avantes hardware trigger fire, from one process, no NOVA? |
| `query_autolab_run.py` | `query_autolab_run_setup.md` | Can Python **run** CV / a hold and read the data back? Reflection by default; energizing is opt-in. |

Run them in that order — a failure early makes everything after it ambiguous. The procedure is
`docs/metrohm-bench-check.md`; the findings so far are `docs/metrohm-rig-status.md`.

## Autolab bench scripts — characterising the templates

Written against the API proven by the probes (`docs/autolab-run-api.md`), these run real
measurements on a **10 kΩ dummy resistor** and answer what is left before the
`AutolabPotentiostat` driver can be written. Shared SDK calls live in `autolab_common.py`.

| Script | Closes | What it settles |
|--------|--------|-----------------|
| `bench_autolab_cv.py` | items 1, 2, 3 | Is `CommandParameters[4]` the crossing count? Does a second run reuse the first's buffer? What does `Abort()` leave behind? Writes CSVs of a known-good CV. |
| `bench_autolab_ca.py` | item 6 | The chronoamperometry parameter index map — **unknown**, and needed by three of the four data types. Verifies each candidate index against the recorded data instead of adopting it on looks. |
| `bench_autolab_fault.py` | items 4, 7 | What a *failed* run looks like. Compares a clean run against an overload, an open cell and a USB pull — because a fault the SDK doesn't report finishes looking exactly like a success. |

Both default to `ENERGIZE_CELL = False`, which runs the parameter-map phase only and touches
nothing. That phase is worth running even without a dummy cell.
