# In-GUI analysis — design

Agreed with Dean 2026-09-14. Companion to the TODO entry; this is the version to build from.

**The premise: the value is analysis DURING a run.** On 2026-09-11 a film collapsed after a +0.8 V
excursion, nothing said so until the files were analyzed later, and the next run was spent on a
sample that was already dead. A modulation number on screen would have caught it.

Everything exploratory stays in Jupyter. This is "is this run worth continuing?", plus enough
fitting to compare optical and electrical kinetics without leaving the app.

---

## Two tabs, because they are two activities

| | **Tab 4 — Results** | **Tab 5 — Analysis** |
|---|---|---|
| when | live, during the run | after the run |
| attention | a glance | working |
| controls | almost none | model, window, re-fit |
| updates | as each segment's files land | on demand |

An earlier draft put both in tab 4. That was scoped to three automatic plots; once fitting brought
model selection, adjustable windows and re-runs, the control density would have made the live view
worse at the one thing it is for.

---

## Tab 4 — the glance

Three plots, reading from **disk** as each segment completes — the same path as reloading a run from
last week, so there is one code path and one failure mode rather than two.

1. **Modulation** — absorbance at one wavelength vs potential across the ladder. One point per
   segment, so it *builds* during the run. This is the "is the film still working" plot.
2. **Kinetics** — absorbance vs time for the selected segment.
3. **Spectrogram** — the 2-D map for the selected segment.

### Auto-wavelength — the signed difference, not the magnitude

On doping, π→π* **bleaches** while the polaron band **grows**. Taking `|ΔA|` would pick whichever is
larger — often the bleach — and silently show the π band while the user believed they were watching
the polaron. The signed form gives both, for free:

```
ΔA = A(late) − A(early)
λ_polaron = argmax(ΔA)      the growth
λ_π       = argmin(ΔA)      the bleach
```

`λ_polaron` is the default; `λ_π` is available; a manual entry overrides both. Dean's point that the
π band is findable from a *single* spectrum also makes a free cross-check: if `argmin(ΔA)` lands far
from the neutral film's λmax, something is wrong with the run.

---

## Tab 5 — the fitting

### Model — one choice, applied to both

`exp` / `biexp` / `stretched`, **the same model for absorbance and for current**, because comparing
them is the entire point. Single exponential is where people start; real decays usually are not.

```
exp        y = A + B·exp(−t/τ)
biexp      y = A + B₁·exp(−t/τ₁) + B₂·exp(−t/τ₂)
stretched  y = A + B·exp(−(t/τ)^β)
```

### Window — and the capacitive spike

Start/stop times, adjustable, shaded on the trace so it is visible what is being fitted.

**Default start = the time of peak |current|.** A potential step draws a large capacitive transient
before ion kinetics dominate — 625 µA against a 30 µA settled value on 2026-09-11 — and fitting
through it wrecks a single exponential. The current peak is exactly where that spike ends, it is
computed rather than guessed, and it anchors both datasets to the same instant.

**`biexp` is already the spike-aware model**: a fast component plus a slow one *is* capacitance plus
ion motion. Spike handling is not a fourth option, it is biexp with the window opened up.

### Fitting is on a button, not automatic

Re-running with a different model and a refined window is the workflow, not an error path.

### Output — per segment

| | τ | β | SD |
|---|---|---|---|
| absorbance | | | |
| current | | | |
| charge | | | |

Both current **and** charge: charge is smoother, and it is the more physical comparison against
absorbance, since absorbance tracks polaron *population* rather than rate.

SD from `sqrt(diag(pcov))`. `curve_fit` already returns the covariance; Raj's `banded_fits` discards
it. A fit whose SD is a large fraction of τ is flagged rather than printed as a confident number —
`curve_fit` returns nonsense with a huge covariance rather than raising.

### τ vs potential, with a ratio toggle

- **individual** — τ_abs, τ_current, τ_charge overlaid vs potential
- **ratio** — τ_abs / τ_current vs potential: does the optical response track charge injection, or
  lag it, and does that change with doping level?

Both are useful; hence a toggle rather than a choice.

