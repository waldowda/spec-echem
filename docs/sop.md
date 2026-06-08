# spec-echem Standard Operating Procedure

**Version:** 0.1 (matches notebook v0.996, July 2025)  
**Audience:** Waldow Lab students, first-time users  
**System:** Windows 11 instrument PC (`inst-chem` account)

---

## Overview

This system runs spectroelectrochemistry experiments by coordinating two instruments:

- **Gamry Ref-600+ potentiostat** — applies potentials and measures current; sends a hardware trigger signal at the start of each electrochemical step
- **Avantes spectrometer** — collects UV-Vis spectra (~380–1100 nm); receives the trigger and begins acquiring spectra

You control the Gamry through its own **Sequence Wizard** software. You control the spectrometer from a **Jupyter notebook** running in Python. The two are synchronized by a hardware wire connecting the Gamry's digital output to the Avantes trigger input.

---

## Part 1 — Installation (One-Time Setup)

Complete this section once per machine. Skip to Part 2 if the instrument PC is already set up.

### 1.1 Prerequisites

Before starting, confirm you have:

- [ ] Windows 11 PC with Avantes spectrometer connected via USB
- [ ] Gamry Ref-600+ connected via USB, Gamry Framework software installed
- [ ] Anaconda (individual edition) installed — **verify the install path** before proceeding (see Step 1.2)
- [ ] Avantes SDK files (obtain from Avantes; the 64-bit version is `AvaSpecX64-DLL_9.14.0.0`)
- [ ] Access to the spec-echem GitHub repository (ask Dr. Waldow for access)

### 1.2 Verify Anaconda Install Path

The Anaconda path on this machine is non-standard. Before doing anything else, open **Anaconda Prompt** and run:

```
python -c "import sys; print(sys.executable)"
```

You should see something like:

```
C:\Users\inst-chem\AppData\Local\anaconda3\python.exe
```

> **Note:** The path is `AppData\Local\anaconda3`, **not** `anaconda3` directly under the user folder. Use this path any time you need to locate site-packages (see Step 1.4).

### 1.3 Create the Conda Environment

In **Anaconda Prompt**, run:

```
conda create -n SpecEchem python=3.13
conda activate SpecEchem
pip install numpy matplotlib pandas scipy jupyter
```

Verify the environment is active — you should see `(SpecEchem)` at the start of the prompt.

### 1.4 Install avaspec.py (Avantes Python Bindings)

The Avantes SDK includes a Python file (`avaspec.py`) that is **not pip-installable** — it must be copied manually to your Python environment and edited.

**Step A — Find site-packages:**

With the `SpecEchem` environment active, run:

```
python -c "import site; print(site.getsitepackages())"
```

The path will be something like:

```
C:\Users\inst-chem\AppData\Local\anaconda3\envs\SpecEchem\Lib\site-packages
```

**Step B — Copy the file:**

Copy `avaspec.py` from the SDK examples folder to site-packages:

```
Source:      C:\AvaSpecX64-DLL_9.14.0.0\examples\PyQt5_simple\avaspec.py
Destination: C:\Users\inst-chem\AppData\Local\anaconda3\envs\SpecEchem\Lib\site-packages\avaspec.py
```

**Step C — Apply three required edits** (open the destination file in Notepad or VS Code):

1. **Comment out the `import globals` line** near the top:
   ```python
   # import globals
   ```

2. **Comment out the `from PyQt5.QtCore import *` line** near the top:
   ```python
   # from PyQt5.QtCore import *
   ```

3. **Fix the DLL loading block.** Find the section that loads `avaspecx64.dll` and replace it so it reads:
   ```python
   import os
   os.add_dll_directory(r"C:\AvaSpecX64-DLL_9.14.0.0")
   lib = WinDLL("avaspecx64.dll")
   ```
   The original code uses a relative path that fails when running from Jupyter.

> **Important:** Do not overwrite this file with a fresh copy from the SDK without reapplying these three edits.

**Step D — Verify:**

```
python -c "import avaspec; print('avaspec OK')"
```

### 1.5 Clone and Install spec-echem

In **Anaconda Prompt** with `SpecEchem` active:

