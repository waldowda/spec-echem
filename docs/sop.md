# spec-echem Standard Operating Procedure

**Version:** 0.2.0
**Audience:** Waldow Lab students, first-time users
**System:** Windows 11 instrument PC (`inst-chem` account)

---

## Overview

This system runs spectroelectrochemistry experiments by coordinating two instruments:

- **Gamry Reference 600 potentiostat** — applies potentials and measures current; raises a hardware
  trigger (DIGOUT0) at the start of each electrochemical step
- **Avantes AvaSpec-VRS2048CL-EVO spectrometer** — collects UV-Vis spectra; receives that trigger and
  begins acquiring. 2048 pixels, optical configuration **300–1100 nm** with a 50 µm slit

> The code isn't tied to these models — the pixel count and wavelength calibration are read from the
> device on connect, and any Gamry that `EchemToolkitPy` supports (e.g. the Interface 1010 series)
> should work in Python mode. But the Reference 600 and the VRS2048CL-EVO are what it has actually
> been run on.

They are synchronized by a **wire**, not by software timing: the Gamry's digital output is
connected to the Avantes trigger input, so the optical and electrochemical data share one start.

**You drive the whole experiment from the GUI** (`python -m gui`) — a four-tab app:

| Tab | What you do there |
|---|---|
| **1. Instrument** | Connect, set integration time / averaging, run the linearity check, collect dark + reference |
| **2. Parameters** | Sample info, data folder, CV and doping/dedoping settings |
| **3. Run** | Start the run and watch it |
| **4. Results** | Review each segment; reload an old run |

The Gamry can be driven two ways, chosen on the Instrument tab:

