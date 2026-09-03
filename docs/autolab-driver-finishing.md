# Finishing the Autolab driver — what to fill in after the bench tests

> **STATUS 2026-09-03 — bench items A–G done.** All four `bench_autolab_*.py` scripts ran on the
> rig; `docs/autolab-run-api.md §4` carries the results and `AutolabPotentiostat` is wired to them
> (commit after this doc). What changed in the driver:
> - **`set_param` int-index bug fixed** — `list(cmd.CommandParameters)[i]`, not `[i]` (SDK rejects a
>   bare int). Was latent in `_set()` and `_wait_window()`.
> - **CA map filled in** — `CA_RECORDER_COMMAND` / `CA_SETPOINT_COMMAND` / `CA_IDX_*`. The hold
>   potential is on a *separate* `FHSetSetpointPotential` command, and the stock `.nox` is a
>   **3-step** template — `_neutralise_extra_ca_steps()` zeroes the extra FHLevel durations.
> - **Reload-per-segment is now load-bearing** — a reused procedure object is INERT (§4.2), not just
>   "might accumulate".
> - **Open-cell detection added** — `_warn_if_current_never_rose()`; overload flags never fire for an
>   open cell (§4.4), only `max|I|` in the noise gives it away.
> - **`autolab_trigger_in_procedure`** — new flag; when the `.nox` has an `FHDIO` step the Autolab
>   fires P1.A itself and `fire()` skips the Python pulse.
>
> **DRIVER RAN ON HARDWARE 2026-09-03** — `examples/bench_autolab_driver.py` drives the real
> `AutolabPotentiostat` class (open / prepare / fire / pump / finish / last_data) through one CV
> and one doping segment on the 10 kΩ dummy. Both come back matching Ohm's law: CV 1640 pts ±1 V
> 101 µA; doping 80 pts, held at +0.300 V, 30 µA (= 0.30 V / 10 kΩ), and `_neutralise_extra_ca_steps`
> confirmed working (one hold, ~14 s, vs ~26 s for the un-neutralised 3-step template). Positional
> command-list indexing therefore works. `config/bench.ini` on this rig now has the `[autolab]`
> section and `potentiostat_mode = autolab`; corrected trigger skew is −51 ms with
> `autolab_pulse_delay_s = 5.95`.
>
> **Still need the rig:** a full run through the GUI with real spectra, checked against the
> 8-column format (watch the first one); the first real-sample run; and, for the last of the skew,
> a `.nox` with an `FHDIO` step so the edge leaves Python's timing path (§4.5) — not required to
> operate, ~50 ms improvement.
>
> **First GUI run — 2026-09-03, STALLED, not yet resolved.** `python -m gui` → Autolab mode → a
> CV-only experiment. Two findings:
> 1. `gui/tabs/run_tab.py::on_start` referenced an undefined `python_mode` → `NameError` on Start
>    for *every* mode. **Fixed** (commit 1487af4) — defined once, `autolab` counts as Python-driven.
> 2. After the fix, the run started (`20260903_test2`) but never finished — no spectra/echem files
>    written. Most likely cause: **`scan_averages = 200`** (saved into `config/bench.ini` by "Save
>    as defaults" after a Linearity Check) × 2.64 ms integration ≈ **530 ms per spectrum**, and the
>    CV segment wanted 241 spectra at `delta_time = 0.1 s` → the run needs 2+ minutes of collection,
>    which looked like a hang. Not confirmed hung vs slow. Secondary suspect: whether
>    `AvantesSpectrometer.set_trigger_mode(1)` alone puts the detector in external-edge mode — the
>    bench scripts set `m_Trigger_m_Source`/`m_Trigger_m_SourceType` explicitly, `set_trigger_mode`
>    only sets `m_Trigger_m_Mode`. **Next session:** drop `scan_averages` to ~1–5 for timed
>    segments, re-run, capture the full `{folder}/{folder}.log`, and confirm spectrum 0 gets the
>    trigger. `examples/bench_autolab_fullrun.py` runs the same pipeline headless for faster
>    iteration.

`AutolabPotentiostat` (`spec_echem/potentiostat.py`) is written and test-covered. This is the
checklist for turning it from "correct as far as we know" into "correct".

The design rule throughout: every unresolved point is a **named constant or an explicit stub**, so
finishing is filling in blanks rather than auditing assumptions. Nothing below is buried in logic.

## Starting a Claude Code session on the rig

Claude Code is installed on the Win11 box. Run it from the repo root (`claude`) in the Anaconda
Prompt with the `SpecEchem` env active, so any Python it runs is the interpreter you have been
testing with. It loads `CLAUDE.md` automatically. Paste this to start it warm:

> I'm at the UW Metrohm rig on the Win11 box: Autolab PGSTAT302N + AvaSpec-ULS2048L, 64-bit
> SpecEchem env. A 10 kΩ 1% dummy resistor is available (2-electrode: W+WS one leg, RE+CE the
> other). Read `docs/autolab-driver-finishing.md`, `docs/autolab-run-api.md` and
> `examples/bench_autolab_fault_setup.md`.
>
> `AutolabPotentiostat` and the GUI wiring are written and test-covered but were written before the
> bench scripts ran. Today: run the four `examples/bench_autolab_*.py` scripts (their
> `ENERGIZE_CELL = False` phases first — those energize nothing), then fill in the CA parameter map
> (`CA_COMMAND` / `CA_IDX_*` in `spec_echem/potentiostat.py`), which is the one thing blocking
> doping/dedoping/pre-dedoping.
>
> Constraints: dummy resistor only, never a real sample; adopt a parameter index only if the bench
> script reports it CONFIRMED; do not change the External or Python (Gamry) paths — that is a
> working rig. Confirm the build id first, commit and push what we learn, and update
> `docs/autolab-run-api.md` §4 with the answers.

If it starts a long piece of work, remind it the suite is `python -m pytest tests/ -q` and should
stay green (206 tests, 1 skipped as of `8a10323`).

---

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

- [x] `config/bench.ini` on the rig carries the `[autolab]` section (`autolab_sdk`, `autolab_adx`,
      `autolab_hdw`, `autolab_nox_cv`, `autolab_nox_ca`, `autolab_dio_port`, `autolab_pulse_delay_s`,
      `autolab_trigger_in_procedure`). Done 2026-09-03.
- [x] `potentiostat_mode = autolab` in that same file. Done 2026-09-03.
- [x] Driver class exercised on hardware — `examples/bench_autolab_driver.py`, CV + doping on the
      dummy, both match Ohm's law.
- [ ] A full run through the **GUI** with real spectra, checked against the 8-column format.
- [ ] `autolab_nox_cv` currently points at the stock template. Switch to
      `spectroelectrochem_CV.nox` (Sung-Joo's — fixed current range, no `FHPreCurrentRangingCV`)
      once it is confirmed to run via the SDK; it is the protocol-equivalent base.

---

## 4. The one thing not to skip

The first run on a **real sample** should be treated as a first run, not a formality. Everything so
far has been validated against a 10 kΩ resistor, which cannot be damaged and has no
electrochemistry. Watch the first doping cycle rather than starting it and walking away.
