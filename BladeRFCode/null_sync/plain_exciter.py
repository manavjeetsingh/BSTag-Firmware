#!/usr/bin/env python3
"""
Minimal bladeRF CW transmitter. No GUI, no files.

Edit the settings below and run:  ./bladerf_tx_cw.py
Ctrl-C to stop.

TONE_OFFSET = 0    -> unmodulated carrier exactly at FREQ (DC in baseband,
                      sits on top of LO leakage)
TONE_OFFSET = 1e5  -> carrier at FREQ + 100 kHz, cleanly separated from the LO
"""

import signal
import sys

from gnuradio import analog, gr
import osmosdr

# ---------------- settings ----------------
FREQ        = 915e6          # TX center frequency (LO), Hz
SAMP_RATE   = 2e6            # sample rate, sps
TONE_OFFSET = 100e3          # CW tone offset from LO, Hz (0 = carrier at LO)
AMPLITUDE   = 0.7            # 0..1, keep below 1.0 to avoid clipping

RF_GAIN     = 50             # overall / VGA2
IF_GAIN     = 20
BB_GAIN     = 20
DEVICE_ARGS = "bladerf=0"
# ------------------------------------------


class TX(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "bladeRF CW TX")

        # Complex exponential = single-sided CW tone.
        self.src = analog.sig_source_c(
            SAMP_RATE, analog.GR_COS_WAVE, TONE_OFFSET, AMPLITUDE, 0, 0)

        self.sink = osmosdr.sink(args=f"numchan=1 {DEVICE_ARGS}")
        self.sink.set_sample_rate(SAMP_RATE)
        self.sink.set_center_freq(FREQ, 0)
        self.sink.set_freq_corr(0, 0)
        self.sink.set_bandwidth(0.75 * SAMP_RATE, 0)
        self.sink.set_gain(RF_GAIN, 0)
        self.sink.set_if_gain(IF_GAIN, 0)
        self.sink.set_bb_gain(BB_GAIN, 0)

        self.connect(self.src, self.sink)


if __name__ == "__main__":
    tb = TX()
    print(f"TX CW at {(FREQ + TONE_OFFSET)/1e6:.3f} MHz "
          f"(LO {FREQ/1e6:.3f} MHz + {TONE_OFFSET/1e3:.1f} kHz). Ctrl-C to stop.")
    tb.start()
    try:
        tb.wait()
    except KeyboardInterrupt:
        tb.stop()
        tb.wait()
        print("\nstopped")