- **External mode** — you start a `.GSequence` in Gamry Framework yourself. This is the proven
  default and works in a 64-bit environment. See [Appendix B](#appendix-b--gamry-sequence-wizard-external-mode).
- **Python mode** — the app drives the Gamry directly and fires the trigger itself. Requires a
  **32-bit** Python (see §1.3), because Gamry's `EchemToolkitPy` is 32-bit only.

> The original Jupyter-notebook workflow still works and is preserved in
> [Appendix A](#appendix-a--legacy-jupyter-notebook-workflow). New users should use the GUI.

---

## Part 1 — Installation (One-Time Setup)

Complete this once per machine. Skip to Part 2 if the instrument PC is already set up.

### 1.1 Prerequisites

- [ ] Windows 11 PC with the Avantes spectrometer connected via USB
- [ ] Gamry Reference 600 connected via USB, Gamry Framework installed
- [ ] Anaconda installed — **verify the install path** (§1.2)
- [ ] Avantes SDK files (from Avantes; the 64-bit version is `AvaSpecX64-DLL_9.14.0.0`)
- [ ] Access to the spec-echem GitHub repository (ask Dr. Waldow)

### 1.2 Verify the Anaconda Install Path

The Anaconda path on this machine is non-standard. In **Anaconda Prompt**:

```
python -c "import sys; print(sys.executable)"
```

You should see:

```
C:\Users\inst-chem\AppData\Local\anaconda3\python.exe
```

> **Note:** the path is `AppData\Local\anaconda3`, **not** `anaconda3` directly under the user
> folder. You'll need it again in §1.4.

### 1.3 Create the Conda Environment — 64-bit or 32-bit?

**Which one you need depends on how you want to drive the Gamry.**

| You want… | Python | Environment |
|---|---|---|
| Spectrometer only, or **External** Gamry mode | 64-bit | `SpecEchem` (3.13) |
| **Python** Gamry control (`EchemToolkitPy`) | 32-bit | `SpecEchem32` (3.7.x) |

`EchemToolkitPy` is 32-bit-only until Gamry ships 64-bit support (targeted ~Sept 2026). In a
64-bit environment the app **automatically disables Python mode** and falls back to External —
you don't have to do anything, but the Python radio button will be greyed out.

**64-bit (`SpecEchem`):**

```
conda create -n SpecEchem python=3.13
conda activate SpecEchem
pip install numpy matplotlib pandas scipy jupyter PyQt5 qtpy
```

**32-bit (`SpecEchem32`)** — note the PyQt5 pin. Version 5.15.2 is the last release with a
self-contained 32-bit Windows wheel; anything newer pulls `PyQt5-Qt5`, which has no 32-bit build
and will fail to install.

```
set CONDA_FORCE_32BIT=1
conda create -n SpecEchem32 python=3.7
conda activate SpecEchem32
pip install --only-binary=:all: "PyQt5==5.15.2" "PyQt5-sip==12.11.0"
pip install numpy matplotlib pandas scipy qtpy
```

### 1.4 Install avaspec.py (Avantes Python Bindings)

The Avantes SDK includes `avaspec.py`, which is **not pip-installable** — copy it into your
environment and edit it.

**Step A — find site-packages** (with the environment active):

```
python -c "import site; print(site.getsitepackages())"
```

**Step B — copy the file:**

```
Source:      C:\AvaSpecX64-DLL_9.14.0.0\examples\PyQt5_simple\avaspec.py
Destination: …\envs\SpecEchem\Lib\site-packages\avaspec.py
```

**Step C — apply three required edits:**

1. Comment out `import globals`
2. Comment out `from PyQt5.QtCore import *`
3. Fix the DLL loading block so it reads:
   ```python
   import os
   os.add_dll_directory(r"C:\AvaSpecX64-DLL_9.14.0.0")
   lib = WinDLL("avaspecx64.dll")
   ```
   The original uses a relative path that fails when the app isn't launched from the SDK folder.

> **Important:** do not overwrite this file with a fresh SDK copy without reapplying all three edits.

**Step D — verify:**

```
python -c "import avaspec; print('avaspec OK')"
```

### 1.5 Clone and Install spec-echem

```
cd C:\Users\inst-chem\Documents
git clone https://github.com/waldowda/spec-echem.git
cd spec-echem
pip install -e .[gui]
```

### 1.6 Verify

With no hardware connected, this should still succeed:

```
python -c "from spec_echem import AvantesSpectrometer; print('Package import OK')"
```

You can also launch the GUI with **Simulated (no hardware)** ticked on the Instrument tab to click
through the whole app without instruments — useful for learning the layout.

---

## Part 2 — Hardware Setup

### 2.1 Trigger Wiring

| Gamry Ref-600 | Avantes DB26 connector |
|---|---|
| DIGOUT0 | Pin 6 (hardware trigger input) |

This wire should already be in place. If it has been disconnected, ask Dr. Waldow before
reconnecting.

> **TODO — cable preparation.** How this trigger cable is actually *built* (the Gamry-side
> connector and which conductor carries DIGOUT0, the DB26 shell and pin 6 termination, ground /
> shield, and cable length) is not yet written down. It lives only in Dr. Waldow's head and in the
> one cable on the bench — so if it is ever damaged or a second rig is set up, this section is what
> would be needed to rebuild it. **To be added.**

### 2.2 Power-On Order

1. Turn on the Avantes spectrometer (USB to PC)
2. Turn on the Gamry potentiostat (USB to PC)
3. **Turn on the light source and let it warm up ~15 minutes.** Do not set the integration time or
   collect a reference before it has stabilized — the lamp drifts while warming, and everything
   downstream depends on the reference.
4. Log in to Windows, open Anaconda Prompt

---

## Part 3 — Running an Experiment (GUI)

### 3.0 Launch

```
conda activate SpecEchem        (or SpecEchem32 for Python Gamry mode)
cd C:\Users\inst-chem\Documents\spec-echem
python -m gui
```

### 3.1 Instrument Tab — Connect

Click **Connect Spectrometer**. The serial number appears when it's found.

Under **Potentiostat**, choose:

- **External — start the Gamry sequence in Gamry Framework** (default), or
- **Python — drive the Gamry from here (EchemToolkitPy)** — only selectable in the 32-bit env.
  In Python mode, tick **Also save Gamry .DTA files (dta/ subfolder)** if you want native Gamry
  files for Echem Analyst alongside the clean `.txt` files. In External mode this does nothing —
  Gamry Framework writes its own `.DTA` files.

### 3.2 Instrument Tab — Integration Time and Averaging

Under **Spectrometer Settings**:

**Integration time (ms)** — how long the detector collects light per scan. It is entirely
dependent on your light source; a halogen + neutral-density filter saturates around 0.11 ms, and a
different lamp (e.g. AvaLight) will differ by a lot. Don't guess — use the Linearity Check (§3.3).

**Scan averages** — more averaging means less noise but slower acquisition. The time to acquire one
averaged spectrum **must be less than the "Time between spectra"** you set on the Parameters tab
(default 100 ms), or you'll fall behind the Gamry.

Click **Apply to Spectrometer** after changing either, then **Run Timing Test** — it reports the
actual time per measurement. 200 averages typically lands ~78–85 ms, comfortably under 100 ms.

### 3.3 Instrument Tab — Linearity Check ⚠️ *do this with the reference in the beam*

**Put the reference (blank FTO + electrolyte, 100 %T) in the light path before running this.** The
check ramps the integration time upward and watches the peak until the detector saturates, so it
must see the light you'll actually be measuring against.

The detector is a 16-bit ADC — it clips hard at 65535 counts. Real detectors stay linear almost all
the way to that clip, so "where does it stop being linear" alone would leave you with almost no
headroom. The check therefore recommends the **tighter** of two limits:

- 5 % below where the response deviates from the fitted line by more than **Tol** (default 2 %), or
- the integration time that fills at most **Max fill** of full scale (default **85 %**)

In practice the fill cap is what binds, and that's the one that leaves room for lamp drift.

**Procedure:**

1. Click **Find saturation** — it doubles the integration time until the detector clips, then
   bisects to find the real saturation point and writes it into **Stop**.
2. Check **Start** / **Steps** (defaults are fine), then click **Run Linearity Check**.
3. Read the plot: measured points, the fitted line, ADC full scale, and the recommended point.
4. Click **Use recommended** to load it into Integration time — or type your own. You are never
   forced to take the recommendation.

Re-run this whenever you change the lamp, the ND filter, or the cell.

### 3.4 Instrument Tab — Wavelength Range (optional)

The lamp fades into noise below ~400 nm and above ~1050 nm, and those pixels are written into every
data file. Setting a **Wavelength range** crops them, giving smaller files and cleaner data.

> **Why the edges are junk.** The SDK will happily report a wavelength for every one of the 2048
> detector pixels — roughly 144 to 1308 nm on this unit. But the instrument's *optics* are only specified
> from **300 to 1100 nm**, and the halogen lamp doesn't usefully reach either end of even that. So the
> outer pixels aren't measuring anything meaningful; they're just noise being written to disk. The app
> already restricts itself to a calibrated window (~380–1100 nm); this setting narrows it further to
> the range your lamp actually illuminates.

Set **min** / **max** and click **Apply Range**; **Reset** returns to the spectrometer's full range.
Leave it alone and you get the full range — this is opt-in and off by default.

> Narrowing the range re-slices your existing dark/reference. **Widening it discards them** — the
> data simply isn't there — so you'll be asked to re-collect. Set the range *before* dark/reference
> if you can.

### 3.5 Instrument Tab — Dark, Reference, and Test

The three tabs under **Spectra**:

**Dark** — block the beam completely, then **Collect New**. This is detector noise and stray light;
it's stable and rarely needs redoing.

**Reference (100 %T)** — put the blank in the beam: electrolyte in the cell with a **blank FTO
insert** (no sample). Click **Collect New**. Check that the peak lands near your target
(~85 % of full scale ≈ 56 000 counts if you took the linearity recommendation).

**Test (sample)** — **this is the non-destructive one.** Click **Measure** to take a single spectrum
of whatever is in the beam right now, *without* overwriting your dark or reference. Use it to:

- confirm blank-vs-blank reads ≈ 0 absorbance during setup, and
- look at the actual sample spectrum after you swap the blank FTO for the real sample — the step
  where a plain "Collect New" on the Reference tab would **destroy your reference** by recording the
  sample as 100 %T.

Switch between **Counts** and **Absorbance** to view the stored scan either way (Absorbance needs a
dark and a reference). **Suggest range from this** reads the noise floor of a blank test-absorbance
and proposes a wavelength window.

Both dark and reference can be saved and reloaded with **Save** / **Load**.

### 3.6 Instrument Tab — Bench Defaults

**Save as defaults** writes the current spectrometer/linearity/Gamry settings to
`config/bench.ini` — this rig's settings, remembered next launch. These are *bench* settings (lamp,
detector, machine), not experiment settings. **Restore factory defaults** deletes that file and
falls back to the lab-wide `config/defaults.ini`. See [`config/README.md`](../config/README.md).

### 3.7 Parameters Tab

**Sample Info** — sample name, electrolyte, notes; all of it is written into a metadata JSON in the
run folder, so the data documents itself.

**Data folder** — pre-filled with today's date; add a short description (e.g. `20260714_P3HT_KPF6`).
Click **Today** if the app has been open past midnight. **Save location** is the *parent* folder
(leave it at `…\Documents\specechem_data`); the run folder is created for you.

> ⚠️ Don't browse *into* a folder you created yourself for the Save location, or you'll get a
> doubled path like `…\20260714_test\20260714_test\`.

**Wait for Gamry trigger** — leave this on. It's the hardware sync.

**Cyclic Voltammetry**, **Pre-dedoping Baseline**, **Doping / Dedoping Cycles** — each group has an
"Include…" checkbox, so you can run any subset.

> In **External** mode the potentials here are **documentation only** — the `.GSequence` holds the
> real values, and these are just recorded with your data. In **Python** mode they *drive the run*.
> Either way they must match what the Gamry is actually doing, or your metadata will lie.

**Run it, but discard the data** (under *Include pre-dedoping*) — the pre-dedoping step still runs
normally, so the film is conditioned, but **no files are written for it**: no spectra, no echem, no
`.DTA`. The segment won't appear in Results either. Use it when pre-dedoping is just conditioning
and its data would only clutter the folder.

**Save Settings File** / **Load Settings File** store this whole tab as JSON, so you can reload an
experiment's parameters exactly.

### 3.8 Run Tab — Start

**In External mode this is a two-step start, and the order matters:**

1. Click **Start** in the GUI *first*. The app arms the spectrometer and **waits** — the banner will
   tell you it's armed.
2. *Then* start the sequence in Gamry Framework (Appendix B). When the Gamry raises DIGOUT0, the
   spectrometer fires and collection begins.

If you start the Gamry first, the trigger edge arrives before the spectrometer is armed and is
simply **missed**.

**In Python mode there is only one step:** click **Start**. The app arms the spectrometer, then
fires the trigger and runs the Gamry itself, segment by segment.

While it runs, the Run tab shows the sequence with a ✓ against each finished segment, the last
completed segment's absorbance, the live echem curve (Python mode), and a status log.

- **Stop** finishes the current segment cleanly, then halts.
- **ABORT** stops immediately. A partial segment is **not** written.

### 3.9 Results Tab

Pick any completed segment from **Segment** to see its absorbance and, in Python mode, its
electrochemistry. Narrow the plotted **Wavelength range** for a closer look (this only affects the
plot, not the data). **Save Plots** exports images; **Open Data Folder** opens the run folder;
**Load Run…** reopens a previous run from disk.

> A segment you marked *discard* never appears here — nothing was saved, so there's nothing to review.

---

## Part 4 — Data Output

### 4.1 File Locations

Spectra and (in Python mode) echem `.txt` files are written by the app to:

```
C:\Users\inst-chem\Documents\specechem_data\[data folder]\
```

In **External** mode, Gamry Framework separately writes its own `.DTA` files to whatever directory
you set in the Sequence Wizard. **Point it at the same run folder** — downstream analysis expects
the Gamry step files and the spectra files to live together.

### 4.2 Output Files

| Filename | Experiment step |
|---|---|
| `CVspectra.txt` | Cyclic voltammetry |
| `prededopingspectra(0).txt` | Pre-dedoping baseline |
| `spectra(0).txt`, `spectra(1).txt`, … | Doping cycles |
| `dedopingspectra(0).txt`, `dedopingspectra(1).txt`, … | Dedoping cycles |
| `[data folder]_metadata.json` | Build id, sample, notes, and every setting used |
| `[data folder].log` | Full run log (build id on the first line) |

Each spectra file is tab-separated with 8 columns: wavelength, absorbance, dark (first block only),
reference (first block only), raw intensity, spectrum number, absolute time, corrected time.

### 4.3 Which version produced this data?

Every run records the exact code that wrote it, so you never have to guess later.

- **The title bar** shows it while you work: `spec-echem 0.2.0+7.g0f26a7a`.
- **`_metadata.json`** stores it as `spec_echem_version`.
- **The run log's first line** repeats it.

`0.2.0` means a tagged release. `0.2.0+7.g0f26a7a` means seven commits past that tag, at commit
`0f26a7a` — which is normal, since most work happens between releases. A **`.dirty`** suffix means
the code had uncommitted edits, so that run can't be reproduced from any commit. That's fine while
you're developing; it's worth noticing on data you intend to publish.

Quote this string in bug reports — it's the single most useful thing you can give.

**In Python mode** the Gamry data is written alongside as `CV.txt`, `steps(N).txt`,
`dedoping(N).txt`, `prededoping(N).txt` (time, corrected time, potential, current, index), plus
native `.DTA` files in a `dta/` subfolder if you enabled them.

> This format is fixed — downstream analysis at UW
> ([`OECT_processing`](https://github.com/rajgiriUW/OECT_processing)) depends on the exact column
> names and filenames. See [`docs/data-format.md`](data-format.md). Don't change them.

---

## Troubleshooting

**Spectrometer not found on Connect**
- Check USB; make sure it was powered on before launching the app
- Confirm `avaspec.py` is in site-packages and the DLL path edit was applied (§1.4)

**Python mode is greyed out**
- You're in a 64-bit environment. `EchemToolkitPy` is 32-bit only — use `SpecEchem32`, or stay in
  External mode.

**The run starts but no spectra are collected (it just waits)**
- In External mode: you probably started the Gamry *before* clicking Start. Abort, click Start
  first, then run the Gamry sequence.
- Check the DIGOUT0 → Pin 6 trigger wire.

**Spectra count doesn't match the electrochemistry**
- Scan averages × integration time must be **less** than "Time between spectra". Run the timing test
  (§3.2). The run log also reports the actual measured cadence for every segment — check it there.

**Reference peak is too low / saturated**
- Re-run the Linearity Check (§3.3) with the reference in the beam. Lamp output drifts with age and
  changes completely if you swap the source or the ND filter.

**Absorbance looks like pure noise at the edges**
- That's the lamp dying out below ~400 nm and above ~1050 nm. Set a wavelength range (§3.4).

---

## Appendix A — Legacy Jupyter Notebook Workflow

> The notebook path still works and is kept for reference and for anything the GUI doesn't cover.
> **New users should use the GUI (Part 3).** The notebook defines its own `Avantes` class,
> separate from the `spec_echem` package.

Launch Jupyter with the `SpecEchem` environment active and open
`notebooks/SpecEchem Avantes 0.996-20250717.ipynb`.

**Session setup:** run the definition cells in order — standard imports; the `Avantes` class;
`setup()` (dark/reference), `get_spectra()` (acquisition), `plot_data()`. Then:

```python
spec = Avantes()
measconfig, serial_number = spec.init()
wavelength_old, wavelength = spec.wavelengths()
spec.set_int_time(measconfig, 0.05)     # ms
```

Take a test spectrum and adjust until the reference peak is 50 000–60 000 counts:

```python
test_timestamp, test = spec.measure()
spec.plot_data(wavelength, test)
```

Find a workable number of scan averages (`T_dif` must stay below `deltaTime`, default 100 ms):

```python
for i in range(1, 27):
    spec.set_scan_aves(measconfig, i*10)
    a, b, c, d = spec.measure_timing(measconfig)
    print(f"Ave Scans: {i*10}, transfer = {c:.4g} ms, T_dif {d*1000:.4g} ms")
spec.set_scan_aves(measconfig, 200)
```

Collect dark (beam blocked) and reference (blank cell in the beam):

```python
dark, ref = setup(wavelength, file_prefix="YYYYMMDD_SampleName")
dark, ref = setup(wavelength, load_existing=True)   # to reload later
```

**Running the sequence.** The notebook cell always starts first and waits for the trigger; then you
start (or resume) the Gamry. The Gamry sequence has 15-second delays between steps — use them to get
the next cell running and waiting before the next trigger fires.

```python
data_type = 1   # 1 = CV, 2 = doping, 3 = dedoping, 4 = pre-dedoping
run_number = 0
spectra_data, absorb_data = get_spectra(measconfig, data_folder, dark, ref,
                                        deltaTime=0.1, num_echem_points=301,
                                        data_type=data_type, run_number=run_number,
                                        trigger=True)
```

Repeat per step, incrementing `run_number` for each doping/dedoping cycle. If you miss a window,
pause the Gamry before it enters the next step, get the cell running, then resume.

---

## Appendix B — Gamry Sequence Wizard (External Mode)

Only needed in **External** mode. In Python mode the app builds the sequence itself and you can skip
this entirely.

Open **Gamry Framework**, launch the **Sequence Wizard**, click **Load Sequence** and open:

```
C:\Users\inst-chem\Documents\spec-echem\gamry\Spec_Echem_20250714.GSequence
```

The "User Defined Sequence" panel shows the experiment tree. **Double-click any `Define…` item to
change its value:**

| Item in tree | What it controls | Typical |
|---|---|---|
| `Define An Integer Number, [cvcycles]` | Number of CV cycles | 1 |
| `Define A Potential (V), [CVScanLimit1]` | CV negative scan limit | −0.5 |
| `Define A Potential (V), [CVScanLimit2]` | CV positive scan limit | +0.7 |
| `Define A Potential (V), [DedopingPotential]` | Dedoping potential | −0.5 |
| `Define A Potential (V), [DopingPotInitial]` | Starting doping potential | +0.1 |
| `Define A Real Number, [DopingDurationsec]` | Duration of each step (s) | 5 |

Double-click **Group Data Files** to set the output directory. Set **Directory/Precursor** to your
run folder name — **make it match the Data folder name on the Parameters tab**, so the Gamry `.DTA`
files land with the spectra.

**Doping/dedoping cycle count:** the loop reads "Loop Until [DopingPotInitial] > 0.25". Starting at
0.1 V and stepping 0.1 V, that's **2 cycles**. Raise the threshold for more.

**Individual step dialogs** (double-click, then OK) — you generally change nothing here; the
potentials and times are greyed out and driven by the variables above.

- **Cyclic Voltammetry** (`CV.DTA`) — you *may* adjust **Scan Rate**, **Step Size**, **Max Current**
- **Set Digital Out** — appears in pairs bracketing each step (HIGH before, LOW after). These *are*
  the trigger. **Do not edit them.**
- **Chronoamperometry — Pre-dedoping / Doping / Dedoping** — click OK without changes

Click **Run Sequence** — but **not** until the GUI is running and armed (§3.8).