**The ratio uses MEAN relaxation times, not raw τ.** For a stretched exponential τ alone is not the
physical timescale, so a ratio of raw τ would be wrong in a way that looks entirely plausible:

```
exp        ⟨τ⟩ = τ
biexp      ⟨τ⟩ = (B₁τ₁ + B₂τ₂) / (B₁ + B₂)        amplitude-weighted
stretched  ⟨τ⟩ = (τ/β)·Γ(1/β)
```

The table still shows τ and β, because those are what you tune the fit against. The ratio view
computes ⟨τ⟩ internally.

Two consequences to build in deliberately:

- **The ratio view has gaps where the individual view has points** — any potential where one of the
  two fits failed. That is informative, and should look intentional rather than silently skipped.
- **Both fits must share a model** for the ratio to mean anything. One dropdown drives both, so this
  holds by construction; keep it that way.

---

## Stays in Jupyter

- `banded_fits` across a wavelength band — fitting per wavelength, needing residual inspection
- comparison across runs (film A vs film B, this week vs last)
- publication figures: axis choices, normalization, color maps
- charge integration with baseline decisions that should not be made silently

---

## Density of states from the CV — BUILT 2026-09-15 (v1)

Dean, 2026-09-14: *"I wonder if we could plot density of states from the CV curves. Maybe at
some point."* **Not built.** Recorded here so the design is settled before anyone starts.

For a sweep slow enough to be quasi-equilibrium, the current IS the differential charge:

```
i = v · dQ/dV                    v = scan rate (V/s)

g(E) = i / (v · e · V_film)      states eV⁻¹ cm⁻³,   E = −eV
```

Units check: (C/s)·(s/V)/(C·cm³) = eV⁻¹cm⁻³.

So the shape of `g(E)` is just the CV with its x-axis flipped to energy and its y-axis divided
by constants. **The physics is not in the arithmetic — it is in the four decisions below**, which
is why this is a design note and not a one-line plot.

### Where it goes — Dean, 2026-09-15

*"For density of states, it seems it could be another option in the Optical View when CV
is chosen. Does that make sense to you?"*

Yes, and it is better than a new tab. Tab 4's view selector already switches what the
optical canvas shows for the selected segment — Spectra / Kinetics / Modulation — and a
DOS is another view of the CV that is already selected. It needs no new navigation, and
selecting the CV is already how you say "I want to look at the sweep".

- **Enabled only for a CV segment.** The other three views apply to a chrono step; this
  one applies only to a sweep, so it greys out otherwise rather than producing nonsense.
  That matches the existing behavior where a CV gets no automatic polaron band.
- **It also puts the two DOS estimates in the same place.** The electrochemical one comes
  from `CV.txt`; the spectroscopic one from `CVspectra.txt` — the same sweep, same
  segment, same selector. That is exactly the comparison worth having (see below), and
  splitting them across tabs would make it awkward.
- Film volume stays an optional input; without it the axis reads dQ/dV in C/V and says
  so, rather than inventing a volume.

### What is already on disk

- `CV.txt` — potential and current, both sweep directions, all 3 cycles.
- `cv_scan_rate` — in the run metadata, so `v` needs no new input.
- `CVspectra.txt` — the optical side of the same sweep, which is the interesting part (below).

### The one missing input

**Film volume — thickness × active area.** Nothing in spec-echem records either, and without it
the result is arbitrary units, not a DOS. This needs a home: most naturally the Parameters tab
alongside sample name and electrolyte, saved into the run metadata so a folder stays
self-describing. Thickness usually comes from profilometry or ellipsometry done elsewhere, so it
is a typed value, not a measured one — and it should be OPTIONAL, with the plot falling back to
`dQ/dV` in C/V and saying so on the axis rather than inventing a volume.

### Four decisions that must not be made silently

1. **The capacitive baseline.** Double-layer charging is not density of states. Some of the
   current is non-faradaic and subtracting it changes the answer, especially at the low-potential
   end where the real signal is smallest. Whatever is subtracted has to be visible.
