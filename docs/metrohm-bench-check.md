# Taking spec-echem to a Metrohm rig — bench check

A start-to-finish order of operations for a machine that has **never run spec-echem**, with a
**Metrohm Autolab** instead of the Gamry. Each step is cheap and answers one question; do them in
order, because a failure early makes everything after it ambiguous.

The probe scripts have their own detailed setup guides — this document says *when* to run them
and what to conclude. It does not repeat their contents:

- `examples/query_avantes_setup.md` — spectrometer probe (installing the AvaSpec-DLL SDK)
- `examples/query_autolab_setup.md` — Autolab connect probe (installing the Autolab SDK + pythonnet)
- `examples/query_avantes_trigger.py` — end-to-end trigger check (Step 4): arms the Avantes and
  pulses the Autolab DIO from one Python process, no NOVA. No separate setup guide — it needs the
  same two SDKs as the probes above.
- `examples/query_autolab_run_setup.md` — can Python *run* electrochemistry (Step 5)? Reflects the
  SDK and, behind an off-by-default flag, holds a potential and runs a procedure. Writes
  `autolab_api_report.txt`, which is the input to writing an Autolab driver.

**First rig brought up this way:** an Autolab PGSTAT10 + AvaSpec-ULS2048L, 2026-08-28. Results,
findings, and what they imply for an Autolab backend are in [`metrohm-rig-status.md`](metrohm-rig-status.md).

---

## The one thing to decide first: bitness

**Use 64-bit Python.** This is the whole reason the order below matters.

The 32-bit/64-bit split in this project was always a **Gamry** constraint, never an Avantes one:
`EchemToolkitPy` is 32-bit-only, `avaspec` is 64-bit, and that conflict is why Python potentiostat
mode and the spectrometer could never share one environment. **A Metrohm rig has no such
constraint** — so if the Autolab SDK and pythonnet both run 64-bit, one interpreter can hold both
instruments, which the Gamry path has never allowed.

That is what Step 3 actually tests, and it decides how much work an Autolab backend would be.

---

## Step 1 — Spectrometer alone

Install the **64-bit** AvaSpec-DLL SDK, then:

```
python examples/query_avantes.py
```

Follow `examples/query_avantes_setup.md` for the install and the `AVASPEC_DLL_DIR` edit. Expect
serial number, pixel count, and wavelength span.

Two things that bite on a fresh install:

- Keep `avaspec.py`, `globals.py`, and `avaspecx64.dll` **together**, and keep the wrapper and the
  DLL a **version-matched pair** — they are released as a set.
- 64-bit Python needs `avaspecx64.dll` (32-bit needs `avaspec.dll`). A mismatch shows up as a
  DLL-load error, not as "no spectrometer".

## Step 2 — Spectrometer, as the application sees it

The probe passing does **not** guarantee the app can import `avaspec`, and on the 9.14.0.0 SDK it
almost certainly won't without help. That `avaspec.py` loads its DLL as
`ctypes.WinDLL("./avaspecx64.dll")` — an explicit relative path, which Windows resolves against the
**current directory**. `import avaspec` therefore works only when you are sitting in the DLL's own
folder. The probe sidesteps this by being run from there.

**Two things were tried against real hardware and do NOT fix it:** `os.add_dll_directory()` (a path
containing a separator bypasses the DLL search order entirely) and preloading the DLL by absolute
path (Windows does not match the `"./..."` request to the already-loaded module). Don't reach for
either — both look correct in the Windows documentation and neither works here.

**What does work** is the vendored-file edit in `examples/query_avantes_setup.md` §2: in your
environment's `site-packages\avaspec.py`, comment out `import globals` and
`from PyQt5.QtCore import *`, and change the DLL load to a **bare name** after an
`os.add_dll_directory(...)`. Once the wrapper loads by bare name, the search path applies and this
variable is honoured:

```
set SPEC_ECHEM_AVASPEC_DLL_DIR=C:\AvaSpecX64-DLL_9.14.0.0
```

(Windows only; unset, it does nothing. The app also preloads the DLL from that folder, which is
enough on its own for a wrapper that already loads by bare name.) Then launch the app and read the
banner at the top of the app log:

```
python -m gui
```

```
  drivers avaspec: yes | toolkitpy: no
```

`toolkitpy: no` is **correct and expected** here — there is no Gamry stack on this machine, and the
app falls back to External mode automatically.

If it says `avaspec: no`, the next line gives the reason:

