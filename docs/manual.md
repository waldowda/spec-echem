# spec-echem — user manual

How to drive the GUI, and what the numbers it reports actually mean.

Two halves. **Part 1** is the tabs: what each control does and what it is for. **Part 2**
is the mathematics: which functions are fitted, how the mean relaxation time and its
confidence interval are computed, what makes a fit get flagged, and how the density of
states is calculated. Part 2 exists because several of these numbers are easy to
misread — the mean relaxation time is *not* the fitted τ, and the 95% interval is
narrower than the true uncertainty for a reason worth understanding.

A principle that runs through the whole program: **it raises concerns; it does not hide
results.** No fit, plot, or table is withheld because software judged it unworthy. Where
a check objects, the objection appears next to the number, and the decision is yours.

---

# Part 1 — The tabs

## 1. Instrument

Connect to hardware and establish the optical baseline. Nothing else works until dark
and reference spectra exist.

| Control | What it does |
|---|---|
| **Connect** | Opens the spectrometer and, in Python mode, the potentiostat. Logs serial numbers into the run log so a data folder can name the hardware that produced it. |
| **Integration time** | Exposure per spectrum, in **milliseconds**. Passed straight to the detector. |
| **Scan averages** | Spectra averaged per recorded point. Total time per point is integration × averages — this is what sets your real cadence. |
| **Dark** | Detector response with the light blocked. Subtracted from everything. |
| **Reference** | Full-intensity spectrum through a blank. The denominator of the absorbance. |
| **Test (sample)** | A single non-destructive spectrum. Use it after swapping the sample — it does **not** overwrite the reference. |
| **Linearity check** | Ramps the integration time and finds where the detector stops responding linearly. Recommends a working exposure. |

**Order matters.** Dark and reference are taken with a blank in the beam. Once the sample
is in, use *Test (sample)*, never *Collect New* — recollecting the reference through the
sample destroys the baseline the whole run depends on.

### The linearity check

Detector response is linear until it approaches saturation, then rolls over. The check
ramps exposure, tracks one pixel, fits the linear region, and reports:

- **limit** — where the response departs the fit by more than the tolerance (default 2%).
- **recommended** — the exposure to actually use, usually set by the **fill cap** (default
  85% of full scale) rather than by the linearity limit, because the detector stays linear
  almost to the clip.

The ramp bounds are settings (`lin_start_ms`, `lin_stop_ms`). **They must sit above your
detector's minimum integration time** — a ramp below it measures nothing meaningful.

### The detector's minimum integration time

The SDK does **not** state it. A full `DeviceConfigType` dump on a 2048-pixel detector
shows only pixel count, sensor type, gains, offsets and calibration polynomials — no
minimum or maximum. So the device is asked directly, by bisecting what
`AVS_PrepareMeasure` will accept.

That acceptance was checked against the detector's own integral before being trusted.
On one detector `PrepareMeasure` accepted **0.009033 ms**, and counts against exposure
fit

```
counts = 112 + 26051 · t        within 1% from 0.009 ms to 0.1 ms
```

so those short exposures are genuinely integrated, not clamped. **Host timing cannot
show this** — a USB round trip plus a 2048-pixel readout is ~1.5 ms, which swamps
everything below 1 ms, and an attempt to measure the floor with a stopwatch produced a
number that was pure artefact.

Detectors differ by a large factor here: one accepts ~0.009 ms, another has a floor
around 1.05 ms. A hardcoded default cannot serve both, which is why it is probed.
`examples/probe_min_integration.py` runs all of this standalone, including the
counts-versus-exposure check, which needs the lamp on.

Note what the same data says about the *upper* end: above ~0.1 ms the measured counts
fall progressively below the linear fit — 13% low at 0.2 ms, 48% at 0.5 ms. That is the
detector saturating at that lamp level, and it is exactly what the linearity check
exists to find.

## 2. Parameters

Everything about *this run*. Saved to `{folder}/{folder}_metadata.json` at run start, so a
data folder is self-documenting.

- **Sample info** — name, electrolyte, notes.
- **Film thickness** and **immersed film area** — only used by the density of states; see
  Part 2.
- **CV** — vertices, scan rate, cycles.
- **Doping ladder** — start, step, end. In Python mode these *drive* the run; in External
  mode the sequence file holds the real values and these are documentation.