2. **Which cycle, and which direction.** The runs sweep 3 cycles and the film is not the same
   on cycle 1 as on cycle 3. Forward and reverse disagree (hysteresis); averaging them hides a
   real effect, and showing one hides the other. Default to the last cycle, plot both directions.
3. **Whether the scan rate was slow enough.** The equation above assumes equilibrium at every
   potential. The test is empirical: run several scan rates and check `i/v` collapses onto one
   curve. If it does not, the number is a rate measurement, not a DOS. This is a bench protocol,
   not code — but the GUI should not present a DOS that has never had this check.
4. **Ionic vs electronic charge.** In an OMIEC the counter-ion motion is part of the measured
   current. The CV alone cannot separate them.

### Why this rig can do better than a CV alone

Decision 4 is where the spectrometer earns its place. The polaron absorbance is an INDEPENDENT
measure of how much charge went in, so `ΔA(V)` from `CVspectra.txt` gives a *spectroscopic* DOS
against the *electrochemical* one from the same sweep. Where they agree, the current was
electronic; where they diverge, it was not. That comparison is the reason to build this here
rather than in a generic echem tool, and it reuses the band-selection work already done for the
kinetics (see *Auto-wavelength*, above).

### The 95% CI is a WITHIN-MODEL number — read the residual split beside it

⟨τ⟩'s interval comes from the delta method on the full covariance,
σ² = ∇f'C∇f, with Student *t* on (n−p). That propagation is sound — validated against
Monte Carlo to 2% (stretched) and 16% (biexp), and it reduces exactly to `tau_sd` for a
single exponential.

**What it does NOT cover is whether the model is right.** `curve_fit`'s covariance
assumes independent residuals; a systematic misfit breaks that, and no widening of the
bar fixes a wrong model — it hides it. So the interval is reported as-is and labelled
as what it is: given THIS model over THIS window, how well ⟨τ⟩ is pinned.

(An earlier version of this note proposed scaling the CI by √(n/n_eff) from the residual
autocorrelation. That was wrong: the AR(1) effective-sample-size formula assumes
stationary correlated NOISE, and reads a smooth systematic trend as near-perfect
correlation, returning a meaningless number — 14 from 601 points. The fit still has
n − p = 597 degrees of freedom.)

The honest companion is the **residual split**, on the legend:

```
resid: noise 3.2e-04, model-miss 1.5e-03 (1.4% of swing)
```

Successive differences cancel any smooth trend, so the point-to-point scatter IS the
measurement noise; the RMS above that is a curve the model failed to follow. It ranks
models directly — MEASURED on `the 20250710 reference run` Doping 0 @ 800 nm:

| model | noise | model-miss | ratio | % of swing |
|---|---|---|---|---|
| exp | 1.7e-4 | 1.7e-3 | 10.2× | 1.6% |
| **biexp** | 1.8e-4 | **3.9e-4** | **2.2×** | **0.4%** |
| stretched | 3.2e-4 | 1.5e-3 | 4.6× | 1.4% |

biexp leaves four times less systematic residual than stretched here. Dean: *"we don't
have better models currently"* — so the split is there to show how far short the
available ones fall, not to choose among a richer set.

**This ranking is specific to THIS system, not a general result.** Dean: *"the conclusion
about biexp compared to strexp is likely in the context of this particular OMIEC system
and maybe different for a different system."* One sample, one electrolyte, one band. A
different OMIEC — different ion, different morphology, more dispersive transport — may
well rank stretched above biexp. Re-read the split per system; do not carry this table
forward as a default.

Two timescales also need not mean two PROCESSES in one material; depending on the
sample they may be two populations. That distinction changes what τ₁ and τ₂ mean without
changing either number, and it is checkable: if they are populations that respond at
different potentials, the amplitude ratio |B₁|/(|B₁|+|B₂|) should trend across the
ladder rather than stay flat.

### Prefactor signs carry physics — MEASURED 2026-09-15

For a sum-of-parts model the prefactors of one process pull the same way. Opposite signs
mean competing processes or a fit gone wrong, so `FitResult.mixed_amplitude_signs` says
so on the legend rather than deciding which.

On `the 20250710 reference run`, fitted biexp:

