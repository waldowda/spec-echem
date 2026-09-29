"""
Export a run's HDF5 into the layout `rajgiriUW/OECT_processing` already reads.

A DERIVED VIEW, never the record. Our archival file keeps raw counts, the dark and
the reference, the CV, pre-dedoping, and full provenance; this layout has nowhere to
put any of them. It exists so a downstream pipeline opens a file with no changes to
its code, not so anything is stored twice.

The target, from `oect_processing/specechem/uvvis_h5.py`:

    /potentials              the ladder
    /charge                  integrated charge per potential, mC
    /{potential}/data        absorbance (n_wavelengths x n_times)
    /{potential}/index       wavelengths
    /{potential}/columns     times
    /current/data|index|columns

Four constraints, all read off his code rather than assumed:

1. **The group key is the potential as a string**, and `convert_h5` calls `float()`
   on every top-level key that is not current/charge/potentials. So NOTHING else may
   sit at the top level. Extra datasets INSIDE a potential group are fine — he reads
   only data/index/columns by name — and attributes are ignored entirely.
2. **The dedoping file is keyed by the DOPING potentials.** His notebook passes the
   same `volts` to both UVVis objects, so `/0.7/` in dedopingdata.h5 means "the
   dedope that FOLLOWED the 0.7 V dope". Keying it by the actual dedoping potential
   would put every cycle on one key and `time_dep_spectra` would overwrite silently.
3. **Key the potential the way HE derives it** — the FIRST sample of the step,
   rounded to 2 dp — not our median. They agree on a clean hold, but his notebook
   looks up `spectra_vs_time[0.7]`, so the key has to match his arithmetic.
4. **`charge` and `current` must always be written.** His `save_h5` wraps them in
   try/except but `convert_h5` reads them unguarded, so a file missing either cannot
   be read back by his own reader.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.integrate import trapezoid

from spec_echem.data import (
    DATA_TYPE_DEDOPING, DATA_TYPE_DOPING, H5PY_AVAILABLE, H5PY_IMPORT_ERROR,
    h5_path, h5py,
)
from spec_echem.logging_config import get_run_logger

# His notebook builds these paths itself, so the names are free — but using his
# exactly means a notebook pointed at the subfolder just works. The SUBFOLDER is
# what marks them as derived, rather than a mangled filename.
OECT_SUBFOLDER = "oect"
OECT_FILENAMES = {DATA_TYPE_DOPING: "dopingdata.h5",
                  DATA_TYPE_DEDOPING: "dedopingdata.h5"}


def _rounded(potential):
    """The potential as the downstream pipeline stores it: rounded to 2 dp.

    read_files.py does `np.round(pp[pot][0], 2)`, so BOTH its group keys and its
    /potentials array carry the rounded value. Ours must too: its `volt()` does a
    searchsorted on /potentials and its notebooks then index spectra_vs_time by that
    result, so an array of raw first samples against rounded keys means every lookup
    misses. CAUGHT 2026-09-29 by comparing our export against its own file for the
    same run, where /potentials read [0.1 ... 0.8] and ours [0.199703, ...].
    """
    return float(np.round(float(potential), 2))


def _potential_key(potential):
    """The group name: the rounded potential, stringified as it stringifies it."""
    return str(_rounded(potential))


def read_cycles(path):
    """[(cycle, potential_first_sample, absorbance_df, echem_df)] from one of ours.

    `potential` is the FIRST sample of the echem trace, matching how read_files.py
    derives it, falling back to potential_set when a segment has no echem.
    """
    out = []
    with h5py.File(path, "r") as f:
        wavelengths = np.asarray(f["wavelength"])
        for key in sorted((k for k in f if k != "wavelength"), key=int):
            g = f[key]
            absorb = pd.DataFrame(np.asarray(g["absorbance_vs_time"], dtype=float),
                                  index=wavelengths,
                                  columns=np.asarray(g["time"], dtype=float))
            echem = None
            potential = g.attrs.get("potential_set")
            if "echem" in g:
                e = g["echem"]
                echem = pd.DataFrame({
                    "time": np.asarray(e["time"], dtype=float),
                    "potential": np.asarray(e["potential"], dtype=float),
                    "current": np.asarray(e["current"], dtype=float),
                })
                if len(echem):
                    potential = echem["potential"].iloc[0]
            if potential is None:
                raise ValueError(
                    f"{Path(path).name} cycle {key} has no potential recorded, so "
                    f"it cannot be exported: the target layout addresses its groups "
                    f"BY potential.\n\nThis is normal for a run taken in External "
                    f"mode — there the sequence file sets the potentials and the "
                    f"software never sees them, so neither the echem trace nor a "
                    f"requested value was stored. Only Python-mode or Autolab runs "
                    f"can be exported.")
            out.append((int(key), float(potential), absorb, echem))
    return out


def _current_frame(cycles, potentials):
    """His `current`: index = time, one column per potential.

    `current_vs_time` takes the time axis from the FIRST step and assumes every step
    shares it — ragged steps raise there. Ours are equal length in a normal run, so
    a mismatch means something unusual; truncate to the shortest and say so rather
    than write a file his reader cannot open.
    """
    traces = [(p, c) for (_, _, _, c), p in zip(cycles, potentials) if c is not None]
    if not traces:
        return None
    lengths = {len(c) for _, c in traces}
    n = min(lengths)
    if len(lengths) > 1:
        get_run_logger().warning(
            "OECT export: the echem traces are not all the same length %s; "
            "truncating every one to %d points, because the target layout puts them "
            "in a single table sharing one time axis.", sorted(lengths), n)
    index = traces[0][1]["time"].to_numpy()[:n]
    return pd.DataFrame({_rounded(p): c["current"].to_numpy()[:n] for p, c in traces},
                        index=index)


def export_oect_h5(run_folder, data_type=DATA_TYPE_DOPING, out_path=None,
                   doping_potentials=None):
    """Write one direction in the downstream layout. Returns the Path.

    `doping_potentials` is how rule 2 is honoured: pass the doping file's potentials
    when exporting the DEDOPING direction, so its groups carry the potential each
    dedope followed.
    """
    if not H5PY_AVAILABLE:
        raise RuntimeError(f"h5py is not importable: {H5PY_IMPORT_ERROR}")
    run_folder = Path(run_folder)
    source = h5_path(run_folder, data_type)
    if not source.is_file():
        raise FileNotFoundError(f"no {source.name} in {run_folder}")

    cycles = read_cycles(source)
    if not cycles:
        raise ValueError(f"{source.name}: no cycles to export")

    potentials = (list(doping_potentials) if doping_potentials is not None
                  else [p for _, p, _, _ in cycles])
    if len(potentials) != len(cycles):
        raise ValueError(
            f"{source.name}: {len(cycles)} cycles but {len(potentials)} potentials "
            f"to key them by — the doping and dedoping ladders must correspond.")

    current = _current_frame(cycles, potentials)
    if out_path is None:
        out_path = run_folder / OECT_SUBFOLDER / OECT_FILENAMES[data_type]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(out_path, "w") as f:
        rounded = np.asarray([_rounded(p) for p in potentials], dtype=float)
        f.create_dataset("potentials", data=rounded)

        # charge is DERIVED, which is why the archive does not store it: computed
        # here the way he computes it, trapezoidal over current, in mC.
        charges = []
        for (_, _, _, echem), _p in zip(cycles, potentials):
            if echem is None or not len(echem):
                charges.append(0.0)
            else:
                charges.append(float(trapezoid(echem["current"].to_numpy(),
                                               x=echem["time"].to_numpy()) * 1e3))
        f.create_dataset("charge", data=np.asarray(charges, dtype=float))

        for (_, _, absorb, _), potential in zip(cycles, potentials):
            g = f.create_group(_potential_key(potential))
            g.create_dataset("data", data=absorb.to_numpy(dtype=float))
            g.create_dataset("index", data=absorb.index.to_numpy(dtype=float))
            g.create_dataset("columns", data=absorb.columns.to_numpy(dtype=float))

        # Always present: save_h5 makes these optional but convert_h5 reads them
        # unguarded, so a file without them cannot be read back by his own reader.
        cg = f.create_group("current")
        if current is None:
            cg.create_dataset("data", data=np.zeros((0, len(rounded))))
            cg.create_dataset("index", data=np.zeros(0))
        else:
            cg.create_dataset("data", data=current.to_numpy(dtype=float))
            cg.create_dataset("index", data=current.index.to_numpy(dtype=float))
        cg.create_dataset("columns", data=rounded)

        # Attributes are ignored by his reader, so they are the safe place to say
        # what this file is. Nothing may go at the TOP LEVEL as a group or dataset.
        f.attrs["generated_by"] = "spec-echem oect_export"
        f.attrs["source_file"] = source.name
        f.attrs["note"] = (
            "A DERIVED VIEW for OECT_processing, not the archival record. The "
            f"complete data — raw counts, dark, reference, provenance — is in "
            f"{source.name} beside this folder.")
    return out_path


def export_run(run_folder, out_dir=None):
    """Both directions, keyed the way the downstream reader expects. [Path, ...]

    `out_dir` sends them somewhere else. Unlike the archival .h5 — which is the run's
    own data and belongs with it — this is a DELIVERABLE: made to hand to someone, or
    to point a notebook at. Wanting it on a shared drive or in an analysis working
    directory is the normal case, not the edge one. The default stays <run>/oect/
    because that is predictable and self-describing.
    """
    run_folder = Path(run_folder)
    out_dir = Path(out_dir) if out_dir else None
    written = []
    doping_potentials = None
    if h5_path(run_folder, DATA_TYPE_DOPING).is_file():
        cycles = read_cycles(h5_path(run_folder, DATA_TYPE_DOPING))
        doping_potentials = [p for _, p, _, _ in cycles]
        written.append(export_oect_h5(
            run_folder, DATA_TYPE_DOPING,
            out_path=(out_dir / OECT_FILENAMES[DATA_TYPE_DOPING]
                      if out_dir else None)))
    if h5_path(run_folder, DATA_TYPE_DEDOPING).is_file():
        # Rule 2: the dedoping file carries the DOPING potentials.
        written.append(export_oect_h5(
            run_folder, DATA_TYPE_DEDOPING,
            out_path=(out_dir / OECT_FILENAMES[DATA_TYPE_DEDOPING]
                      if out_dir else None),
            doping_potentials=doping_potentials))
    return written
