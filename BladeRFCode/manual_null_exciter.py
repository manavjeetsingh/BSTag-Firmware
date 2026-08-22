#!/usr/bin/env python3
"""
bladeRF CW transmitter with a manually triggered dropout.

Transmits a continuous CW tone. Press Enter and the output drops to zero
for 200 ms, then comes back. Type 'q' + Enter (or Ctrl-C) to quit.

The gate is generated in baseband and applied sample-by-sample, so the
200 ms is exact. The *latency* between your keypress and the dropout is
not exact -- it's however much audio is already queued in the sink's
buffers, typically a few tens of ms. Shrink DEVICE_ARGS buffers if that
matters.
"""

import sys
import threading

import numpy as np

from gnuradio import analog, blocks, gr
import osmosdr

# ---------------- settings ----------------
FREQ        = 915e6          # TX center frequency (LO), Hz
SAMP_RATE   = 2e6            # sample rate, sps
TONE_OFFSET = 100e3          # CW tone offset from LO, Hz (0 = carrier at LO)
AMPLITUDE   = 0.7            # 0..1, keep below 1.0 to avoid clipping

DROP_MS     = 2            # length of the dropout
RAMP_MS     = 1              # edge softening; 0 = hard keying

RF_GAIN     = 50             # overall / VGA2
IF_GAIN     = 20
BB_GAIN     = 20
DEVICE_ARGS = "bladerf=0"
# ------------------------------------------


class TriggeredGate(gr.sync_block):
    """Outputs 1.0 continuously; outputs a drop envelope once triggered."""

    def __init__(self, samp_rate, drop_ms, ramp_ms):
        gr.sync_block.__init__(self, name="triggered_gate",
                               in_sig=None, out_sig=[np.complex64])

        drop = int(round(samp_rate * drop_ms / 1000.0))
        ramp = int(round(samp_rate * ramp_ms / 1000.0))

        if ramp > 0:
            fall = 0.5 * (1 + np.cos(np.pi * np.arange(1, ramp + 1) / (ramp + 1)))
            env = np.concatenate([fall, np.zeros(drop), fall[::-1]])
        else:
            env = np.zeros(drop)

        self.env = env.astype(np.complex64)
        self.pos = len(self.env)          # start already finished == full output
        self.lock = threading.Lock()

    def trigger(self):
        with self.lock:
            self.pos = 0

    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)

        with self.lock:
            pos = self.pos
            take = max(0, min(len(self.env) - pos, n))
            self.pos = pos + take

        if take:
            out[:take] = self.env[pos:pos + take]
        out[take:] = 1.0
        return n


class TX(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "bladeRF CW TX with triggered drop")

        self.tone = analog.sig_source_c(
            SAMP_RATE, analog.GR_COS_WAVE, TONE_OFFSET, AMPLITUDE, 0, 0)

        self.gate = TriggeredGate(SAMP_RATE, DROP_MS, RAMP_MS)
        self.mult = blocks.multiply_cc(1)

        self.sink = osmosdr.sink(args=f"numchan=1 {DEVICE_ARGS}")
        self.sink.set_sample_rate(SAMP_RATE)
        self.sink.set_center_freq(FREQ, 0)
        self.sink.set_freq_corr(0, 0)
        self.sink.set_bandwidth(0.75 * SAMP_RATE, 0)
        self.sink.set_gain(RF_GAIN, 0)
        self.sink.set_if_gain(IF_GAIN, 0)
        self.sink.set_bb_gain(BB_GAIN, 0)

        self.connect(self.tone, (self.mult, 0))
        self.connect(self.gate, (self.mult, 1))
        self.connect(self.mult, self.sink)


if __name__ == "__main__":
    tb = TX()
    tb.start()

    print(f"TX CW at {(FREQ + TONE_OFFSET)/1e6:.3f} MHz.")
    print(f"Press Enter for a {DROP_MS} ms drop.  'q' + Enter to quit.\n")

    n = 0
    try:
        while True:
            line = sys.stdin.readline()
            if not line or line.strip().lower() in ("q", "quit", "exit"):
                break
            tb.gate.trigger()
            n += 1
            print(f"  drop #{n}")
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()
        print("stopped")