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
