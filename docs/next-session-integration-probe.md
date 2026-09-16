# Next session — probe the ULS2048L's minimum integration time

**One job.** Everything else in this file is context for it.

Written 2026-09-15 for the session on the instrument box at the other lab. Branch
`gui-dev`, 436 tests passing. **`git pull` first.**

---

## Run this

Lamp **on** and stable — stage 3 needs illumination or every row reads the dark floor.

```
python examples\probe_min_integration.py
```

Three stages. Only **stage 3** matters; 1 and 2 just re-confirm what is already known.

## What the other detector gave, for comparison

`AvaSpec-…SP`, 2048 px, SensorType 22:

| stage | result |
|---|---|
| 1. stated minimum | **not exposed** under either spelling |
| 2. `AVS_PrepareMeasure` accepts | 0.009033 ms |
| 3. counts vs exposure | `counts = 112 + 26051·t`, within **1% from 0.009 to 0.1 ms** |

So on that detector the accepted minimum is genuine — short exposures really are
integrated. Above ~0.1 ms counts fall below the fit (13% low at 0.2 ms, 48% at 0.5 ms):
that is saturation at that lamp level, not a detector limit.

## Two possible outcomes, and what each means

**A — counts go flat below ~1 ms.** That is a real floor. It confirms the ~100× spread
between the two detectors and the 1.05 ms recorded in `metrohm-rig-status.md`.

**B — counts track the request all the way down**, as on the other detector. Then the
1.05 ms figure came from a datasheet rather than the hardware, and
`docs/metrohm-rig-status.md` needs correcting.

Either way, **report the stage-3 table**, not just the final line — the knee is read from
the counts column.

## Then wire it (the actual fix)

There is a **live bug** on this detector if outcome A holds:

```
settings.py    lin_start_ms = 0.022    lin_stop_ms = 0.15
this detector  floor ~1.05 ms  (to be confirmed)
```

The entire linearity ramp sits below what the detector can do, so the check measures
nothing. Wiring:

1. `integration_time_ms` and `lin_start_ms` default to
   `AvantesSpectrometer.minimum_integration_time()` at Connect, not to a constant.
   That method already reads the SDK field if present and otherwise bisects
   `AVS_PrepareMeasure`; `FakeSpectrometer.min_integration_time` is settable for tests.
2. Scale `lin_stop_ms` from the floor rather than fixing it. On a 1.05 ms detector the
   stop wants to be nearer 8 ms to show curvature; 0.15 is right only for a fast one.
3. Reject an out-of-range exposure with a clear message instead of letting the poll loop
   time out, which is the symptom otherwise.

## Do not re-derive these

- **The SDK does not expose a minimum integration time.** A full `DeviceConfigType` dump
  shows only pixel count, sensor type, gains, offsets and calibration polynomials. Manual
  snippets showing `m_Detector.m_MinIntegrationTime` are the C API or a newer wrapper.
- **Host timing cannot measure the floor.** USB round trip plus a 2048-pixel readout is
  ~1.5 ms with ~0.6 ms of scatter, larger than any request below 1 ms — a 0.05 ms request
  came back *faster* than a 0.009 ms one. An elapsed-time method reported a floor of
  0.1 ms that was pure artifact. **Use counts.**
- This wrapper returns the struct from `AVS_GetParameter(handle, 63484)` with
  **flattened** names (`devcon.m_Detector_m_NrPixels`), not nested.

## House rules for this repository

It is **public**. Read the marked section at the top of `CLAUDE.md` before committing.
In short: no personal or institution names in code, docs or commit messages; refer to
hardware by **model number**; sample identity and unpublished interpretation live in
`private-notes/`, which is outside this repo.