```
cd C:\Users\inst-chem\Documents
git clone https://github.com/waldowda/spec-echem.git
cd spec-echem
pip install -e .
```

### 1.6 Verify the Installation

With the spectrometer **not yet connected**, run:

```
python -c "from spec_echem import AvantesSpectrometer; print('Package import OK')"
```

This should succeed even without hardware. If it fails, check that `pip install -e .` completed without errors.

---

## Part 2 — Hardware Setup

### 2.1 Trigger Wiring

The Gamry and Avantes are synchronized by a direct wire connection:

| Gamry Ref-600+ | Avantes DB26 connector |
|----------------|------------------------|
| DIGOUT0 | Pin 6 (hardware trigger input) |

This wire should already be in place on the instrument PC. If it has been disconnected, ask Dr. Waldow before reconnecting.

### 2.2 Power-On Order

1. Turn on the Avantes spectrometer (USB to PC)
2. Turn on the Gamry potentiostat (USB to PC)
3. Log in to Windows, open Anaconda Prompt

The spectrometer must be powered and connected before launching Jupyter.

---

## Part 3 — Running an Experiment

### 3.1 Before You Start

- [ ] Spectrometer and Gamry are both powered and connected
- [ ] Light source is on and warmed up (allow ~15 min)
- [ ] Electrochemical cell is prepared and installed
- [ ] You know your experiment parameters (see Section 3.3)

### 3.2 Launch Jupyter

In **Anaconda Prompt** with `SpecEchem` active:

```
cd C:\Users\inst-chem\Documents\spec-echem
jupyter notebook
```

Open the notebook: `notebooks/SpecEchem Avantes 0.996-20250717.ipynb`

### 3.3 Notebook Initialization (Run Once Per Session)

Run the following cells **in order at the start of every session**. These define all the functions and classes the notebook uses.

> The notebook already contains descriptive markdown headers above most sections — read those as you go.

**Cell 1 — Standard imports**
Run this cell first. It imports numpy, pandas, matplotlib, and other standard libraries.

**Cell 2 — Avantes class definition**
Defines the `Avantes` class used to control the spectrometer. This class is defined directly in the notebook (separate from the `spec_echem` package — this is intentional for now).

**Cells 3–5 — Function definitions**
These define:
- `setup()` — collects dark and reference spectra
- `get_spectra()` — the main data acquisition function
- `plot_data()` — plots absorbance data after an experiment

Run all three.

**Cell 7 — Instantiate the spectrometer**
```python
spec = Avantes()
```

**Cell 8 — Initialize and connect**
```python
measconfig, serial_number = spec.init()
```
If the spectrometer is connected and powered, you will see the serial number printed. If this fails, check the USB connection and that the Avantes SDK DLL path is correct (Section 1.4, Step C).

Get the wavelength calibration (also in Cell 8):
```python
wavelength_old, wavelength = spec.wavelengths()
```

### 3.4 Set Integration Time

The integration time controls how long the detector collects light per scan. Set it so the **reference spectrum peak is in the range of 50,000–60,000 counts**.

Run the integration time cell (sets 0.05 ms as a starting point):
```python
spec.set_int_time(measconfig, 0.05)
```

Take a test measurement and plot it:
```python
test_timestamp, test = spec.measure()
spec.plot_data(wavelength, test)
```

Adjust `set_int_time` up or down until the reference spectrum peak is in the 50,000–60,000 range.

### 3.5 Set Scan Averages

More averages = less noise, but slower acquisition. The scan average time must be **less than `deltaTime`** (the interval between spectra in `get_spectra()`). The default `deltaTime` is 100 ms.

Run the timing test to find a good number of averages:
```python
for i in range(1, 27, 1):
    spec.set_scan_aves(measconfig, i*10)
    a, b, c, d = spec.measure_timing(measconfig)
    print(f"Timestamp: {a}, Ave Scans: {i*10}, transfer time = {c:.4g} ms, T_dif {d*1000:.4g} ms")
```

Look at the `T_dif` column — choose a number of averages where `T_dif` is clearly below 100 ms. Typically **200 averages** works well (~78–85 ms).

Set the final value:
```python
spec.set_scan_aves(measconfig, 200)
```