| rung | polaron 800 nm | π–π* 550 nm |
|---|---|---|
| +0.2 … +0.5 V | same sign | same sign |
| **+0.6 V** | **MIXED** | same sign |
| **+0.7 V** | **MIXED** | same sign |

Dean predicted the asymmetry before it was checked: a bipolaron growing at high doping
steals from the polaron band, while π–π* simply keeps bleaching and stays single-signed.
Fit instability would have appeared at both wavelengths. **A second probe on the
bipolaron band would separate the two components properly** — the tab already fits any
wavelength typed; what is missing is fitting two at once and comparing.

Note the sign convention this implies: at 800 nm the prefactors are NEGATIVE (the
polaron band RISES to its plateau A) and at 550 nm POSITIVE (π–π* falls). A negative
prefactor is a direction, not an error, which is why A and y(0) are on the legend.

### Planned — a third component, and joint fits across two bands

Dean, 2026-09-15, explicitly *for later*. Recorded so the design is settled before
anyone starts.

**Tri-exponential for the bipolaron.** *"For the bipolaron changing the polaron, the fit
would likely be a tri-exponential. Same two exps as in pi-pi* and an added one to remove
two polarons to form one bipolaron."*

    y = A + B₁e^(−t/τ₁) + B₂e^(−t/τ₂) + B₃e^(−t/τ₃)

Seven parameters, so it needs the good data and probably bounds. The payoff is that the
third component has a **predicted signature**: it consumes polarons, so it should appear
with OPPOSITE sign at the polaron band and be absent or much weaker at π–π*. That is
exactly the asymmetry `mixed_amplitude_signs` already detects — fires at 800 nm above
+0.5 V, never at 550 nm. The flag is currently the only evidence of a process the models
cannot represent; a tri-exponential would let it be measured instead of merely flagged.

**Joint fit with shared τ.** *"One could even do dual fits of both polaron and pi-pi*
with the same tau parameters being optimized as they should be directly related."*

This is the structurally right answer and probably worth more than a third component on
its own. The two bands are two views of ONE ion-motion process, so the timescales are not
independent measurements to be compared after the fact — they are the same numbers seen
twice.

- **Shape:** one parameter vector with SHARED τ (and β), and per-band amplitudes and
  baselines. Stack the two traces into a single residual vector and fit once; `curve_fit`
  needs only a wrapper that unpacks a concatenated x. No new solver.
- **Why it is better than two fits:** it roughly halves the free timescales while
  doubling the data constraining them, so τ comes out far better determined — and the
  per-band amplitudes then carry the interesting physics, since they are what actually
  differs between the bands.
- **The assumption IS the hypothesis.** A shared-τ fit asserts both bands follow the same
  kinetics. Where that holds it is a much stronger measurement; where it fails — the
  polaron band above +0.5 V, if the bipolaron reading is right — it will fail VISIBLY, in
  the residual split. That failure is a result, not a problem.
#### Why the joint fit is NECESSARY, not just better — Dean, 2026-09-15

*"I actually have some good derivations that I and Claude did before regarding the
polaron fits including the leaking of polaron abs to bipolaron abs even though we can't
see the true bipolaron since we are limited to ~1100nm. Exactly why to use a dual fit
from pi-pi* and polaron to define tau1 and tau2 then add tau3 for bipolaron kinetics."*

**The bipolaron band is not observable on this instrument.** Silicon QE runs out by
~1050 nm; MEASURED on the UW ULS2048L, 66 counts of signal above floor at 1100 nm, 17 at
1123.7 nm, zero past 1150 (`docs/bench-2026-09-04.md`). Seeing it needs an InGaAs
detector, not a config change. So bipolaron formation is visible ONLY as polaron
absorbance going missing — a deficit, never a peak.

That is what makes the fit order matter:

1. **τ₁, τ₂ come from the BAND PAIR.** π–π* and the polaron share the ion-motion
   kinetics, so a shared-τ fit across both determines those timescales using twice the
   data and none of the bipolaron ambiguity.
2. **τ₃ is then whatever is left at the polaron band.** With τ₁ and τ₂ pinned, the
   polaron band's shortfall against the shared kinetics is the bipolaron channel, and
   its sign is negative by construction — polarons being consumed.

