# spec-echem — TODO

Running list of planned work and deferred cleanups. (Active design/status notes live in CLAUDE.md.)

## Gamry DTA converter — cleanups for when we own the parser

The conversion (raw `.DTA` → clean `.txt`) currently runs as a manual post-collection step in
`notebooks/gamry_dta_conversion.ipynb`, using the third-party `gamry_parser` library. The GUI only
reads the clean output (`spec_echem/gamry_data.py`). When we fold the converter into the package
and/or roll our own raw-`.DTA` parser, address:

- [ ] **Pre-dedoping is skipped.** The converter ignores `prededope*` files. For consistency, add
      `prededope_#N.dta → prededoping(N).txt` (pairs with `prededopingspectra(N).txt`), even though
      it's an optional/low-value step.
- [ ] **`+100` magic offset on the chrono `Time (s)` column.** The converter sets
      `Time = Corrected + 100`. Likely vestigial (downstream keys off `Corrected time`, which starts
      at 0). Confirm nothing depends on it, then drop or document.
- [ ] **Positional CV column drop is fragile.** CV conversion drops columns `[0,3,4,5,6,7,8]` by
      position. Select potential/current by name instead.
- [ ] **Multi-cycle CV is concatenated** into one series (loops overlay). Fine for I-vs-E plotting;
      just noted — revisit if per-cycle separation is ever needed.

## Integration-time unit is ambiguous (reconcile before next data campaign)

- [ ] Three sources disagree on the unit of the integration time:
      - `CLAUDE.md` documents `set_integration_time(time)` as **seconds** ("0.05 = 50 ms")
      - `settings.py` names the field **`integration_time_ms`** (default `0.022`)
      - the value flows unchanged into the Avantes `MeasConfigType.m_IntegrationTime`, which the
        Avantes SDK defines in **milliseconds**
      The GUI passes the spin value straight through (`set_integration_time(self.integration_spin.value())`),
      so whatever the box shows IS `m_IntegrationTime`. The 2026-06-16 validation run recorded
      `0.08` — fine for the structural comparison (both control and GUI used the same value), but
      the label/doc mismatch means a student can't tell what unit to type. Pick one unit, fix the
      field name + CLAUDE.md doc + any conversion so they agree, and label the GUI spin box with it.

## Decide later (triggered)

- [ ] **Roll our own raw-`.DTA` parser** to drop the `gamry_parser` dependency — only when triggered
      (distribution/reproducibility need, `gamry_parser` breaks/unmaintained, or GUI-automated
      conversion). Check `gamry_parser` license first (likely MIT) to learn from it.

## Echem plotting in the GUI (Phase 1)

- [ ] Wire CV (I vs E) + chrono (I vs t) plots into the Results review area, reading converted files
      via `spec_echem.gamry_data`. Live echem deferred to the EchemToolkitPy phase.
