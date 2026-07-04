"""
Bench check: toggle DIGOUT0 via toolkitpy — the trigger line to the Avantes.

Purpose: confirm Python can drive the same digital output the .GSequence toggles
(DIGOUT0), independent of any experiment. Watch the Avantes trigger input (or a
scope / LED on the pin) to confirm it sees the HIGH/LOW edges.

    set_digital_out(0x1, 0x1)  -> DIGOUT0 HIGH   (.GSequence "Set Digital Out 0: High")
    set_digital_out(0x0, 0x1)  -> DIGOUT0 LOW
The second arg is the mask: only bit 0 (DIGOUT0) is touched.

Run on the 32-bit Win11 box, in SpecEchem32, with the Gamry connected:
    python examples/bench_digital_out.py
"""
import time

try:
    import toolkitpy as tkp
except ImportError:
    raise SystemExit(
        "toolkitpy not importable — run this on the 32-bit Win11 box (SpecEchem32 env)."
    )

from spec_echem.potentiostat import initialize_pstat

PULSES = 5
HIGH_S = 1.0
LOW_S = 1.0


def main():
    tkp.toolkitpy_init("bench_digital_out")
    pstat = tkp.Pstat("PSTAT")
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    initialize_pstat(pstat)

    print(f"Pulsing DIGOUT0 {PULSES}x ({HIGH_S}s high / {LOW_S}s low). "
          "Watch the Avantes trigger / a scope on the pin.")
    for i in range(PULSES):
        pstat.set_digital_out(0x1, 0x1)
        print(f"  [{i+1}/{PULSES}] HIGH")
        time.sleep(HIGH_S)
        pstat.set_digital_out(0x0, 0x1)
        print(f"  [{i+1}/{PULSES}] LOW")
        time.sleep(LOW_S)

    tkp.toolkitpy_close()
    print("done.")


if __name__ == "__main__":
    main()