- **Dedoping** and **pre-dedoping** potentials.
- **Data folder** — `YYYYMMDD_Description`.

## 3. Run

Builds the segment list and executes it. Live echem trace and post-segment spectra.

Segments run in order: CV → pre-dedoping → (doping, dedoping) × N. Pre-dedoping can be set
to run but discard its data — a reset step whose numbers you do not want.

**Stop** finishes the current acquisition cleanly. **Abort** is immediate.

## 4. Results

Review what was recorded. The **Optical view** selector switches what the upper canvas
shows for the selected segment:

| View | Shows |
|---|---|
| **Spectra (all times)** | Every spectrum in the segment, colored by elapsed time. Click the plot to set the analysis wavelength — a red line marks it. |
| **Kinetics (one wavelength)** | Absorbance against time at one wavelength. |
| **Modulation (across the ladder)** | Absorbance at the **end** of each doping step, against potential. One point per rung, building during a run. This is the view to watch live: a film that stops modulating has stopped being worth the rest of the ladder. |
| **Density of states (CV only)** | See Part 2. Requires a CV segment. |

The **wavelength** box reads `auto (polaron)` by default, with the chosen value shown
beside it. Type a number to override. Clicking the spectrum sets it, and carries it to the
Analysis tab.

## 5. Analysis

Fitting, after a run.

| Control | Purpose |
|---|---|
| **Segment** | Which step to fit. Shows its potential. CVs are not offered — a sweep has no single transient. |
| **Model** | `exp`, `biexp`, or `stretched`. The equation appears beside it. |
| **Fit window** | First and last point used. `0` at either end means the segment's own start/end. |
| **Wavelength** | `auto (polaron)` or typed. The resolved value is shown. |
| **Fit segment / Fit all segments** | Fits absorbance, current and charge. |
| **All fits…** | Every fit in the run, one row per segment per trace. Carries **every fitted parameter with its SD** — the columns follow whichever model was used — plus y(0), ⟨τ⟩, its 95% CI, the point count and the residual split. **Copy as CSV** / **Save CSV…**. |
| **Show / log y** | Which traces appear on the ladder, and whether its y-axis is logarithmic. |

The fit plot shows the data, the fitted curve, and a **residual panel above** (the
convention in XPS/NMR/IR fitting). The residuals are the point: an exponential and a
stretched exponential look nearly identical drawn over the same decay, and structure in
the residuals is how you tell them apart.

---

# Part 2 — The mathematics

## Absorbance

```
A = −log₁₀( (sample − dark) / (reference − dark) )
```

Computed at acquisition and stored; the analysis never recomputes it.

## Choosing the wavelength

`auto (polaron)` uses the **signed** change in absorbance across the segment:

```
ΔA = A(end) − A(start)
λ_grows    = argmax(ΔA)
λ_bleaches = argmin(ΔA)
```

Signed, not `|ΔA|` — the bleach is often larger than the growth, so a magnitude would
return π–π* while you believed you were watching the polaron.

**Which one is the polaron depends on the segment.** On doping the polaron grows and π–π*
bleaches; on **dedoping the polaron decays** while π–π* recovers. The program maps by
segment type. A CV gets no automatic pick at all: it returns to where it started, so
ΔA ≈ 0 and there is no growth to find.

Two guards, both learned from real data:

- **Pixels below 410 nm never win.** At the blue edge the lamp delivers essentially
  nothing — a measured 416 counts against 41 250 at 780 nm — so the absorbance there
  swings wildly on noise alone and was beating the real polaron band on every segment.
- **A pixel must clear 10× its own noise.** Noise is estimated from the *second*
  difference along time (÷√6), which cancels any smooth trend; the first difference would
  measure how fast the signal is changing, not how noisy it is.

## The models

```
exp         y = A + B·exp(−t/τ)
biexp       y = A + B₁·exp(−t/τ₁) + B₂·exp(−t/τ₂)
stretched   y = A + B·exp(−(t/τ)^β)
```

- **A** is the asymptote — what the curve approaches as t grows.
- **B** is the amplitude of the part that decays. **A negative B means a rising
  component**, which is correct for a polaron band growing to a plateau.
- Hence **y(0) = A + ΣB**, shown on the legend next to the measured first point. They
  should agree roughly; a large gap says the model misses the earliest behavior.

