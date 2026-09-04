# Finishing the Autolab driver — what to fill in after the bench tests

## ▶ PLAN FOR THE NEXT UW TRIP

**Goal: one complete co-acquisition run that writes valid files on the 10 kΩ dummy, then an old
sample.** Not a good sample. The driver has never produced a full set of run files; that is the
gap, and everything below is ordered to close it in the fewest instrument-minutes.

### Before you go — pre-flight, no instrument needed

```
git pull
python -c "from spec_echem.build_info import build_id; print(build_id())"
```

Then fix the thing that most likely caused the 2026-09-03 stall. **`scan_averages = 200` cannot
work on this detector.** The arithmetic:

```
per spectrum  = integration_time_ms x scan_averages   <- WALL CLOCK, not exposure
CV slot       = cv_step_size / cv_scan_rate           <- DERIVED, not a free setting
chrono slot   = chrono_delta_time
```

**"Per spectrum" is how long one averaged spectrum takes to collect — it is NOT the integration
time.** The integration time does not change here: it stays wherever the Linearity Check put it
(~2.64 ms on this rig, for ~85% ADC fill). Setting *that* to 53 ms would over-expose by ~20× and
clip every peak flat. `scan_averages` only decides how many of those 2.64 ms readouts get averaged
into one spectrum, and therefore what the spectrum costs in wall clock.

At this rig's ~2.64 ms integration, 200 averages is **528 ms** per spectrum against a **100 ms**
slot (10 mV step / 100 mV/s). Set **`scan_averages = 20`** in `config/bench.ini` → 53 ms, which
fits both slots with margin.

| averages | per spectrum (2.64 ms each) | fits a 100 ms slot? |
|---|---|---|
| 200 | 528 ms | no — 5× over |
| 50 | 132 ms | no |
| 20 | 53 ms | **yes** |
| 5 | 13 ms | yes |

That is a real tradeoff, not free: optical S/N falls as √N, so 200 → 20 costs about 3.2×. If the
spectra are too noisy, **raise `cv_step_size`** (a coarser CV, a longer slot) rather than putting
averages back — the CV slot is derived from step ÷ rate, so a 20 mV step at 100 mV/s gives a 200 ms
slot and room for ~70 averages. Coarser potential resolution, better optical S/N. Your call which
matters more for the sample.

`acquire_segment()` now warns at the start of every segment if it still does not fit, naming both
numbers — so this cannot silently repeat.

### Step 1 — headless full run, dummy resistor

```
python examples\bench_autolab_fullrun.py
```

Same pipeline as the GUI with none of the GUI's variables, so iterate here. In the log, want to see:

- **no cadence warning** (the pre-flight worked)
- **`neutralised N extra CA hold step(s)`** — proves the 3-step stock CA template is tamed; without
  it a real sample gets driven to 0 V for ~10 s after every hold
- **no open-cell warning** — peak current should be ~100 µA at 1 V through the 10 kΩ
- `Run finished: done.`

### Step 2 — check the files it wrote

Against [`data-format.md`](data-format.md): 8 columns in the spectra files, and the echem `.txt`
(`CV.txt`, `steps(N).txt`, …). Compare shapes against `tests/golden/`. **This has never been done
for the Autolab path** — it is the actual acceptance test, more than any single instrument reading.

### Step 3 — the same run through the GUI

`python -m gui`, Autolab mode, same experiment. Files should match Step 1's. This is where
2026-09-03 failed; the `NameError` is fixed and now smoke-tested, so it should reach acquisition.

### Step 4 — an old sample

Only once Steps 1–3 are clean. **Watch the first doping cycle** rather than starting it and walking
away. Stop if you see: the open-cell warning, an overload warning, a cadence warning, or spectra
that look nothing like the dummy run's shape.

### Step 3.5 — a clean single-step CA `.nox` (worth doing, AFTER Steps 1–3)

