# Finishing the Autolab driver

`AutolabPotentiostat` (`spec_echem/potentiostat.py`) is written, test-covered, and has run on the
instrument: CV and chronoamperometry both drove a 10 kΩ dummy correctly on 2026-09-03. **What has
never happened is a full pipeline producing a run folder.** Everything in the plan below is ordered
to close that gap in the fewest instrument-minutes.

Read this file top-down. The plan is what to do; the reference section is what is proven; the
appendix is history — kept because it records what was *tried and rejected*, but it is history, not
a task list.

---

## ▶ PLAN FOR THE NEXT UW TRIP

**Goal: one complete co-acquisition run that writes valid files on the 10 kΩ dummy, then an old
sample.** Not a good sample.

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
fits both slots with margin. *(Set to 20 on the rig box 2026-09-04 — confirm it is still 20 before
you run, since "Save as defaults" after a Linearity Check rewrites this file.)*

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

`acquire_segment()` warns at the start of every segment if it still does not fit, naming both
numbers — so this cannot silently repeat.

**This pre-flight is tested at Step 3, not Step 1.** `bench_autolab_fullrun.py` sets its own
`scan_averages = 1` and its own CV, so it runs clean whatever `bench.ini` says. The GUI is what
reads `bench.ini`, and the GUI is where the stall happened.

### Step 1 — headless full run, dummy resistor

```
python examples\bench_autolab_fullrun.py
```

Same pipeline as the GUI (`build_segments` → `run_one_segment` per segment) with none of the GUI's
variables, so iterate here. **It is a file-shape test, not a cadence test:** the script forces
`scan_averages = 1` (~2.6 ms per spectrum) and a short CV — 5 mV at 500 mV/s, a 10 ms slot — plus
0.25 s chrono slots, so no cadence warning is expected regardless of `bench.ini`.

In the log, want to see:

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

This step, unlike Step 1, runs on `bench.ini`'s real `scan_averages`. **Check the log for the
cadence warning before you blame anything else** — if it is there, the pre-flight number is still
wrong.

Then, at the end of a *dummy* run, **press Abort once** and start the next segment. The SDK side of
this is measured (`autolab-run-api.md` §4.3: `Abort()` settles in ~1.3 s, `.Signals` comes back
completely empty, the next run is full-length with no reconnect) — what has never run on hardware
is the driver's own path from the GUI button through `stop()`. Ten seconds to check, and Abort is a
button students will press.

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

**Two candidate bases, both by deleting rather than building from scratch.** Not from
`Chrono amperometry fast.nox` — that one uses a different `Levels` / `LevelShortSetpoint` model
(looked at on 2026-08-31 and rejected), and starting there breaks the parameter map silently.
Whichever base you take, the driver addresses `FHSetSetpointPotential` for the hold potential and
`FHLevel` with **duration at index 1, interval at index 0**. Keep those two commands intact and the
map still holds.

- **The stock `Chrono amperometry.nox`** — delete holds 2 and 3. Known parameter map, nothing else
  in the procedure, lowest risk.
- **`PC_SpectralChronoAmperometry_0.36-0.8V.nox`** (in the NOVA procedures folder, see below) —
  Dean's route, and the more interesting one: it is a *working* spectro-EC procedure, so it already
  carries the P1.A trigger pulse. But it also drives the Avantes itself
  (`ExecCommandAvantesStart` / `SpectroTriggered` / `HOptionGetSpectrum`) and NOVA and spec-echem
  **cannot both own the spectrometer over USB** — so every Avantes command has to come out, plus
  the CV steps (`FHCyclicVoltammetry` strings are present) and the extra holds. Re-print
  `Commands.IdNames` and re-verify the two parameter indices afterwards: this base has not been
  through the SDK, and a silently different index runs the wrong potential.

Either way, do the 2-minute read-only SDK check below before committing to a base.

Confirm it worked two ways:

```python
proc = inst.LoadProcedure(r"...\your_single_step_CA.nox")
print(list(proc.Commands.IdNames))     # exactly one FHSetSetpointPotential, one FHLevel
```