### 3.6 Collect Dark and Reference Spectra

**Dark spectrum:** Block the light beam completely, then run:
```python
dark, ref = setup(wavelength, file_prefix="YYYYMMDD_SampleName")
```
Follow the prompts — it will ask you to confirm before collecting.

**Reference spectrum:** Place the blank cell (water in 1 cm cell with blank FTO glass) in the light path, then follow the prompts in `setup()`.

Both spectra are saved to disk automatically with the prefix you provide. To reload them in a later session:
```python
dark, ref = setup(wavelength, load_existing=True)
```

### 3.7 Set Up the Gamry Sequence

Open **Gamry Framework** and launch the **Sequence Wizard** from the menu. Click **Load Sequence** and open:
```
C:\Users\inst-chem\Documents\spec-echem\gamry\Spec_Echem_20250714.GSequence
```

The right panel ("User Defined Sequence") shows the full experiment tree. **Double-click any `Define...` item to change its value** for your experiment. The editable items near the top of the tree are:

| Item in tree | What it controls | Typical value |
|---|---|---|
| `Define An Integer Number, [cvcycles]` | Number of CV cycles | 1 |
| `Define A Potential (V), [CVScanLimit1]` | CV negative scan limit (V vs. ref) | −0.5 |
| `Define A Potential (V), [CVScanLimit2]` | CV positive scan limit (V vs. ref) | +0.7 |
| `Define A Potential (V), [DedopingPotential]` | Dedoping potential (V vs. ref) | −0.5 |
| `Define A Potential (V), [DopingPotInitial]` | Starting doping potential (V vs. ref) | +0.1 |
| `Define A Real Number, [DopingDurationsec]` | Duration of each doping/dedoping step (s) | 5 |

Also double-click **Group Data Files** to set the output directory name. In the dialog:
- **Group By:** leave set to Directory
- **Directory/Precursor:** enter your experiment folder name (e.g., `20250714-SampleName`)
- **Show runtime dialog** is checked — Gamry will show this dialog again when the sequence starts, so you can confirm or change the name at that point

The directory name here should match the `data_folder` variable you set in the notebook (see Section 3.8).

**Doping/dedoping cycle count:** The loop at the bottom of the tree reads "Loop Until [DopingPotInitial] > 0.25". Starting at 0.1 V and incrementing by 0.1 V per cycle, the sequence runs **2 cycles** (at 0.1 V and 0.2 V) by default. To run more cycles, increase the loop threshold or lower `DopingPotInitial`.

#### Individual step dialogs (double-click to open, then click OK)

The remaining items in the sequence tree each open a dialog when double-clicked. You generally do not need to change anything in these — all potentials and times are grayed out and controlled by the variables you already set above. Review the key fields below and click OK.

**Cyclic Voltammetry** (`CV.DTA`):
- Scan Limit 1, Scan Limit 2, Cycles — grayed out (set by variables above)
- Fields you may adjust for your sample: **Scan Rate (mV/s)** (default 1000), **Step Size (mV)** (default 100), **Max Current (mA)** (default 0.3)
- Initial E and Final E are 0 V vs Eref — leave unless your experiment requires otherwise

**Set Digital Out** (appears multiple times in the tree):
- These dialogs set DIGOUT0 High (trigger on) or Low (trigger off) at each step boundary
- They are fully pre-configured — **do not edit them**. Click OK if one opens.

**Chronoamperometry — Pre-dedoping** (`prededope.DTA`):
- Runs before the doping/dedoping loop to ensure the sample starts in the fully dedoped state
- All voltage and time fields grayed out (controlled by `DedopingPotential` and `DopingDurationsec`)
- Click OK without changes

**Chronoamperometry — Doping** (`steps.DTA`, inside the loop):
- Applies the doping potential (`DopingPotInitial`, incrementing each cycle)
- All voltage and time fields grayed out
- Click OK without changes

**Chronoamperometry — Dedoping** (`dedoping.DTA`, inside the loop):
- Returns the sample to `DedopingPotential` after each doping step
- All voltage and time fields grayed out
- Click OK without changes

