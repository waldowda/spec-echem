"""
Read converted Gamry echem data files for plotting.

These are the clean tab-separated .txt files produced by the DTA->txt conversion
step (see notebooks/gamry_dta_conversion.ipynb), co-located with the spectra in
the run folder. This module only READS that clean output — it has no third-party
dependency and is Qt-free. (Raw .DTA parsing lives in the conversion step, which
currently uses the gamry_parser library.)

File shapes:
  CV.txt              2 cols: WE(1).Potential (V), WE(1).Current (A)
  steps(N).txt        doping chrono     5 cols (see CHRONO_COLUMNS)
  dedoping(N).txt     dedoping chrono   5 cols
  prededoping(N).txt  pre-dedoping chrono (if produced)
"""
import pandas as pd

POTENTIAL_COL = "WE(1).Potential (V)"
CURRENT_COL = "WE(1).Current (A)"
CV_COLUMNS = [POTENTIAL_COL, CURRENT_COL]
CHRONO_COLUMNS = ["Time (s)", "Corrected time (s)", POTENTIAL_COL, CURRENT_COL, "Index"]


def _require_columns(df, expected, path):
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing expected column(s) {missing}; got {list(df.columns)}")


def read_cv(path):
    """
    Read a converted CV file -> DataFrame with columns [potential, current].
    Plot current vs potential (I vs E).
    """
    df = pd.read_csv(path, sep="\t")
    _require_columns(df, CV_COLUMNS, path)
    return df


def read_chrono(path):
    """
    Read a converted chronoamperometry file (doping / dedoping / pre-dedoping)
    -> DataFrame with columns [Time (s), Corrected time (s), potential, current, Index].
    Plot current vs corrected time (I vs t).
    """
    df = pd.read_csv(path, sep="\t")
    _require_columns(df, CHRONO_COLUMNS, path)
    return df
