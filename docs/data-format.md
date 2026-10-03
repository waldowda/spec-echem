# Output File Format — DO NOT CHANGE

This file is the authoritative specification for every data file spec-echem writes.
Downstream analysis tools at UW (`rajgiriUW/OECT_processing`) depend on these formats. **Do not
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
| 6 | `Spectrum number` **or** `Index` | Integer index | Sequential across all time points. **The header differs by file type** — see the note below |
| 7 | Time (s) | Absolute timestamp | Seconds since acquisition start |
| 8 | Corrected time (s) | Relative timestamp | Time relative to first spectrum in step |

**Column 6's header is not the same in every file.** `spectra(N).txt` (doping,
`DATA_TYPE_DOPING`) writes **`Spectrum number`**; `CVspectra.txt`,
`dedopingspectra(N).txt` and `prededopingspectra(N).txt` all write **`Index`**. The
column's contents are identical either way — a sequential integer — and this is
deliberate rather than a defect (`spec_echem/data.py`, `col6_name`), inherited from the
notebook-era output that downstream analysis already reads. It is recorded here because
this file is the authority a reader would be written against, and reading column 6 by
NAME rather than by position will silently miss three of the four file types.

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
offset is **dropped** here — verified against `OECT_processing/.../uvvis.py`
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

---

## 4. HDF5 files — written IN ADDITION, never instead

Since 2026-09-29 a run also writes HDF5 beside the ascii. **The 8-column format above is
unchanged and remains the authority**: downstream analysis depends on those names, so the
text files are still the source of truth. The H5 is additive and best-effort — if `h5py`
is missing or a write fails, the run and the ascii are unaffected and the run log says so.

Why: a 14-segment run is ~645 MB of ascii, because every wavelength is reprinted for every
time point. Measured on a real CV (1261 wavelengths × 721 spectra): **83.6 MB of ascii
becomes 7.3 MB of HDF5**, and it carries strictly more.

### Four files per run, one per segment type

```
{folder}/{folder}_cv.h5
         {folder}_prededoping.h5
         {folder}_doping.h5            all doping cycles
         {folder}_dedoping.h5          all dedoping cycles
```

Cycles are groups **keyed by cycle number**, never by potential: a potential repeats —
every dedoping step shares one — the ladder need not be monotonic, and a chronoamperometry
scheme need not be an ordered ladder at all. Potential is an *attribute* of a cycle, never
its address.

### Layout

```
/ (root) attrs
    schema_version   "1"
    run_id           the run folder name
    data_type        1/2/3/4          data_type_name   "CV" | "Doping" | ...
    build_id         the code that wrote it
    metadata_json    the ENTIRE {folder}_metadata.json, verbatim
    run_started, sample_name, electrolyte, notes, instrument identities
                     promoted from that JSON so a viewer shows them

/wavelength                  float64 (n_wl)        units="nm"

/{cycle}/  attrs: run_number, label, num_points, delta_time, trigger,
                  potential_set       what was asked for   (absent for a CV)
                  potential_measured  median of the trace  (absent without echem)

    counts_vs_time      uint16 or float32 (n_wl, n_t)  units="counts"
    absorbance_vs_time  float32           (n_wl, n_t)  units="-log10(T)"
    dark                float64 (n_wl)
    reference           float64 (n_wl)
    time                float64 (n_t)  seconds from THIS segment's first spectrum
    time_spectrometer   float64 (n_t)  the spectrometer's own clock, unrebased

    echem/time | echem/potential | echem/current     three parallel 1-D arrays,
                     absent in External mode (no potentiostat, so no data)
```

Every dataset carries `units`; every 2-D one carries `dims` ("wavelength x time").

### Five things worth knowing

**`potential_set`, never a bare `potential`.** The bare name belongs to the *measured*
trace in `echem/`. Everything inside `echem/` is measured by definition, so one word means
one thing in one file.

