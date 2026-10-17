"""
Print each Gamry's current- and voltage-channel FILTER settings, as the instrument
reports them, and what toolkitpy would choose for a given sample rate.

Why (2026-10-17): on an Interface 1010E our runs showed a ~3.36 Hz (as sampled at 10
points/s), +-80-114 nA wave on the current that Gamry Framework's own chrono on the
same instrument and dummy did not. Framework's .DTA header records ICHFILTER 3 and
VCHFILTER 3; ours record 1 and 1 -- spec-echem never sets the filters. Before setting
them, this shows what the settings ARE on each model and what `test_ich_filter`
answers, rather than guessing an index.

    conda activate SpecEchem32
    python examples/probe_gamry_filters.py

Read-only and cell-safe: it opens each instrument, reads lists and asks `test_*`
questions, and closes. **It does not switch the cell on, set a filter, apply a
potential, or measure.** No spectrometer and no dummy cell are needed.
"""
import faulthandler
import sys

faulthandler.enable()

RATES_HZ = (1.0, 10.0, 100.0, 1000.0)


def call(obj, name, *args):
    """The result of obj.name(*args), or the error as text: the signatures are not
    documented, so every call reports what happened instead of stopping."""
    try:
        return getattr(obj, name)(*args)
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"


def show_channel(pstat, ch):
    print(f"  {ch.upper()} filter")
    print(f"    now:     {call(pstat, ch + '_filter')}  "
          f"({call(pstat, ch + '_filter_label')})")
    values = call(pstat, ch + "_filter_value_list")
    labels = call(pstat, ch + "_filter_label_list")
    print(f"    values:  {values}")
    print(f"    labels:  {labels}")
    for hz in RATES_HZ:
        print(f"    test_{ch}_filter({hz:g}) -> {call(pstat, 'test_' + ch + '_filter', hz)}")
    print()


def main():
    try:
        import toolkitpy as tkp
    except ImportError as exc:
        print(f"toolkitpy is not importable: {exc}  (conda activate SpecEchem32?)")
        return 1

    # Initialise before enum_sections(): called cold, the process dies silently.
    tkp.toolkitpy_init("spec-echem-filter-list")
    try:
        sections = [str(x) for x in (tkp.enum_sections() or [])]
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list instruments: {exc}; reading the default one)")
        sections = []
    finally:
        tkp.toolkitpy_close()

    failed = 0
    for sec in sections or [None]:
        print("=" * 62)
        tkp.toolkitpy_init("spec-echem-filters")
        pstat = None
        try:
            pstat = tkp.Pstat("PSTAT", sec) if sec else tkp.Pstat("PSTAT")
            print(f"Section: {sec or '(default)'}   {call(pstat, 'label')}\n")
            for ch in ("ich", "vch", "ach"):
                show_channel(pstat, ch)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"Could not read it: {type(exc).__name__}: {exc}")
        finally:
            if pstat is not None:
                try:
                    pstat.close()
                except Exception:  # noqa: BLE001
                    pass
                del pstat
            tkp.toolkitpy_close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