For a sum of parts, **the prefactors normally share a sign** — two components of one
process pull the same way. Opposite signs mean either competing processes (one band
feeding another) or a fit that has wandered somewhere unphysical. The program flags it and
leaves the interpretation to you.

### Bounds

- `τ > 0` — a negative time constant is a growing exponential, not a slow decay.
- `0.05 ≤ β ≤ 1` — above 1 is a *compressed* exponential, a different physical claim this
  model does not make. The lower bound exists because ⟨τ⟩ involves Γ(1/β), which overflows
  past 1/β ≈ 170.

## Mean relaxation time ⟨τ⟩ — and why it is not τ

The ladder plots **⟨τ⟩**, not the fitted τ. For a stretched exponential τ alone is *not*
the relaxation time, so comparing raw τ between models or samples would be wrong in a way
that looks entirely plausible.

```
exp         ⟨τ⟩ = τ
biexp       ⟨τ⟩ = (|B₁|τ₁ + |B₂|τ₂) / (|B₁| + |B₂|)
stretched   ⟨τ⟩ = (τ/β)·Γ(1/β)
```

Both non-trivial forms are the integral definition **⟨τ⟩ = ∫₀^∞ f dt / f(0)**, where f is
the decaying part — verified numerically, not assumed.

**One deliberate departure.** The biexp form uses `|B|`, not `B`. With same-sign
amplitudes the two agree exactly. With *opposite* signs the signed version partly cancels
and leaves the range of its own components — a measured case gave 0.117 s from τ₁ = 0.553 s
and τ₂ = 4.11 s, and went negative once the denominator crossed zero. A mean outside
[min τ, max τ] is not a mean. So for competing processes the ladder shows a bounded
weighted average, and the sign flag tells you that you are in that regime.

## Uncertainty

⟨τ⟩ is a nonlinear function of several fitted parameters, so its uncertainty is **not**
the τ standard deviation. It is propagated by the delta method using the **full**
covariance matrix:

```
σ² = ∇f ᵀ C ∇f          gradient computed numerically
CI₉₅ = t(0.975, n−p) · σ
```

The off-diagonal terms matter: τ and β are strongly anticorrelated in a stretched fit, and
using only the diagonal overstates the error. Student *t* rather than a flat 1.96 because a
short segment fitted with a five-parameter biexp can have few degrees of freedom.

Validated against Monte Carlo — refitting noisy realisations and taking the spread of ⟨τ⟩ —
agreeing to 2% (stretched) and 16% (biexp), and reducing exactly to the τ standard
deviation for a single exponential.

### The interval is a WITHIN-MODEL number

It answers "given this model over this window, how well is ⟨τ⟩ pinned". It does **not**
cover whether the model is right. The covariance assumes independent residuals, and a
systematic misfit breaks that assumption — but widening the bar would hide a model problem
rather than fix it.

The honest companion is the **residual split**, on the legend:

```
resid: noise 3.2e-04, model-miss 1.5e-03 (1.4% of swing)
```

Successive differences cancel any smooth trend, so the point-to-point scatter **is** the
measurement noise; the RMS above that is a curve the model failed to follow. This ranks
models directly. On one measured segment: exp left 10.2× the noise, stretched 4.6×, biexp
2.2× — so biexp described that transient four times better than stretched, which no
parameter uncertainty could have told you.

**That ranking is specific to a system.** A more dispersive material may well rank
stretched above biexp. Re-read the split per sample.

## When a fit is flagged

Two tiers, and neither hides anything:

- **FIT DID NOT CONVERGE** — no parameters exist. There is genuinely nothing to show.
- **NEEDS REVIEW** — it converged but a check objected. The curve is drawn dashed in
  amber, **every parameter stays in the legend**, the table shows `? value`, and the point
  is plotted on the ladder with a ring. The reason travels with the numbers.

Checks that raise a concern:

| Check | Why |
|---|---|
| τ ≤ 0 | Not a decay. |
| β outside (0, 1] | Not a stretched exponential. |
| **τ > 10 × the fitted window** | Not measurable from the data. A near-straight line is a *very* well-determined exponential with an enormous τ and a tiny uncertainty, so the SD check cannot catch it. A measured case returned τ = 5.5×10¹¹ s from a 60 s segment. |
| SD > 50% of τ | The number is not a measurement. |
| Prefactors differ in sign | Competing processes, or a fit gone wrong. |