**The counts dtype is chosen, not assumed.** The ADC is 16-bit, so a single scan is an
exact integer — but `scan_averages` defaults to 200 and an averaged count is not. Measured
on real data: a fractional part of up to exactly 0.5. The writer stores `uint16` only when
the array really is integral and in range, `float32` otherwise.

**`n_echem` is not `n_t`.** The potentiostat and the spectrometer are independent devices
on independent clocks — roughly 300 echem points against 301 spectra for a 30 s hold.
Neither is resampled onto the other; correlating them is the reader's decision.

**No `charge`.** It is derived (trapezoidal integration of current), and a derived quantity
in an archive drifts out of agreement with its source. The OECT export computes it.

**Compression is off by default.** Measured: gzip buys 21%, not the 2-4× first estimated,
for ~120 ms a segment, and level 9 is byte-identical to level 4. `hdf5_compression` in the
settings turns it on for archiving.

### The one knowing compromise: float32

**Absorbance and averaged counts are stored as float32, so the round trip is exact to
about seven significant figures rather than bit-exact.** Decided deliberately 2026-09-29,
with the numbers measured on a real 13-segment run rather than estimated:

| column | agreement after text → h5 → text |
|---|---|
| wavelength, dark, reference, Index / Spectrum number, both time columns | **exact** |
| echem potential, current, corrected time | **exact** |
| Absorbance | max 3.0e-08 |
| Measured value (counts) | max 1.9e-03 |

The counts figure is the one that looks alarming and is not: 1.9e-03 at ~54,700 counts is
**3.6e-08 relative**, seven orders of magnitude below the shot noise of √54,700 ≈ 234
counts. Nothing measurable is lost.

float64 counts would round-trip bit-exactly at roughly **+50% file size**. That trade was
considered and declined — the error is far below the physics, and the format's whole
purpose is size.

**When this would be worth revisiting:** a detector with a much larger dynamic range (the
absolute error scales with magnitude), or a use that needs the archive to be
*bit*-reproducible rather than *physically* faithful — a checksum-verified deposit, say.
Neither applies today. `counts_dtype()` already stores integral counts as exact `uint16`,
so a run at one scan average is bit-exact as it stands; only averaged counts take the
float32 path.

Reproduce any of this with `python examples/h5_to_ascii.py <run> --out /tmp/x --compare`.

**Where each one writes.** The archival `.h5` goes INTO the run folder, because it is that
run's own data — like its ascii, its metadata JSON and its log — and a run folder being
self-contained is a property the rest of the project relies on. `--out` overrides it for a
source on read-only media or a conversion that must not touch an archived original. The
OECT export defaults to `<run>/oect/` but is a DELIVERABLE rather than the run's data, so
sending it elsewhere with `--out` is the normal case there, not the exception.

### Reading, converting, exporting

| | |
|---|---|
| `spec_echem.data.read_segment_h5(path, cycle)` | absorbance, shaped exactly like `read_spectra_absorbance()` |
| `spec_echem.data.discover_run_h5(folder)` | segments in run order, mirroring `discover_run_segments()` |
| `examples/ascii_to_h5.py`, or the Results tab's **Convert to HDF5** | rebuild the H5 for a run recorded before this existed (`spec_echem.h5_backfill`) |
| `examples/h5_to_ascii.py` | regenerate the ascii FROM the H5 — the round-trip evidence |
| `examples/export_oect.py`, or the Results tab's **Export for OECT analysis** | a derived view in the downstream pipeline's own layout |
| `examples/bench_h5_size.py` | bytes and write time on this machine's disk |
| `spec_echem.oect_export` | a derived view in the downstream pipeline's own layout |

**A backfilled file cannot have `time_spectrometer`.** `write_spectra_file` computes
`Time (s)` and `Corrected time (s)` identically — a leftover from when `Time` carried a
+100 offset — so the device clock was never in the ascii. Backfilled files omit the
dataset and set `time_spectrometer_recovered = False` rather than store a copy of `time`
under that name.