The stock `Chrono amperometry.nox` is a **three**-hold template and spec-echem wants one.
`_neutralise_extra_ca_steps()` zeroes the extras and was verified on hardware (14 s vs 26 s) — but
it is a guard over a template that is wrong for the purpose. If it ever silently fails on a real
sample, the cell is driven to steps 2–3's default 0 V for ~10 s after **every** hold: partial
de-doping between segments, arriving as data rather than as an error. Deleting the extra steps
removes the failure mode instead of guarding it.

**Order matters.** Do it after a complete run on the stock template, not before — neutralisation is
proven, so that gives you a known-good baseline to compare against. Then swap the clean template in
and re-run Step 1. If NOVA fights you, you still have a working pipeline.

**Build it by deleting from the stock `Chrono amperometry.nox`.** Not from scratch, and not from
`Chrono amperometry fast.nox` — that one uses a different `Levels` / `LevelShortSetpoint` model
(looked at on 2026-08-31 and rejected), and starting there breaks the parameter map silently. The
driver addresses `FHSetSetpointPotential` for the hold potential, and `FHLevel` with **duration at
index 1, interval at index 0**. Keep those two commands intact and the map still holds.

Confirm it worked two ways:

```python
proc = inst.LoadProcedure(r"...\your_single_step_CA.nox")
print(list(proc.Commands.IdNames))     # exactly one FHSetSetpointPotential, one FHLevel
```

and in the run log, the line **`neutralised N extra CA hold step(s)` disappears** — there are no
extras left to find. Then point `autolab_nox_ca` at it in `config/bench.ini`.

### Deliberately NOT on this trip

- **`FHDIO` / trigger-in-procedure — and only this.** The command could not be found in NOVA, the
  `PC_Spectral*` procedures could not be found on disk or in the database, and the Python pulse
  already measures **−51 ms**, which is fine for 100 ms spectra. It refuses cleanly with an
  explanatory message if misconfigured, so nothing is at risk by leaving it. This is *adding* a
  command nobody can locate for ~50 ms; the CA cleanup above is *deleting* two steps you already
  have, and is worth the time. Different jobs — don't let deferring one defer the other.

### If it stalls again

1. Is there a **cadence warning** in the log? → the pre-flight arithmetic is still wrong.
2. Did **spectrum 0** ever land? → a trigger problem, not a speed problem. `query_avantes_trigger.py`
   still passing isolates the cable.
3. Neither? → capture `{folder}/{folder}.log` in full and push it; the run log records per-segment
   cadence and every warning.

Remember files are only written at **segment end** — a slow segment looks exactly like a hang.

