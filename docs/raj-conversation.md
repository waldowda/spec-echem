# Conversation with Raj — H5 layout, core analyses, and two reader bugs

Assembled 2026-09-11, revised 2026-09-14. Companion to the HDF5 and in-GUI-analysis
items in [`../TODO.md`](../TODO.md).

---

## ▶ CONVERSATION WITH RAJ — the questions to ask (assembled 2026-09-11, revised 2026-09-14)

Ordered so the first answer settles the most. **The two reader bugs are NOT on this list** — they
belong in a pull request, not a conversation (see the separate section below).

### A. The H5 layout

1. **Would you read an acquisition-written H5 at all, or keep your pipeline on the ascii?**
   *Ask this first — it gates everything else.* If the answer is no, this is purely a disk-space
   feature for us, questions 2–6 are moot, and the layout is entirely ours to choose.
2. **Is the H5 an analysis convenience, or the archival record?** His `save_h5` keeps absorbance
   and current only — no raw counts, no dark, no reference, which are columns 3/4/5 of our
   8-column format. Match him exactly and the H5 is lossy, so the ascii stays the archive and
   nothing is saved on disk. A superset is no longer his format and `convert_h5()` would not read
   it unchanged.
   - Measured sizes for a 14-segment run (**645 MB ascii today**): his layout as written (float64)
     **51.6 MB**, absorbance-only float32 **25.9 MB**, complete archival **38.8 MB**. Carrying
     *everything* costs ~50% more than the minimum and is still smaller than his current file.
     **Lead with these** — the trade is much softer than it sounds.
3. **Does the ascii keep being written forever?** Our position is "in addition, never instead",
   because `docs/data-format.md` says DO NOT CHANGE and his reader depends on the naming. But is
   there a future in which it retires, or is it permanent? That changes whether the H5 is a
   convenience or the eventual primary.
4. **Metadata as HDF5 attributes** — instrument identities, serials, `build_id()`, the settings
   snapshot. Our run folders are self-documenting today; an H5 that travels without that is a step
   backwards. Would `attrs` break anything of his? (It should not — `convert_h5()` reads named
   datasets.)
5. **float32 for absorbance?** His `df.values` is float64. Absorbance does not support seven
   significant figures, and float32 halves the file.
6. **One file per run**, rather than per potential — the wavelength axis is then stored once
   instead of once per segment, which is where most of the saving is.
7. If we write a **neutral superset**, would he rather we ship a converter to his layout, or adapt
   his reader? Ours has to stay vendor-neutral — it must survive an Ocean Optics spectrometer or a
   third potentiostat without change.

### A2. Two things that will hurt later if skipped now

8. **A `schema_version` attribute from day one.** If two repos read this file, the first format
   change without a version is painful for both. Cheapest possible insurance, and it has to be
   agreed before anything is written.
9. **Where does the written spec live** — our `docs/data-format.md`, his repo, or both? An unowned
   format drifts. `docs/data-format.md` exists precisely because that already happened once with
   the 8-column layout.

### B. The core analyses

10. **Which `UVVis` methods are load-bearing, and which are historical?** Our shortlist for
    during-a-run use is `abs_vs_voltage`, `single_wl_time` and a spectrogram; `banded_fits` and the
    rest look like Jupyter work. Does that match how he actually works?
11. **What does he look at FIRST** when judging whether a run is worth continuing? That is the
    number that belongs on screen mid-acquisition — on 2026-09-11 it would have caught film A
    collapsing during `pbttt2` rather than after `pbttt3` had been spent on a dead film.
12. **Are there analyses he does that are NOT in the repo?** Ad-hoc notebook work often is the real
    practice, and the committed methods may not reflect it.

### C. The question that finds what we have not thought of

13. **"What about our output makes your life harder today?"** Everything above is us asking him.
    This is how the unreported problems surface — the `read_files.py` regression sat for four
    months and only came to light because someone went looking.

### D. Worth opening with

14. The chain today is: we write ~150 MB of text per spectra file **so that he can parse it into
    ~6 MB of H5**. Neither side wants the intermediate — it is only the handoff format. That framing
    makes the whole question easy, because it is not "adopt our format", it is "delete a step we
    both pay for".

---

## ▶ SEND RAJ A PULL REQUEST — two reader bugs (verified 2026-09-11 and 2026-09-14)

**Not conversation items.** Both were verified here; one already has a working patch in the local
tree. Discussed-and-agreed bugs get forgotten; a PR does not. This also protects the existing fix,
which currently lives only in an uncommitted working copy where the next `git pull` could discard it.

**Bug 1 — pre-dedoping files are read as dedoping files.** The classifier does
`if 'dedopingspectra(' in name`, and `prededopingspectra(0).txt` *contains* that substring:

```
prededopingspectra(0).txt -> dedopespecfiles   WRONG
prededoping(0).txt        -> dedopestepfiles   WRONG
```

Fix: test for `prededoping` before the `dedoping` branches, or use `startswith`. Matters now that
pre-dedoping runs by default and is not always discarded.

**Bug 2 — the May 2026 `specfiles` → `stepfiles` regression is still upstream.** Verified
2026-09-14 by diffing the local copy against `b6abee5`: the patch is exactly three lines, all
reading potential from the spectra files instead of the step files.

```python
-    first = pd.read_csv(specfiles[0], header=0, sep='\t', nrows=1)
+    first = pd.read_csv(stepfiles[0], header=0, sep='\t', nrows=1)
-    potentials = np.zeros(len(specfiles))
+    potentials = np.zeros(len(stepfiles))
-    for x, fl in enumerate(specfiles[1:], start=1):
+    for x, fl in enumerate(stepfiles[1:], start=1):
```

Potential lives in the step files (`WE(1).Potential (V)`). The 8-column spec-echem format is
correct and needs no change — this is entirely a reader bug. Has been sitting in `CLAUDE.md` as
"notify Raj" for months.

Both fit in one PR against `oect_processing/specechem/read_files.py`.