A flagged ⟨τ⟩ can be orders of magnitude from the rest and flatten the ladder. That is the
honest cost of not hiding it — use **log y**, or uncheck that trace.

## Density of states

For a sweep slow enough to be quasi-equilibrium, the current *is* the differential charge:

```
i = v · dQ/dV        →        g(E) = i / (v · e · V_film)        E = −eV
```

Units: (C/s)·(s/V)/(C·cm³) = eV⁻¹cm⁻³. `V_film = area × thickness`.

**The scan rate is measured from the data**, not taken from the form. `CV.txt` has no time
column, but the CV's spectra file carries corrected times, and total path swept ÷ elapsed
time is the rate. On one run that gave 98.4 mV/s against a nominal 100 — and it works for
runs with no metadata at all.

**The sweep rate is signed.** On the reverse sweep both `i` and `dV/dt` are negative, so
the quotient is positive — a DOS is positive whichever way the sweep runs.

**Last complete cycle, directions separate.** A CV usually starts and ends partway along a
sweep, so the first and last sweeps are partial and are skipped. Forward and reverse are
never averaged: hysteresis between them is a real effect.

Rising potential removes electrons (**oxidizing**, p-doping); falling potential puts them
back (**reducing**). The curves are labelled accordingly.

### How it is plotted, and why

**Log y-axis.** A DOS spans orders of magnitude and the literature reports the
distribution "over about two of them"; a linear axis flattens the tails, which is where
the interesting part is. Points at or below zero cannot appear on a log axis — those are
the sweep turnarounds, where the current has not reversed yet — so the plot states how
many were excluded rather than losing them quietly.

**A Gaussian is fitted to each direction, and σ is reported in meV.** That width is the
quantitative descriptor the literature compares: HOMO distributions typically come out
**55–95 meV** and LUMO **55–65 meV**, with a narrow, intense DOS associated with edge-on
orientation and few film defects, and a broadened one with face-on orientation and
defects. A constant offset is fitted alongside, so an unsubtracted capacitive baseline is
partly absorbed rather than inflating σ.

Two things get flagged rather than reported as measurements:

- **σ at the window width** — the fit has railed against its bound, which means no peak
  is resolved in the range swept, not that the peak is very broad.
- **σ far above ~250 meV** — usually the unsubtracted capacitive baseline, or a sweep
  that does not span the distribution.

**The potential window matters more than anything else here.** Outside the doping range
the current is double-layer charging, not the distribution being measured. On one CV
running −0.5 to +0.7 V, **42% of every curve sat below 0 V** and contributed pure
capacitance to the fit. The *DOS range* controls default to **0 V → sweep max**, applied
to BOTH directions so the two stay comparable and their hysteresis means something.

0 V is a principled default — below it a p-doping film is nominally neutral — but it is
not necessarily the doping **onset**, which is film-dependent and can be several hundred
mV higher. Raise the lower bound if the kinetics say nothing happens until later.

**If the sweep stops before the distribution turns over, there is no Gaussian to fit.**
A Gaussian needs a peak; fitted to a monotonic rising edge it rails against the window
and reports a width describing nothing. The tab then falls back to the **exponential tail
energy E₀** — the slope of log g against E, which is what an edge actually supports, and
a recognized feature of amorphous organic semiconductors in its own right.

E₀ comes with an **R²**, and that is the real guard: a tail that is not straight in log g
still produces a number, and only R² says it means nothing. On one real CV the two
directions gave R² = 0.66 and 0.36, so **neither a Gaussian nor a single exponential
describes that window** — which is a result, not a failure. Only a model the data
supports is drawn.

**Axis orientation.** Both conventions are in use, and which is right depends on what
the figure sits next to:

- **Energy horizontal, log DOS vertical** — what this tab does, and what the
  electrochemical-DOS papers use when the distribution is shown on its own.
- **Energy vertical, DOS horizontal** — the solid-state convention, used when the DOS is
  placed beside a band-structure or energy-level diagram so the two *share* the energy
  axis. If you want to line the DOS up against HOMO/LUMO levels, this is the one.

