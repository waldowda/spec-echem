# In-GUI analysis — design

Agreed with Dean 2026-09-14. Companion to the TODO entry; this is the version to build from.

**The premise: the value is analysis DURING a run.** On 2026-09-11 a film collapsed after a +0.8 V
excursion, nothing said so until the files were analysed later, and the next run was spent on a
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
- publication figures: axis choices, normalisation, colour maps
- charge integration with baseline decisions that should not be made silently

---

## Build order

The maths first (`spec_echem/analysis.py`, no Qt, no hardware, testable against synthetic data),
then the GUI. Dean: *"testing the code is going to be needed for further design tweaks"* — so the
parts that can be tested without a GUI should be right before the GUI exists.
