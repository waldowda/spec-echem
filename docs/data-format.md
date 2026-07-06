# Output File Format — DO NOT CHANGE

This file is the authoritative specification for every data file spec-echem writes.
Downstream analysis tools at UW (Raj's `OECT_processing`) depend on these formats. **Do not
change column names, order, separator, or filename conventions without explicit instruction.**

All files are **tab-separated**, written under the run folder:

```
{data_root}/{added_path}/            e.g.  .../specechem_data/20260705_P3HT/
```

where `added_path` has the form `YYYYMMDD_Description`.

Each experiment segment produces a **spectra** file (always) and, in Python mode, a matching
**echem** file (current/potential from the Gamry). External mode writes only spectra; the
Gamry Framework writes its own `.DTA`, converted separately by
`notebooks/gamry_dta_conversion.ipynb`.

---

## 1. Spectra files (optical, UV-Vis)

| Data type | Filename |
|-----------|----------|
| Cyclic voltammetry | `CVspectra.txt` |
| Doping | `spectra(N).txt` |
| Dedoping | `dedopingspectra(N).txt` |
| Pre-dedoping | `prededopingspectra(N).txt` |

`N` = `run_number` (cycle counter). **Parentheses in filenames are literal** —
`spectra(0).txt`, not `spectra_0.txt`.

**8 columns:**

| # | Header | Content | Notes |
|---|--------|---------|-------|
| 1 | Wavelength (nm) | Wavelength calibration | Same for every time point |
| 2 | Absorbance | Calculated absorbance | A = −log₁₀((Sample − Dark) / (Ref − Dark)) |
| 3 | Column 3 (a. u.) | Dark spectrum | Only for time_point == 0; empty otherwise |
| 4 | Column 4 (a. u.) | Reference spectrum | Only for time_point == 0; empty otherwise |
| 5 | Measured value (a.u.) | Raw intensity | Direct spectrometer output |
| 6 | Spectrum number | Integer index | Sequential across all time points |
| 7 | Time (s) | Absolute timestamp | Seconds since acquisition start |
| 8 | Corrected time (s) | Relative timestamp | Time relative to first spectrum in step |

**Absorbance calculation pipeline:**

```
raw spectra (wavelength_pixels × num_time_points)
    → transmittance = (spectra - dark) / (ref - dark)
    → absorbance = -1 * np.log10(transmittance)
    → absorb3_df = pd.DataFrame(absorb3)
    → absorb4 = absorb3_df.T
    → absorb5 = absorb4.set_index(wavelengths)
    → absorb6 = absorb5.T  →  absorb6.index = timeStamp_diff
    → absorb7 = absorb6.T  ← this is what gets written to file
```

---

## 2. Echem files (current/potential, Python mode only)

Added in Phase 2.5. When Python drives the Gamry (`ToolkitPotentiostat`), the
current/potential returned by `curve.acq_data()` is written next to the spectra file. Written
by `spec_echem/data.py:write_echem_file`; the column contract is defined by
`CV_COLUMNS` / `CHRONO_COLUMNS` in `spec_echem/gamry_data.py`.

Timestamps are **not** copied from the spectra file — they come from the potentiostat's own
clock (the two instruments are synchronized in hardware, not by shared timestamps).

### 2a. Cyclic voltammetry — `CV.txt`

**2 columns**, all points, cycles concatenated into one series (the `cycle` field from the
device is available but not split out):

| # | Header |
|---|--------|
| 1 | `WE(1).Potential (V)` |
| 2 | `WE(1).Current (A)` |

### 2b. Chrono holds — `steps(N).txt` / `dedoping(N).txt` / `prededoping(N).txt`

| Data type | Filename |
|-----------|----------|
| Doping | `steps(N).txt` |
| Dedoping | `dedoping(N).txt` |
| Pre-dedoping | `prededoping(N).txt` |

**5 columns:**

| # | Header | Content |
|---|--------|---------|
| 1 | `Time (s)` | Device time − time[0] (starts at 0) |
| 2 | `Corrected time (s)` | Same as column 1 (starts at 0) |
| 3 | `WE(1).Potential (V)` | Potential |
| 4 | `WE(1).Current (A)` | Current |
| 5 | `Index` | Integer 0 .. n−1 |

**Note on `Time (s)`:** the legacy `.DTA` converter set `Time = Corrected + 100`. That `+100`
offset is **dropped** here — verified against Raj's `OECT_processing/.../uvvis.py`
(`current_vs_time`), which reads step files by column *name* and only uses `Corrected time (s)`
and `WE(1).Current (A)`; `Time (s)` is never referenced. Both time columns therefore start at 0.
The column is kept present because spec-echem's own reader (`read_chrono`) requires all 5.

### 2c. Native Gamry `.dta` (optional, on by default)

When `save_dta` is true (default), a genuine Gamry `.DTA` is also written via
`tkp.print_default_dta_file`, into a **`dta/` subfolder** of the run folder:

```
{data_root}/{added_path}/dta/CV.dta
                             /steps(N).dta
                             /dedoping(N).dta
                             /prededoping(N).dta
```

Lowercase `.dta` matches the toolkitpy convention. These open directly in Gamry Echem Analyst
and are for archival / cross-check; the clean `.txt` files above are the analysis interface.