```
          avaspec import failed: DLL load failed while importing avaspec
```

That is the DLL path (set the variable above). `No module named 'avaspec'` is a different problem —
the wrapper itself is not on `sys.path`. Without that reason line, both look identical to an
unplugged spectrometer.

## Step 3 — Autolab, connect only

**Close NOVA first** — a held link is the most common connect failure.

```
python examples/query_autolab.py
```

Edit the three paths at the top per `examples/query_autolab_setup.md`; the `HDW` hardware-setup XML
is **model-specific**. This probe connects, reads `IsConnected`, and disconnects. It never turns the
cell on, applies a potential, or loads a procedure — connect and cell power are separate operations
in the SDK.

Even so: **first run with the working electrode disconnected, or on a dummy cell.** Standard
precaution for first-time instrument-control code, and the cell-safety claim has not yet been
confirmed against real hardware.

Run it under the **same 64-bit Python as Step 1.** That is the point of the exercise:

| Result | What it means |
|---|---|
| Connects under 64-bit | Both instruments can share one interpreter. An Autolab backend is mostly plumbing behind the existing `Potentiostat` seam. |
| Assembly "could not load it" | `Adk.x` is 32-bit. The split is back, just mirrored (32-bit Autolab / 64-bit avaspec) — pushing toward the same separate-process design the Gamry needed. |

## Step 4 — A real run, in External mode

There is **no Autolab driver** in `spec_echem/`. The path that works today is **External mode**, and
it is genuinely vendor-neutral: the run worker takes `potentiostat=None` and guards every
electrochemical call, so the application does not know or care who drives the cell. You start the
procedure in NOVA; spec-echem arms the Avantes and collects triggered spectra exactly as always.

Two consequences to plan around:

1. **Python writes no electrochemical file.** NOVA saves its own data. The Results tab reads
   Gamry-format cleaned `.txt` (`spec_echem/gamry_data.py`), so Autolab output will not load there —
   spectra in the app, echem alongside it.
2. **The trigger is the open question.** The sync is a digital-out edge into **Avantes DB26 pin 6**
   (on the Gamry rig, DIGOUT0 — see `docs/sop.md` §2.1). The Autolab SDK wrapper exposes **no
   digital I/O at all**, so whether this Autolab has a usable digital-out line — or whether a `.nox`
   procedure can toggle one — is unconfirmed. Check the back panel before counting on synced spectra.

### If there is no trigger line

Create or edit **`config/bench.ini`** (machine-specific and gitignored — *not* `defaults.ini`, which
is the lab-wide standard and will collide on the next `git pull`):

```ini
[bench]
trigger = false
```

Acquisition then free-runs: start the spectra, start the procedure, correlate by wall clock. You
lose the hardware time zero, which is the point of the rig — but it gets you a first co-acquisition
rather than a dead afternoon.

---

## Step 5 — Can Python run the electrochemistry?

Only needed if you are heading toward a Python-driven Autolab backend rather than External mode.
Steps 1–4 prove the *trigger*; this one asks whether Python can drive the cell as well, which is
what `spec_echem/potentiostat.py` would need.

**Full directions: [`../examples/query_autolab_run_setup.md`](../examples/query_autolab_run_setup.md)**
— what to set, how to read each answer, and what to bring back.

```
python examples\query_autolab_run.py
```

Out of the box it **only connects and reflects** — it lists what the SDK exposes and never
energizes anything. That alone answers most of the design questions. To go further, set `NOX` to a
NOVA procedure and flip `ENERGIZE_CELL = True` — **with a dummy cell or test resistor**, never a
real sample.

It writes `examples/autolab_api_report.txt`. **Commit and push that file**; it is what the driver
gets written from, and it saves retyping API listings between machines.

The two answers that matter most:

- **Can a loaded `.nox`'s parameters be overridden from Python?** The doping potential increments
  every cycle, so if not, the procedure route needs one file per cycle — or isn't viable.
- **Does `Measure()` block?** That decides whether the driver needs its own thread.

---

## What a good day looks like

1. `query_avantes.py` reports the spectrometer.
2. The launch banner says `avaspec: yes`.
3. `query_autolab.py` connects under the same 64-bit Python, cell untouched.
4. One External-mode run producing spectra, triggered if the digital-out exists, free-running if not.

Steps 1–3 are worth doing even if step 4 has to wait — they are what tell you whether an Autolab
backend is a modest job or a hard one.