Fitting the polaron band alone cannot separate these. A free tri-exponential has three
timescales and three amplitudes competing to explain one curve, and it will happily
trade a wrong τ₃ against a wrong τ₁ and land somewhere plausible. **τ₃ is not
identifiable without the constraint the second band provides.** That is the argument for
the joint fit, and it is stronger than "the timescales should agree".

**Dean's derivations for the polaron → bipolaron leakage are not in this repo.** They are
the specification for the τ₃ term and should be captured before anyone implements it —
`private-notes/` if they carry sample specifics, `docs/` if not.

- **Sequencing:** shared-τ joint fit FIRST, on the rungs where the prefactor signs agree
  (+0.2…+0.5 V), which is where it should work and where it validates the machinery. Let
  it break at +0.6 and +0.7 V. Then add τ₃ to fix exactly that break.

### Sequencing

After the τ-vs-potential ladder is validated on a real above-V_th run. The ladder is the feature
with a user waiting on it, and it shares the band-selection code that a spectroscopic DOS needs —
so the DOS gets that machinery for free, but only once it is trustworthy.

---

## Build status (2026-09-14)

- **`spec_echem/analysis.py`** — done. Auto-wavelength, the three models, ⟨τ⟩, fit-with-SD,
  ratio-or-None. 18 tests against synthetic data with known answers.
- **Tab 5 — Analysis** — done. Model, window, wavelength, fit buttons, the τ/β/SD table, and
  τ-vs-potential with the ratio toggle. 6 tests.
- **Tab 4 — the live views** — done. A view selector on the optical canvas: *Spectra* (unchanged,
  still the default), *Kinetics*, *Modulation*. 5 tests.
### What real data changed (2026-09-14, `20260709_P3HT_01` + the PLU rig)

The list above said these needed real data. They got it, and most were wrong:

- **Auto-wavelength did NOT survive real noise.** It picked 381 nm — the dark floor, 416 counts,
  SNR 1.8 — over the real polaron at 780 nm (SNR 682), for every segment. Now gated on
  significance against per-pixel noise, plus a 410 nm floor (`ANALYSIS_WL_MIN`): Dean confirms
  this rig has no usable data below ~410 nm.
- **The polaron is not always the band that grows.** True on doping; on DEDOPING the polaron
  decays while π–π* recovers. The tab maps by data type, and `auto_wavelengths` is named
  `(grows, bleaches)` so the next caller cannot repeat the assumption.
- **The current peak is NOT a useful window start.** On a step the spike peaks at the FIRST
  sample, so it resolves to t = 0 and excludes nothing. Left as the default by Dean's call — the
  RC is unknown on this rig — with the window made tunable and the shading live.
- **`FIT_SD_REJECT_FRACTION = 0.5` holds up.** It correctly rejected +0.3 V (0.0016 OD, the film
  does not dope) and +0.5 V (still accelerating at 30 s, no timescale in the window) while
  accepting +0.7 V. Rejecting is the right answer for both.
- **τ and β are now bounded** — τ > 0, 0 < β ≤ 1 (above 1 is a compressed exponential, a claim
  this model does not make). `BETA_MIN = 0.05` because ⟨τ⟩ = (τ/β)·Γ(1/β) overflows past
  1/β ≈ 170.

**Still open:** a fit whose τ vastly exceeds the observation window passes the SD check — the
charge integral at +0.3 V returned τ = 1.3×10⁵ s in a 30 s window with a tiny SD, because a
straight line is a very well-determined exponential with an enormous τ. A bound like
`τ < 10 × span` is the obvious fix; the multiplier is Dean's call.

**Still needed:** a ladder with ≥3 steps above V_th (~0.6/0.8/1.0 V). `20260709_P3HT_01` reaches
above V_th only at its top step, so the τ-vs-potential plot has one usable point.

## Build order

The maths first (`spec_echem/analysis.py`, no Qt, no hardware, testable against synthetic data),
then the GUI. Dean: *"testing the code is going to be needed for further design tweaks"* — so the
parts that can be tested without a GUI should be right before the GUI exists.