and in the run log, the line **`neutralised N extra CA hold step(s)` disappears** — there are no
extras left to find. Then point `autolab_nox_ca` at it in `config/bench.ini`.

### Step 3.6 — swap the CV template (optional, same rule as 3.5)

`autolab_nox_cv` still points at the stock `Cyclic voltammetry.nox`. Sung-Joo's
`spectroelectrochem_CV.nox` is the protocol-equivalent base — fixed current range, no
`FHPreCurrentRangingCV` — which would also remove the ~50 ms of ranging wobble that sets the
current trigger skew. **Located 2026-09-04** in the NOVA procedures folder (below), alongside a
`spectroelectrochem_doping.nox`. A byte-level string scan shows no `FHPreCurrentRangingCV` (as
expected) and **no DIO strings either** — so this base does not solve the trigger, it only removes
the ranging gap. It has never been run through the SDK, and the parameter map is only known for the
stock template, so:

- do it **after** Steps 1–3 are clean, never before;
- `print(list(proc.Commands.IdNames))` first, and check the CV command and its parameter indices
  still match `CV_IDX_*` in `potentiostat.py`;
- re-run Step 1 against it and compare the files to the known-good baseline.

Skip it without regret if time is short — the stock template works.

### Step 4 — an old sample

Only once Steps 1–3 are clean. **Watch the first doping cycle** rather than starting it and walking
away. Stop if you see: the open-cell warning, an overload warning, a cadence warning, or spectra
that look nothing like the dummy run's shape.

### Deliberately NOT on this trip

- **`FHDIO` / trigger-in-procedure.** The command could not be found in NOVA, the `PC_Spectral*`
  procedures could not be found on disk or in the database, and the Python pulse already measures
  **−51 ms**, which is fine for 100 ms spectra. It refuses cleanly with an explanatory message if
  misconfigured, so nothing is at risk by leaving it. This is *adding* a command nobody can locate
  for ~50 ms; the CA cleanup above is *deleting* two steps you already have, and is worth the time.
  Different jobs — don't let deferring one defer the other. Background in the appendix.
- **Re-running the USB-pull test.** It wedged the SDK session last time and needed a power cycle
  (`autolab-run-api.md` §4.7). The limitation is recorded; re-testing costs bench time and a reboot
  to learn nothing new.

### If it stalls again

1. Is there a **cadence warning** in the log? → the pre-flight arithmetic is still wrong.
2. Did **spectrum 0** ever land? → a trigger problem, not a speed problem. `query_avantes_trigger.py`
   still passing isolates the cable.
3. Neither? → capture `{folder}/{folder}.log` in full and push it; the run log records per-segment
   cadence and every warning.

Remember files are only written at **segment end** — a slow segment looks exactly like a hang.

---

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
> - The appendix of that file is history, not a task list. Items A–G are resolved; do not re-run the
>   bench scripts that closed them.
> - Diagnose before changing code. A slow segment looks exactly like a hang: files are only written
>   at segment end, and `acquire_segment()` warns when spectra cannot keep the requested cadence.
>   Check the log for that warning first.
> - Keep `python -m pytest tests/ -q` green (215 passed, 1 skipped as of `5c00f38`).
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
- **`IsMeasuring` lies on a dead link** — it stuck True through a mid-run USB pull. The poll loop's
  wall-clock ceiling is the only thing that ends that run.
- **"FHDIO" is our shorthand**, not a confirmed NOVA command name — and the evidence now says the
  trigger is not a top-level command at all, but an **option on one** (`Dio_0` / `HDio` /
  `HOptionGetSetValuesPulse`). If so, the driver's `autolab_trigger_in_procedure` check, which
  scans `proc.Commands.IdNames` for "dio", would not see it and would refuse a procedure that does
  in fact fire. Settle it with the `IdNames` printout before touching that flag.
- The Avantes must be armed for a **hardware** trigger (`m_Trigger_m_Mode = 1`) and fired by a real
  edge. Never substitute a software start for spectrum 0 — an edge before arming is silently
  missed, and a software start throws away the hardware t=0 that the rig exists for.

### Writing up the session

Write `docs/bench-<YYYY-MM-DD>.md` at the end and push it. It complements the per-item updates to
`autolab-run-api.md` §4 rather than replacing them. Three rules, each earned:

**1. Mark every finding as MEASURED or INFERRED.**
The `PC_Spectral*` procedures were written up as fact in four files, and the claim traced back to a
single unverified line — one of the four "confirmations" turned out to be a hardcoded string in our
own probe script, echoed into a results file and read back as evidence. A one-word marker per
finding prevents that.

The sequel makes the same point from the other side. On 2026-09-03 those procedures "could not be
found on disk or in NOVA's database", and that got written down too — but on 2026-09-04 they were
sitting in `~\Documents\Nova 2.1\Procedures\`, the exact path `metrohm-rig-status.md` had
recorded on 2026-08-28. **A failed search is not a finding.** Write it as "looked in X and Y, did
not find it", never as "it does not exist".

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

---

## Reference — the driver as it stands

Design rule throughout: every unresolved point is a **named constant or an explicit stub**, so
finishing is filling in blanks rather than auditing assumptions. Nothing is buried in logic.

### Proven on hardware (2026-09-03, 10 kΩ 1% dummy)

`docs/autolab-run-api.md` §4 is the authority; this is the index.

| what | result | where |
|---|---|---|
| CV parameter map | `CV_IDX_*`; crossings = `2 * cv_cycles` | §4.1 CONFIRMED |
| CA parameter map | `CA_RECORDER_COMMAND` / `CA_SETPOINT_COMMAND` / `CA_IDX_*` | §4.6 CONFIRMED |
| Reload per segment | a reused procedure object is INERT | §4.2 MEASURED |
| Abort | settles ~1.3 s, `.Signals` empty, next run full, no reconnect | §4.3 MEASURED |
| Trigger + skew | DIO→Avantes from one process; −51 ms at `autolab_pulse_delay_s = 5.95` | §4.5 MEASURED |
| Driver class end to end | CV 1640 pts ±1 V 101 µA; doping 80 pts at +0.300 V, 30 µA | `examples/bench_autolab_driver.py`, Ohm's law ✓ |

`config/bench.ini` on the rig carries the `[autolab]` section (`autolab_sdk`, `autolab_adx`,
`autolab_hdw`, `autolab_nox_cv`, `autolab_nox_ca`, `autolab_dio_port`, `autolab_pulse_delay_s`,
`autolab_trigger_in_procedure`) and `potentiostat_mode = autolab`.

### Limits the driver cannot detect its way out of

- **An open cell is invisible** to every SDK status field — 1640 points, `IsMeasuring` → False,
  `IsConnected` True, no overload flag. Only `max|I|` moves (101 µA → 22 nA). Hence
  `_warn_if_current_never_rose()`; it is the *only* handle, and it is a heuristic. A
  complete-looking file can carry no electrochemistry — the SOP has to say so.
- **A mid-run USB loss wedges the SDK session** (§4.7): `IsMeasuring` stuck True, the Adk.x
  subprocess floods native errors Python cannot suppress, reconnecting the cable did not recover
  it, Ctrl-C plus a power cycle did. The driver's poll loop has a wall-clock ceiling
  (`_max_wait` = 3× the segment's expected duration + `AUTOLAB_MAX_WAIT_MARGIN_S`) so a run ends
  rather than hanging forever, and it checks `AutolabConnection.IsConnected` — but §4.7's
  recommendation, *kill the Adk.x process* on expiry, is **not implemented**, and `Abort()` on a
  wedged link is INFERRED not to help. Treat a vanished Autolab as "power-cycle and re-run", not as
  something the software recovers from.
- **Whether `Ei.CurrentOverload` fires for a genuine overload is unproven** (§4.4). The stock CV's
  `FHPreCurrentRangingCV` auto-ranges, so a software-induced overload could not be staged. `pump()`
  samples both overload flags every spectrum and `finish()` reports them — belt-and-braces, since an
  overloaded run otherwise completes looking ordinary.

### What the tests do and don't prove

`tests/test_potentiostat.py` drives the driver against `fakes.FakeAutolab`, which encodes the same
understanding of the SDK that the driver does. A green suite proves internal consistency and catches
regressions. It **cannot** catch a misreading of the SDK — if the fake is wrong in the same way the
driver is, both agree and the tests pass. Only the instrument settles that.

### The one thing not to skip

The first run on a **real sample** should be treated as a first run, not a formality. Everything so
far has been validated against a 10 kΩ resistor, which cannot be damaged and has no
electrochemistry. Watch the first doping cycle rather than starting it and walking away.

---

## Appendix — history (resolved; do not re-run)

### The A–G bench checklist — all closed 2026-09-03

Four bench scripts (`bench_autolab_cv.py`, `bench_autolab_ca.py`, `bench_autolab_coacquire.py`,
`bench_autolab_fault.py`) closed the open questions the driver was written around. Results are in
`autolab-run-api.md` §4; the driver was wired to them the same day.

| was | question | outcome |
|---|---|---|
| A | `CV_IDX_CROSSINGS` — is 2 crossings one cycle? | Yes. 4 → 3280 pts, exactly 2× the 1640 at 2; `ScanNumber` 1→2. Driver writes `2 * cv_cycles`. |
| B | is the per-segment reload needed? | **Load-bearing.** A reused procedure object is inert; a reload is the only clean reset. Comment at `potentiostat.py:497`; `test_every_segment_reloads_the_procedure` guards it. |
| C | does `stop()` need a reconnect? | **No.** `Abort()` settles in ~1.3 s, `.Signals` is empty (never a truncated partial), the next run is full-length. `stop()` is correct as written. |
| D | the CA map — was **the blocker** | Filled in: potential on `FHSetSetpointPotential[0]`, duration `FHLevel[1]`, interval `FHLevel[0]`, `FHLevel[2]` a bool left alone. Stock template is 3-step → `_neutralise_extra_ca_steps()`. Covered by `test_chrono_parameters_are_written_to_the_right_commands` and `test_prededoping_uses_its_own_hold_time_and_potential`. |
| E | trigger delay / `_wait_window()` | Skew +988 ms with the pulse at `FHWait`; corrected to **−51 ms** with `autolab_pulse_delay_s = 5.95`. The residual is `FHPreCurrentRangingCV` wobble. |
| F | fault detection | Open cell invisible to every flag; only `max|I|` moves → `_warn_if_current_never_rose()`. Overload flags kept as belt-and-braces. See "Limits" above. |
| G | `device_lost()` / USB pull | Partially. `IsConnected` never got a chance to act — `IsMeasuring` stuck True and the session wedged. See "Limits" above; do not re-run. |

### Driver changes made when those results landed (2026-09-03)

- **`set_param` int-index bug fixed** — `list(cmd.CommandParameters)[i]`, not `[i]` (the SDK rejects
  a bare int). Was latent in `_set()` and `_wait_window()`.
- **CA map filled in**, as above.
- **Reload-per-segment documented as load-bearing**, not merely tidy.
- **Open-cell detection added** — `_warn_if_current_never_rose()`.
- **`autolab_trigger_in_procedure`** — new flag; when the `.nox` has a digital-output step the
  Autolab fires P1.A itself and `fire()` skips the Python pulse.

### The first GUI run — stalled 2026-09-03, diagnosed off-instrument

`python -m gui` → Autolab mode → a CV-only experiment. Two findings:

1. `gui/tabs/run_tab.py::on_start` referenced an undefined `python_mode` → `NameError` on Start for
   *every* mode. **Fixed** (commit `1487af4`) — defined once, `autolab` counts as Python-driven.
2. After the fix the run started (`20260903_test2`) but never finished. Diagnosed on the Mac side
   (commit `60422ab`):
   - **It was probably slow, not hung.** `scan_averages = 200` × 2.64 ms ≈ 528 ms per spectrum ×
     241 spectra ≈ 127 s, and files are only written at *segment end* — so anything under ~2.5
     minutes looks exactly like a hang.
   - **The cadence problem is worse than slowness.** `acquire_segment()` paces only *down* to
     `delta_time`; a slower measurement just runs slower, silently. At 528 ms against a 100 ms
     `delta_time` the CV segment runs ~127 s while the CV itself finishes in ~40 s, so most of its
     spectra record a cell that has already stopped — in a file that looks completely normal.
     `_warn_if_cadence_unachievable()` now says so at the start of every segment, naming both
     numbers. Silent on the Gamry rig (0.088 ms × 200 = 17.6 ms, well inside 100 ms), which is why
     this stayed latent for years.
   - **The trigger-mode suspicion was ruled out without bench time.** `_create_measurement_config`
     already sets `m_Trigger_m_Source = 0` (external) and `m_Trigger_m_SourceType = 0` (edge) at
     init, so `set_trigger_mode(1)` flipping only `m_Trigger_m_Mode` **is** sufficient.
   - **`autolab_trigger_in_procedure` can no longer hang a run — it REFUSES.** It previously told
     `fire()` to stay out of the timing path with nothing checking that the `.nox` actually had a
     digital-output step; set against the stock template, no edge is ever raised and the armed
     spectrometer waits forever. `prepare()` now checks and **raises**, naming the commands it did
     find and both ways to fix it. It deliberately does **not** fall back to a Python pulse (Dean's
     call, and the right one): a fallback keeps the run alive but silently changes what the data
     means — the Python path needs a measured `autolab_pulse_delay_s`, and someone who configured
     the procedure to fire has no reason to have tuned one, so the edge would land at whatever
     stale value is in `bench.ini`, skewing by ~1 s on the untuned default. Plausible, mistimed data
     reported as success is worse than stopping. The check runs in `prepare()`, before the cell is
     switched on and before the spectrometer arms, so it costs a clear error rather than a hung run
     or a disturbed sample.

### `FHDIO` — the procedures are found; what is still unknown

Still deferred for the trip (the Python pulse at −51 ms is fine for 100 ms spectra), but the
evidence moved on 2026-09-04 and the next person should not start from "it cannot be found".

**The NOVA procedures folder is `C:\Users\<user>\Documents\Nova 2.1\Procedures\`** — the path
`metrohm-rig-status.md` recorded on 2026-08-28. The 2026-09-03 "could not be found on disk or in the
database" was a **search failure, not an absence**; treat it as retracted.

MEASURED — 2026-09-04, read-only byte inspection of the `.nox` files (no NOVA, no instrument; a
`.nox` is .NET binary serialization, but its type and label strings extract cleanly):

- 84 files there, mostly NOVA's auto-numbered `(1)(2)(3)` duplicates of ~15 distinct procedures.
- `PC_SpectralChronoAmperometry_0.36-0.8V.nox` contains `HDio`, `Dio_0`, `DioGroup`, `DioPorts`,
  `P1.A`, and `HOptionGetSetValuesPulse` — the exact rendering `metrohm-rig-status.md` described.
  It also contains `FHSetSetpointPotential`, `FHLevel`, `FHWait`, `FHSwitchCell`,
  `FHCyclicVoltammetry`, and `HOptionGetSpectrum` (it drives the Avantes itself).
- `spectroelectrochem_CV.nox` and `spectroelectrochem_doping.nox` are in the same folder. Neither
  contains any DIO string.

INFERRED — from the shape of those names, not yet from the SDK:

- **The trigger is an `HOption` attached to a command, not a standalone command.** That would
  explain why "FHDIO" cannot be found in NOVA's command list, and it matters: the driver's
  `autolab_trigger_in_procedure` guard scans `proc.Commands.IdNames` for "dio", so an
  option-shaped trigger would be invisible to it and the flag would refuse a procedure that
  actually fires.

The check that settles both, read-only, no cell, ~2 minutes:

```python
proc = inst.LoadProcedure(r"C:\Users\<user>\Documents\Nova 2.1\Procedures\PC_SpectralChronoAmperometry_0.36-0.8V.nox")
print(list(proc.Commands.IdNames))
```

If a "dio"-ish name appears there, the driver's guard already works and the step can be copied into
a CV between `FHPreCurrentRangingCV` and the staircase. If it does not, the guard needs to look at
each command's options instead — worth knowing before anyone enables that flag.

**Caveat for any test that RUNS one of these procedures:** they contain Avantes commands and will
seize the spectrometer over USB. Only `LoadProcedure` + `IdNames` is safe to do while spec-echem
owns the Avantes.