Nothing about the calculation changes; it is a transpose. The **energy on Y** checkbox
switches between them, and the log scale follows the DOS axis across the swap.

### What this implementation does NOT do yet

**The energy axis is the applied potential, negated — not an absolute scale.** The
convention is to reference to vacuum via an internal ferrocene standard:

```
E(vs vacuum) = −( E_applied vs Fc/Fc⁺ + 4.8 eV )
```

so a HOMO lands around −4.5 to −5.5 eV and can be compared with published values. Until
a reference offset and a measured ferrocene E½ are supplied, the axis here is **relative**
and the absolute numbers should not be compared with the literature.

Two cautions if you add that calibration. A recent absolute-calibration study puts
ferrocene's adiabatic ionisation energy in MeCN at **4.94 ± 0.05 eV** rather than the
customary 4.8, and notes that competing reference scales differ by **up to 0.3 eV**. And
a *pseudo* reference electrode drifts, so it cannot place an absolute scale on its own —
a ferrocene calibration in the same electrolyte is what fixes it.

### References for the DOS treatment

- Bässler *et al.*, *Mapping the Density of States Distribution of Organic Semiconductors
  by Employing Energy Resolved–Electrochemical Impedance Spectroscopy*, **Adv. Funct.
  Mater.** 2021 — Gaussian DOS over two orders of magnitude; σ ≈ 55–95 meV (HOMO),
  55–65 meV (LUMO). <https://advanced.onlinelibrary.wiley.com/doi/10.1002/adfm.202007738>
- *Tailoring the Density of State of n-Type Conjugated Polymers through Solvent
  Engineering for Organic Electrochemical Transistors*, **ACS Appl. Mater. Interfaces**
  2024 — DOS profile vs morphology; narrow/intense for edge-on, broadened for face-on.
  <https://pubs.acs.org/aamick/article-abstract/16/30/39693/1218480/Tailoring-the-Density-of-State-of-n-Type>
- *Absolute Calibration for Cyclic Voltammetry from the Solution-Phase Ionisation of
  Ferrocene*, **ACS Electrochem.** 2026 — ferrocene IE 4.94 ± 0.05 eV in MeCN; scale
  discrepancies up to 0.3 eV. <https://pubs.acs.org/doi/10.1021/acselectrochem.5c00382>
- *Modeling cyclic voltammetry and electrochemical impedance spectroscopy measurements of
  PEDOT:PSS layers with finite density of states*, **J. Appl. Phys.** 2026.
  <https://pubs.aip.org/aip/jap/article-abstract/139/22/225501/3394458/Modeling-cyclic-voltammetry-and-electrochemical>

### The area to enter

**The immersed coated area, one side** — the part below the electrolyte line.

- **Not the whole coated strip.** Film above the meniscus cannot dope; doping needs ion
  insertion, so a dry region contributes no charge however well connected. A 1 × 3 cm strip
  immersed 2 cm is 2 cm², not 3.
- **Not the optical spot.** The current integrates over the whole wetted film whether or
  not it is illuminated.
- It varies with immersion depth, so **check it each run**. The area scales the DOS
  directly; the wrong one gives the right shape at the wrong magnitude.
- Set it to **0** if unknown and the axis falls back to dQ/dV in C/V, rather than reporting
  a magnitude nothing supports.

### What the DOS does NOT do

- **No capacitive baseline subtraction.** Double-layer charging is not density of states,
  but whatever is removed changes the answer, so nothing is removed silently. A roughly
  flat pedestal across the window is the double layer. Bare conductive substrate in contact
  with electrolyte — uncoated margins, pinholes — adds to this without adding film states.
- **No check that the scan rate was slow enough.** That is empirical: run several rates and
  confirm `i/v` collapses onto one curve. If it does not, the result is a rate measurement,
  not a DOS.
- **It assumes a uniform film of known volume.** Patchy coverage breaks that in a way the
  number cannot reveal. Treat absolute magnitudes as approximate; comparisons between runs
  on the same film are the trustworthy part.

---

## Where things are recorded

| What | Where |
|---|---|
| Output file formats | [`data-format.md`](data-format.md) — do not change |
| Analysis design and what real data changed | [`analysis-design.md`](analysis-design.md) |
| Operating procedure | [`sop.md`](sop.md) |
| Open work | [`../TODO.md`](../TODO.md) |
