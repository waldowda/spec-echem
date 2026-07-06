# spec-echem — Project Status

A short, human-readable snapshot of where the project is and what's next, so the thread
isn't lost between sessions. Task-level detail lives in [`TODO.md`](TODO.md); design context
in [`CLAUDE.md`](CLAUDE.md); output formats in [`docs/data-format.md`](docs/data-format.md).

_Last updated: 2026-07-05_

---

## Where we are

**Phase 2.5 — Python-mode echem data capture — is DONE, hardware-validated, and merged to
`gui-dev`.**

When Python drives the Gamry (`ToolkitPotentiostat`), the potentiostat's current/potential is
now saved alongside the UV-Vis spectra:

- clean analysis `.txt` (`CV.txt`, `steps(N).txt`, `dedoping(N).txt`, `prededoping(N).txt`) in
  Raj's `OECT_processing` format, and
- native Gamry `.dta` in a `dta/` subfolder (on by default; `save_dta` toggle).

Validated headless (`examples/validate_echem_capture.py`, 6/6) and on a real GUI run.
98 tests pass. Also landed this cycle: consistent Load/Save folders (settings/darks/refs all
under the data root) with per-day serial filenames.

Everything before this is also done: the modular package, the 4-tab PyQt5 GUI, hardware
trigger sync (DIGOUT0 → Avantes), and all-Python Gamry control for every segment type
(CV + doping/dedoping/pre-dedoping), all validated against the golden 8-column output.

## The one lesson worth remembering

The Phase 2.5 "empty echem files" bug was a **Python reference-counting / garbage-collection
issue**, not a threading or timing problem. `_build_signal()` returned only the `curve` and
dropped the `signal` local; because `set_signal` stores the signal in C where Python can't see
it, CPython freed it immediately, and the curve then ran a freed (empty) waveform — started,
ran zero samples, died in ~50 ms. **Fix:** return `(curve, signal)` and keep *both* alive
through `run()` and the poll loop. The two-thread / acq_data-polling / build-runway theories
we chased first were all red herrings. (This is also why the current per-segment-thread
machinery is now suspect — see follow-up #1.)

## What's next (priority order)

1. **Two-thread simplification check.** Now that the bug was a lifetime issue, is the
   dedicated-per-segment-thread + fresh-session-per-segment + acq_data-in-loop machinery still
   needed, or would a simpler same-thread design work? Best done on the instrument box.
2. **First real-sample test** — a real polymer sample with proper dark (lamp blocked) and
   reference (blank, lamp on), full multi-cycle sequence in one run.
3. ~~Echem plots in the GUI (I-vs-E, I-vs-t) in the Results area.~~ **DONE (2026-07-06)** —
   absorbance and electrochemistry now shown side by side per segment. Successor:
   **live echem graph during a Python-mode run** (poll `acq_data()` mid-run) so you can watch
   the CV and abort before committing to a long doping sweep. See `TODO.md`.
4. **GUI rearrangement pass** — further tab-layout cleanup (a first pass landed 2026-07-06:
   Instrument tab split into four side-by-side graphs, Run controls moved to top). Still to do:
   auto-verify the Gamry when Python mode is selected, and regroup the External/Python/Identify
   controls (see the "GUI UX" section in `TODO.md`).
5. **Linearity check** on the peak test-counts (saturation warning).
6. **Future/optional:** a compact tidy-DataFrame sidecar to cut Raj-format duplication —
   an *additional* output; the `.txt` files stay the compatibility contract.

## The true acceptance test (not yet done)

Everything so far proves the plumbing. The real end-to-end proof is a real polymer sample run
whose output analyzes cleanly in Raj's `OECT_processing`. That remains the eventual gold
standard, not yet scheduled.

## Machines

- **Mac Mini** (`~/dev/SpectroElectroChem/spec-echem`) — development; no hardware, imports
  guarded so the package + GUI run against fakes.
- **Win11 `SpecEchem32`** — instrument box (Avantes + Gamry). Claude Code is installed here now,
  which greatly speeds up hardware-loop debugging.
