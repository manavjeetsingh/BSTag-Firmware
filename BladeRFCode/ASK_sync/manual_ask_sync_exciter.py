#!/usr/bin/env python3
"""
bladeRF CW carrier that sends one ASK sync preamble per Enter keypress.

    idle   [1 1 1 1 1 0 0 1 1 0 1 0 1]   idle
    ────┐  |<- 2 ms each, 26 ms total ->|  ┌────────
        └──▔▔▔▔▔▔▔__▔▔__▔_▔_▔───────────┘

The tag's `esync` correlates against this pattern (esync.cpp), locks on the
end of the preamble, and fires its queued command ESYNC_FIRE_DELAY_US later.
The pattern and chip length live in bladerf_cw.py; see README.md in this
folder for why a pattern beats a single blank at low signal.

Type 'q' + Enter (or Ctrl-C) to quit.
"""

import sys

from bladerf_cw import CWExciter, PREAMBLE_S

FREQ_MHZ = 715.1     # emitted tone
GAIN_DB  = 80

if __name__ == "__main__":
    exc = CWExciter(freq_mhz=FREQ_MHZ, gain_db=GAIN_DB)
    exc.start()
    print(f"TX CW at {exc.emitted_mhz():.3f} MHz, gain {exc.gain:g} dB.")
    print(f"Press Enter for a {PREAMBLE_S * 1e3:.0f} ms preamble.  "
          f"'q' + Enter to quit.\n")

    n = 0
    try:
        while True:
            line = sys.stdin.readline()
            if not line or line.strip().lower() in ("q", "quit", "exit"):
                break
            exc.sync()
            n += 1
            print(f"  preamble #{n}")
    except KeyboardInterrupt:
        pass
    finally:
        exc.close()
        print("stopped")
