# config/ — bench settings

Two plain-text INI files live here. They hold **bench settings** — the things that
describe *this rig* (lamp, ND filter, detector, machine paths), not *this experiment*.

Experiment settings — sample, electrolyte, folder name, CV vertices, potentials — are a
different thing entirely, and are saved/loaded per run as JSON from the **Parameters** tab.

| File | Tracked in git? | What it's for |
|---|---|---|
| `defaults.ini` | **Yes** | The **lab-wide** standard. What a fresh clone starts with. |
| `bench.ini` | **No** (gitignored) | **This machine's** overrides. Created by *Save as defaults*. |

## Which one do I edit?

**Setting up your own rig** — a new lamp, a different ND filter, a new integration time,
this machine's data folder?

> Set the values in the GUI, then click **Instrument → Save as defaults**. That writes
> `bench.ini`. It's plain text, so you can also hand-edit it afterwards (close the app
> first — *Save as defaults* rewrites the whole file).

**Changing the standard for the whole group, permanently?**

> Edit `defaults.ini` and commit it.

⚠️ **Don't edit `defaults.ini` to tweak your own rig.** It will work today and then
collide on the next `git pull`. That collision is the entire reason these are two files.

## Precedence

Lowest to highest — each layer overrides the one above it:

1. Code defaults (`spec_echem/settings.py`) — the floor; guarantees the app runs.
2. `config/defaults.ini` — lab-wide, tracked.
3. `config/bench.ini` — this machine, untracked.
4. An experiment settings JSON — only when you explicitly load one.

## Why some settings are *not* in `defaults.ini`

`data_root` and `potentiostat_mode` are **machine-specific** and are deliberately absent
from the tracked file:

- **`data_root`** is a Windows path on the instrument box and something else on a dev Mac.
  Committing it would make every `git pull` a conflict.
- **`potentiostat_mode`** — `python` needs 32-bit `EchemToolkitPy`; a 64-bit environment
  can only do `external`. Committing either value would assert something false on half the
  machines.

Each rig writes its own via *Save as defaults*. A unit test enforces this boundary in both
directions (`tests/test_bench.py`), so it can't quietly drift.

## If you break it

The reader is deliberately forgiving, because a file you're invited to hand-edit will
eventually contain a typo. A bad value is **skipped with a visible warning** (shown on the
Instrument tab) and the layer below it stands — the app still launches. To start over,
click **Restore factory defaults**, which deletes `bench.ini` and falls back to the lab
defaults.
