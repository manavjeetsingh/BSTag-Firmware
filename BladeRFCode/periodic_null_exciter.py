#!/usr/bin/env python3
"""
bladeRF CW transmitter with periodic blanking. No GUI, no files.

Every PERIOD_S seconds the output drops to zero for BLANK_MS milliseconds,
then comes back. Ctrl-C to stop.

The blanking is done in baseband (amplitude gated to 0), not by calling
set_gain() on the hardware. A USB gain write takes an unpredictable few
milliseconds to land and isn't sample-aligned, so it can't produce a clean
"few ms" notch. Multiplying by a gate waveform is exact to the sample.

    |<-------------- 1.000 s -------------->|
    ___                                   ___
       |_____|                               |_____|
        20 ms
"""

import numpy as np

from gnuradio import analog, blocks, gr
import osmosdr

# ---------------- settings ----------------
FREQ        = 915e6          # TX center frequency (LO), Hz
SAMP_RATE   = 2e6            # sample rate, sps
TONE_OFFSET = 100e3          # CW tone offset from LO, Hz (0 = carrier at LO)
AMPLITUDE   = 0.8            # 0..1, keep below 1.0 to avoid clipping

PERIOD_S    = 1.0            # blank once per this many seconds
BLANK_MS    = 200             # how long the output stays at zero
RAMP_MS     = 1              # edge softening; 0 = hard keying (see note below)

RF_GAIN     = 50             # overall / VGA2
IF_GAIN     = 20
BB_GAIN     = 20
DEVICE_ARGS = "bladerf=0"
# ------------------------------------------

GATE_RES_HZ = 1000           # gate resolution: 1000 -> 1 ms steps


def build_gate():
    """One period of the gate envelope, at GATE_RES_HZ (1 ms per slot)."""
    slots = int(round(PERIOD_S * GATE_RES_HZ))
    blank = int(round(BLANK_MS * GATE_RES_HZ / 1000.0))
    ramp = int(round(RAMP_MS * GATE_RES_HZ / 1000.0))

    if blank + 2 * ramp >= slots:
        raise ValueError("BLANK_MS + ramps do not fit inside PERIOD_S")

    g = np.ones(slots, dtype=np.float32)
    g[:blank] = 0.0
    if ramp > 0:
        # raised-cosine edges: rise just after the blank, fall just before it
        # (the vector repeats, so the tail ramp precedes the next blank)
        w = 0.5 * (1 - np.cos(np.pi * np.arange(1, ramp + 1) / (ramp + 1)))
        g[blank:blank + ramp] = w
        g[slots - ramp:] = w[::-1]
    return g


class TX(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "bladeRF gated CW TX")

        interp = int(round(SAMP_RATE / GATE_RES_HZ))
        if interp * GATE_RES_HZ != int(SAMP_RATE):
            raise ValueError("SAMP_RATE must be a multiple of GATE_RES_HZ")

        # CW tone
        self.tone = analog.sig_source_c(
            SAMP_RATE, analog.GR_COS_WAVE, TONE_OFFSET, AMPLITUDE, 0, 0)

        # Gate: 1 ms slots, repeated forever, then stretched to full rate
        gate = [complex(v) for v in build_gate()]
        self.gate_src = blocks.vector_source_c(gate, repeat=True)
        self.gate_up = blocks.repeat(gr.sizeof_gr_complex, interp)

        self.mult = blocks.multiply_cc(1)

        self.sink = osmosdr.sink(args=f"numchan=1 {DEVICE_ARGS}")
        self.sink.set_sample_rate(SAMP_RATE)
        self.sink.set_center_freq(FREQ, 0)
        self.sink.set_freq_corr(0, 0)
        self.sink.set_bandwidth(0.75 * SAMP_RATE, 0)
        self.sink.set_gain(RF_GAIN, 0)
        self.sink.set_if_gain(IF_GAIN, 0)
        self.sink.set_bb_gain(BB_GAIN, 0)

        self.connect(self.gate_src, self.gate_up, (self.mult, 1))
        self.connect(self.tone, (self.mult, 0))
        self.connect(self.mult, self.sink)


if __name__ == "__main__":
    tb = TX()
    print(f"TX CW at {(FREQ + TONE_OFFSET)/1e6:.3f} MHz, "
          f"blanking {BLANK_MS} ms every {PERIOD_S} s. Ctrl-C to stop.")
    tb.start()
    try:
        tb.wait()
    except KeyboardInterrupt:
        tb.stop()
        tb.wait()
        print("\nstopped")