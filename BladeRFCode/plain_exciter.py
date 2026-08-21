#!/usr/bin/env python3
"""
Minimal bladeRF RX: capture IQ to a file. No GUI.

Edit the settings below and run:  ./bladerf_rx.py

Output is complex float32 (I,Q,I,Q,...). Read it back with:
    import numpy as np
    x = np.fromfile("capture.cf32", dtype=np.complex64)
"""

from gnuradio import blocks, gr
import osmosdr

# ---------------- settings ----------------
FREQ        = 915e6          # RX center frequency, Hz
SAMP_RATE   = 2e6            # sample rate, sps
SECONDS     = 5              # how long to capture
OUTFILE     = "capture.cf32"

RF_GAIN     = 6              # LNA   (bladeRF 1.x: 0/3/6 dB; 2.0 micro: 0-60)
IF_GAIN     = 20             # RXVGA1 (5-30 dB, 1.x only)
BB_GAIN     = 20             # RXVGA2 (0-30 dB, 1.x only)
DEVICE_ARGS = "bladerf=0"
# ------------------------------------------


class RX(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "bladeRF RX")

        self.src = osmosdr.source(args=f"numchan=1 {DEVICE_ARGS}")
        self.src.set_sample_rate(SAMP_RATE)
        self.src.set_center_freq(FREQ, 0)
        self.src.set_freq_corr(0, 0)
        self.src.set_bandwidth(0.75 * SAMP_RATE, 0)
        self.src.set_dc_offset_mode(2, 0)      # 2 = automatic
        self.src.set_iq_balance_mode(2, 0)
        self.src.set_gain_mode(False, 0)       # manual gain
        self.src.set_gain(RF_GAIN, 0)
        self.src.set_if_gain(IF_GAIN, 0)
        self.src.set_bb_gain(BB_GAIN, 0)

        self.head = blocks.head(gr.sizeof_gr_complex, int(SAMP_RATE * SECONDS))
        self.sink = blocks.file_sink(gr.sizeof_gr_complex, OUTFILE, False)
        self.sink.set_unbuffered(False)

        self.connect(self.src, self.head, self.sink)


if __name__ == "__main__":
    print(f"RX {FREQ/1e6:.3f} MHz @ {SAMP_RATE/1e6:.3f} Msps "
          f"for {SECONDS}s -> {OUTFILE}")
    RX().run()
    print("done")