---

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
>
> **Follow-up 2026-09-03 (Mac side, `60422ab`):**
> - **Suspect 2 is ruled out.** `_create_measurement_config` already sets
>   `m_Trigger_m_Source = 0` (external) and `m_Trigger_m_SourceType = 0` (edge) at init, so
>   `set_trigger_mode(1)` flipping only `m_Trigger_m_Mode` **is** sufficient. No bench time needed.
> - **It was probably slow, not hung.** 241 x ~528 ms is ~127 s, and files are only written at
>   *segment end* — so anything under ~2.5 minutes looks exactly like a hang.
> - **The cadence problem is worse than slowness, and is now warned about.** `acquire_segment()`
>   paces only *down* to `delta_time`; a slower measurement just runs slower, silently. At 528 ms
>   against a 100 ms `delta_time` the CV segment runs ~127 s while the CV itself finishes in ~40 s,
>   so most of its spectra record a cell that has already stopped — in a file that looks completely
>   normal. `_warn_if_cadence_unachievable()` now says so at the start of every segment, naming both
>   numbers. Silent on the Gamry rig (0.088 ms x 200 = 17.6 ms, well inside 100 ms), which is why
>   this stayed latent for years.
> - **`autolab_trigger_in_procedure` can no longer hang a run — it REFUSES.** It previously told
>   `fire()` to stay out of the timing path with nothing checking that the `.nox` actually had a
>   digital-output step; set against the stock template, no edge is ever raised and the armed
>   spectrometer waits forever. `prepare()` now checks and **raises**, naming the commands it did
>   find and both ways to fix it.
>
>   It deliberately does **not** fall back to a Python pulse (Dean's call, and the right one). A
>   fallback keeps the run alive but silently changes what the data means: the Python path needs a
>   measured `autolab_pulse_delay_s`, and someone who configured the procedure to fire has no reason
>   to have tuned one — so the edge would land at whatever stale value is in `bench.ini`, skewing by
>   ~1 s on the untuned default. Plausible, mistimed data reported as success is worse than
>   stopping. The check runs in `prepare()`, before the cell is switched on and before the
>   spectrometer arms, so it costs a clear error rather than a hung run or a disturbed sample.
>
> **Finding the step in NOVA (2026-09-03: Dean could not).** "FHDIO" is *our* shorthand, not
> necessarily NOVA's label — which may be the whole problem. This rig's own `PC_Spectral*`
> procedures already contain the step, rendered there as **`Dio_0` / `HDio`**, written as
> **`P1.A:Write`** and pulsed via `HOptionGetSetValuesPulse` (see `metrohm-rig-status.md`). So don't
> build one from scratch: **open a `PC_Spectral*` procedure, find the step that writes P1.A, and
> copy it** into a copy of the standard CV — positioned after `FHPreCurrentRangingCV` and before
> the staircase. Then confirm what the SDK calls it:
>
> ```python
> proc = inst.LoadProcedure(r"...\your_modified.nox")
> print(list(proc.Commands.IdNames))
> ```
>
> The driver matches any IdName containing "dio" (case-insensitive), so most spellings will work —
> but that printout is the ground truth, and worth recording here.
>
> **This is optional.** The Python-pulse path measures −51 ms skew, which is fine for 100 ms
> spectra. It buys ~50 ms and removes host-timing jitter. A real-sample run matters more; don't
> spend bench time on it.

`AutolabPotentiostat` (`spec_echem/potentiostat.py`) is written and test-covered. This is the
checklist for turning it from "correct as far as we know" into "correct".

The design rule throughout: every unresolved point is a **named constant or an explicit stub**, so
finishing is filling in blanks rather than auditing assumptions. Nothing below is buried in logic.

## Starting a Claude Code session on the rig

Claude Code is installed on the Win11 box. Run it from the repo root (`claude`) in the Anaconda
Prompt with the `SpecEchem` env active, so any Python it runs is the interpreter under test. It
loads `CLAUDE.md` automatically. Paste this:

> I'm at the UW Metrohm rig on the Win11 box: Autolab PGSTAT302N + AvaSpec-ULS2048L, 64-bit
> SpecEchem env, 10 kΩ 1% dummy resistor available (2-electrode: W+WS one leg, RE+CE the other).
>
> Read **`docs/autolab-driver-finishing.md` — the "PLAN FOR THE NEXT UW TRIP" section at the top is
> what we are doing** — plus `docs/autolab-run-api.md` for the proven SDK behaviour. Work that plan
> in order: pre-flight `scan_averages`, then `examples/bench_autolab_fullrun.py` headless on the
> dummy, then check the written files against `docs/data-format.md`, then the same run through the
> GUI, and only then an old sample.
>
> The goal is **one complete run that writes valid files**, not new features. `AutolabPotentiostat`
> and the GUI are written and test-covered; CV and chrono both ran on the instrument on 2026-09-03.
> What has never happened is a full pipeline producing a run folder.
>
> Rules for this session:
> - Dummy resistor until Steps 1–3 are clean. The first sample is an OLD one, and I watch the first
>   doping cycle.
> - **Do not** work on `FHDIO` / `autolab_trigger_in_procedure`. The command cannot be found in
>   NOVA, the `PC_Spectral*` procedures cannot be found at all, and the Python pulse measures
>   −51 ms, which is fine. It is explicitly deferred. This does **not** defer Step 3.5 (a clean
>   single-step CA `.nox`) — that is a separate job and is on the list.
> - Do not change the External or Python (Gamry) paths — that is a working rig at PLU.
> - Diagnose before changing code. A slow segment looks exactly like a hang: files are only written
>   at segment end, and `acquire_segment()` now warns when spectra cannot keep the requested
>   cadence. Check the log for that warning first.
> - Keep `python -m pytest tests/ -q` green (215 passed, 1 skipped as of `88dbd84`).
> - Commit and push what we learn, and update `docs/autolab-run-api.md` / this file with results.
> - At the end, write the session up as `docs/bench-<YYYY-MM-DD>.md` following
>   **"Writing up the session"** below — the three rules there are not stylistic, they each come
>   from something that cost real time to recover.

### Things a fresh session gets wrong — tell it these

Learned the expensive way; worth pasting if it starts down one of these paths:

- **Parameters are addressed BY INDEX.** A `CommandParameter` has no name property on this SDK, and
  `cmd.CommandParameters[i]` with a bare int is *rejected* — use `list(cmd.CommandParameters)[i]`.
- **`LoadProcedure()` returns the Procedure.** There is no `inst.Procedure`.
- **A used procedure object is INERT.** A second `Measure()` on it silently no-ops and `.Signals`
  still holds the previous run. Reload every segment. This is load-bearing, not tidiness.
- **`Measure()` is non-blocking** — poll `proc.IsMeasuring`. No dedicated thread (unlike the Gamry).
- **Scan rate is stored V/s in the SDK** although NOVA's UI shows mV/s.
- **An open cell is invisible** to every status signal — no overload, `IsMeasuring` goes False,
  `.Signals` fills. Only `max|I|` staying in the noise gives it away.
- **"FHDIO" is our shorthand**, not a confirmed NOVA command name. Do not assert it exists.
- The Avantes must be armed for a **hardware** trigger (`m_Trigger_m_Mode = 1`) and fired by a real
  edge. Never substitute a software start for spectrum 0 — an edge before arming is silently
  missed, and a software start throws away the hardware t=0 that the rig exists for.

### Writing up the session

Write `docs/bench-<YYYY-MM-DD>.md` at the end and push it. It complements the per-item updates to
`autolab-run-api.md` §4 rather than replacing them. Three rules, each earned:

**1. Mark every finding as MEASURED or INFERRED.**
The `PC_Spectral*` procedures were written up as fact in four files, and the claim traced back to a
single unverified line — one of the four "confirmations" turned out to be a hardcoded string in our
own probe script, echoed into a results file and read back as evidence. On 2026-09-03 the procedures
could not be found on disk or in NOVA's database. A one-word marker per finding prevents that:

```
MEASURED  CV returned 1640 points, max|I| 1.010e-4 A at +1.000 V (10 kOhm dummy)
INFERRED  the extra CA steps would drive a real sample to 0 V (not observed on a sample)
```

**2. Record what did NOT work, not only what did.**
The dead ends cost the most time and disappear from the record fastest, so they get re-tried.
`os.add_dll_directory()` for avaspec, the absolute-path DLL preload, "the Autolab SDK has no digital
I/O" — every one was plausible, every one was wrong, and each was attempted more than once because
nobody had written down that it had already failed. A "Tried and rejected" section is worth as much
as the results.

**3. Paste the actual numbers, not a description of them.**
`1640 pts, ±1.000 V, max|I| 1.010e-4 A` can be checked against Ohm's law by someone who was not
there. "The CV looked correct" cannot. Include point counts, peak currents, `CalcTime[0]`, the
cadence line from the run log, the skew, and the exact text of any warning or error. Raw log
excerpts beat prose summaries of log excerpts.

If a run failed, that write-up is more valuable than a successful one — say what was expected, what
happened, and what was ruled out.

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
