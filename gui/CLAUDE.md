# gui/ — design notes

Loaded when working under `gui/`. Moved from the root CLAUDE.md on 2026-10-20; some
bullets predate Python Gamry mode and the live plot, so check them against the code.

- **Phase 1 (now):** 64-bit SpecEchem env. PyQt5 + QtPy + embedded matplotlib. No Gamry Python
  control yet — `.GSequence` + hardware trigger; Python only drives the spectrometer.
- **Phase 3 (post Gamry 64-bit):** everything 64-bit; optionally swap to PySide6 via QtPy.
- **Why QtPy abstraction:** keeps the binding swappable across these phases (PyQt5 now, PySide6 later)
  with no code changes. PySide6 has no 32-bit Windows wheel, so PyQt5 is the binding for Phase 2.
- **Why matplotlib not PyQtGraph:** all plots are post-segment/static, so PyQtGraph's live-update
  edge is moot. matplotlib is one fewer dependency, familiar, publication-style, and works under
  both PyQt5 and PySide6 via `FigureCanvasQTAgg`. Rendering happens on the GUI thread, never the
  acquisition thread, so it cannot threaten the ~50 ms/spectrum timing budget. If live update is
  ever wanted, throttled redraw (2–5 Hz) of in-memory data stays well within matplotlib's range —
  PyQtGraph would only be needed for 30–60 Hz rendering, which human monitoring never requires.
- **PySide6 version pin:** when migrating, pin below 6.9.1 (active regressions in 6.9.x)
- **UI pattern:** tabbed layout (6 tabs: Instrument, Parameters, Run, Results, Analysis, Band Fits), not QWizard.
- **Architecture:** thin GUI over current workflow first; swap in EchemToolkitPy backend later.
  Threading: worker-object + `moveToThread()` (not QThread subclass).
- **Potentiostat status (phase-aware):** keep a potentiostat status indicator in the layout, but in
  the 32-bit phase Python CANNOT control or query the Gamry — the `.GSequence` file runs
  autonomously and fires hardware triggers. Show it as "Gamry: standalone (runs from sequence file)"
  in a neutral/disabled state now; it becomes a live green/red connection indicator when
  EchemToolkitPy arrives. The real coordination today is a two-step manual sequence: (1) student
  clicks Start → worker arms spectrometer and BLOCKS on the first trigger → UI shows "Armed — now
  start the Gamry sequence"; (2) student starts the Gamry sequence manually → triggers fire →
  collection proceeds. The UI must make this two-step start explicit so students aren't confused.
- **No live plot updates** — plots update post-segment only (simplifies threading)