When all dialogs are confirmed, click **Run Sequence** — but do not click it until the notebook is running and waiting (see Section 3.9).

### 3.8 Set the Data Folder in the Notebook

In the Sequence Wizard cell of the notebook, set `data_folder` to the same name as the Gamry data directory:
```python
data_folder = 'YYYYMMDD_SampleName'
```

Data will be saved to:
```
C:\Users\inst-chem\Documents\specechem_data\YYYYMMDD_SampleName\
```

### 3.9 Coordinated Experiment Run

**The Python notebook always starts first and waits for the Gamry trigger.** Once the notebook cell is running and waiting, you start (or resume) the Gamry sequence. From that point the two instruments stay synchronized automatically via the hardware trigger.

The Gamry sequence includes 15-second delay windows between steps — use those windows to get the next notebook cell running and waiting before the Gamry fires the next trigger.

**Full sequence:**

1. **In Notebook:** Run the `get_spectra()` call for the CV step. The notebook will start and wait for the hardware trigger:
   ```python
   data_type = 1  # CV
   run_number = 0
   spectra_data, absorb_data = get_spectra(measconfig, data_folder, dark, ref,
                                            deltaTime=0.1, num_echem_points=301,
                                            data_type=data_type, run_number=run_number,
                                            trigger=True)
   ```

2. **In Gamry:** Start the sequence and click through the prompts. When the Gamry sets DIGOUT0 HIGH at the start of the CV scan, the notebook begins collecting spectra automatically.

3. **Gamry** completes the CV scan and sets DIGOUT0 LOW. The notebook finishes collecting and the cell completes. The Gamry then enters a **15-second delay**.

4. **During the 15-second delay — In Notebook:** Run the next `get_spectra()` call (for the pre-dedoping step, `data_type = 4`). Get it running and waiting before the delay expires.

5. **Repeat** for each subsequent step — always get the notebook cell running and waiting during the Gamry delay before the next electrochemical step begins:
   - Pre-dedoping: `data_type = 4, run_number = 0`
   - Doping cycles: `data_type = 2, run_number = 0, 1, 2, …`
   - Dedoping cycles: `data_type = 3, run_number = 0, 1, 2, …`

> **If you miss a window:** Pause the Gamry sequence before it moves past the delay and into the next step. Get the notebook cell running, then resume Gamry.

---

## Part 4 — Data Output

### 4.1 File Locations

Gamry electrochemical data (`.DTA` files) is saved by the Gamry Framework to:
```
C:\Users\inst-chem\Documents\[GroupName]\
```
where `[GroupName]` is the directory name you set in the Gamry sequence.

Spectroscopic data (`.txt` files) is saved by the notebook to:
```
C:\Users\inst-chem\Documents\specechem_data\[data_folder]\
```

### 4.2 Output Files

| Filename | Experiment step |
|----------|----------------|
| `CVspectra.txt` | Cyclic voltammetry |
| `prededopingspectra(0).txt` | Pre-dedoping baseline |
| `spectra(0).txt`, `spectra(1).txt`, … | Doping cycles |
| `dedopingspectra(0).txt`, `dedopingspectra(1).txt`, … | Dedoping cycles |

Each file is tab-separated with 8 columns: wavelength, absorbance, dark (first row only), reference (first row only), raw intensity, spectrum index, absolute timestamp, and corrected timestamp. This format is fixed — downstream analysis tools at UW depend on it.

---

## Troubleshooting

**Spectrometer not found at init:**
- Check USB connection
- Verify spectrometer is powered on before launching Jupyter
- Confirm `avaspec.py` is in site-packages and the DLL path edit was applied

**Wrong Anaconda path:**
- Run `python -c "import sys; print(sys.executable)"` to verify you are in the `SpecEchem` environment
- Always activate with `conda activate SpecEchem` before launching Jupyter

**Import errors in notebook:**
- Make sure you ran all definition cells (Cells 1–5) before running experiment cells
- The `Avantes` class used in the notebook is defined in Cell 2 — if you skipped it, `spec = Avantes()` will fail

**Timing issues / spectra count mismatch:**
- Ensure scan averages × integration time < `deltaTime`
- Use the timing test loop (Section 3.5) to verify before running
