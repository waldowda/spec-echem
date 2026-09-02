# Finishing the Autolab driver — what to fill in after the bench tests

`AutolabPotentiostat` (`spec_echem/potentiostat.py`) is written and test-covered, but it was
written **before** the bench scripts ran. This is the checklist for turning it from "correct as far
as we know" into "correct".

The design rule throughout: every unresolved point is a **named constant or an explicit stub**, so
finishing is filling in blanks rather than auditing assumptions. Nothing below is buried in logic.

> **What the tests do and don't prove.** `tests/test_potentiostat.py` drives the driver against
> `fakes.FakeAutolab`, which encodes the same understanding of the SDK that the driver does. A green
> suite proves internal consistency and catches regressions. It **cannot** catch a misreading of the
> SDK — if the fake is wrong in the same way the driver is, both agree and the tests pass. Only the
> instrument settles that.

---

## 1. Run the bench scripts (order matters)

| Script | Fills in |
|---|---|
| `bench_autolab_cv.py` | items A, B, C below |
| `bench_autolab_ca.py` | **item D — the blocker** |
| `bench_autolab_coacquire.py` | item E |
| `bench_autolab_fault.py` | items F, G |

Instructions for the fault one are in `examples/bench_autolab_fault_setup.md`. All four have an
`ENERGIZE_CELL = False` phase that prints the maps and energizes nothing — run those first.

---

## 2. What each result changes

### A. Multi-cycle — `CV_IDX_CROSSINGS`
The driver writes `2 * cv_cycles`, on the reading that a crossing count of 2 is one full cycle.

- **Confirmed** (points double, `ScanNumber` reaches 2): nothing to do.
- **Different**: fix the multiplier in `_apply_parameters`. One line, and
  `test_cv_parameters_are_written_from_settings` pins the expected value.

### B. Sampler lifecycle — the reload in `prepare()`
The driver reloads the procedure for **every** segment, which is correct whether or not `.Signals`
accumulates.

- **Buffer replaced per run**: the reload is optional. Leaving it costs one load per segment.
  Removing it is a performance change, not a correctness one — and
  `test_every_segment_reloads_the_procedure` is deliberately there to make removal a conscious act.
- **Buffer accumulates**: the reload is load-bearing. Say so in a comment so nobody optimises it
  away later.

### C. Abort — `stop()` / `finish(aborted=True)`
The driver calls `Abort()`, switches the cell off, and keeps no data. spec-echem discards aborted
segments anyway, so a partial trace is moot.

- Check the instrument is ready for the **next** `Measure()` without a reconnect. If it is not, the
  driver needs a reconnect in `stop()` — currently it does not do this.

### D. Chronoamperometry — **the blocker**

Three of the four data types (doping, dedoping, pre-dedoping) are holds, and the CA parameter
indices are unknown. The driver raises `NotImplementedError` naming this file if a chrono segment is
reached; `test_a_chrono_segment_fails_loudly_until_the_ca_map_is_known` holds that in place.

From `bench_autolab_ca.py`, fill in at the top of `potentiostat.py`:

```python
CA_COMMAND = "..."          # the hold command's IdName
CA_IDX_POTENTIAL = ...      # hold potential (V)
CA_IDX_DURATION = ...       # hold duration (s)
CA_IDX_INTERVAL = ...       # sampling interval (s), or leave None if absent
```

Adopt an index **only** if the bench script reported it CONFIRMED — it verifies each against the
recorded data rather than accepting one that looks plausible.

Then replace that test with real coverage of the chrono path: the potentials come from the same
settings as the Gamry path, so `doping_potential_start + run_number * doping_potential_step` should
be asserted the way `test_cv_parameters_are_written_from_settings` asserts the CV vertices.

**Why this is small:** only `prepare()` branches on `data_type`. `fire`, `finish`, `stop`, `pump`,
`last_data` and `close` are identical for CV and chrono — same `Measure()`, same poll, same
`.Signals` read. This is a table of integers and one method, not a second driver.

### E. Trigger delay — `_wait_window()`
The driver reads the procedure's own `FHWait` duration and pulses at that offset, so the optical and
echem clocks start together.

- `bench_autolab_coacquire.py` reports the **skew** and the `PULSE_DELAY_S` that zeroes it. If the
  measured skew is small, leave the driver reading the wait — that is self-correcting if someone
  edits the procedure in NOVA.
- If there is a consistent offset, set `autolab_pulse_delay_s` in `config/bench.ini` (it overrides)
  and record why in this file.
- If `RUN_EARLY_PULSE_CONTROL` shows an early edge is **not** missed on this hardware, then the
  arm-then-fire ordering that `acquisition.py` is built around does not apply here, and that is
  worth a conversation before relying on it either way.

### F. Fault detection — `pump()` / `_report_segment_health()`
`pump()` samples `PotentialOverload` / `CurrentOverload` every spectrum and `finish()` warns if
either fired. This is deliberate belt-and-braces: an overloaded run completes normally and its data
looks ordinary.

- If `bench_autolab_fault.py` shows some other observable also moves (a status field, a point count,
  a peak current near zero for an open cell), add it to `_report_segment_health`.
- If a fault is invisible in **every** observable, record that here — it means the driver cannot
  detect that case and the SOP has to carry it instead.

### G. Lost instrument — `device_lost()`
`pump()` and `_poll_to_completion()` both check `AutolabConnection.IsConnected`.

- Confirm from the USB-pull run that it actually flips **during** a run. If it does not, the driver
  cannot detect a vanished Autolab and this must be said out loud rather than assumed working — the
  Gamry version of this caught a truncated file being written silently.

---

## 3. The GUI — WIRED (2026-09-02)

Done, with tests. The Instrument tab has an **Autolab** radio; it is disabled and says why on a
machine without pythonnet, External stays the default everywhere, and a saved `autolab` mode falls
back to External rather than selecting something the machine cannot honour — so loading a settings
file from the Metrohm rig on the Gamry rig cannot disarm it. **Connect Potentiostat** probes
whichever instrument the selected mode names (`probe_identity` for the Gamry, `autolab_identity`
for the Autolab — both read-only, the Autolab one cell-safe).

`tests/test_gui_layout.py` covers the default, the three-way round trip, the fallback, the `.DTA`
checkbox belonging to Gamry-Python mode alone, and that a disabled radio explains itself.

Still to do at the bench:

- [ ] `config/bench.ini` on the rig carries `autolab_sdk`, `autolab_adx`, `autolab_hdw`,
      `autolab_nox_cv`, `autolab_nox_ca`, `autolab_dio_port`. All machine-specific — they are
      deliberately absent from the tracked `config/defaults.ini`, like `data_root`.
- [ ] `potentiostat_mode = autolab` in that same file.
- [ ] A full run on the dummy resistor with real spectra, checked against the 8-column format.

---

## 4. The one thing not to skip

The first run on a **real sample** should be treated as a first run, not a formality. Everything so
far has been validated against a 10 kΩ resistor, which cannot be damaged and has no
electrochemistry. Watch the first doping cycle rather than starting it and walking away.
