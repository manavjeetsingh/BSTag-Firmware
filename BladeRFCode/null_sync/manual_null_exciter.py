#!/usr/bin/env python3
"""
bladeRF CW transmitter with a manually triggered sync blank.

This is the exciter the tag firmware syncs off. Transmits a continuous CW
tone; press Enter and the output blanks for DROP_MS, then comes back.
Type 'q' + Enter (or Ctrl-C) to quit.

    idle        blank        idle
    ────┐                 ┌────────
        └─────────────────┘
        ^<--- 50 ms  --->^
     t_fall          the tag fires here

The tag times off the FALLING edge that starts the blank and fires
ESYNC_FIRE_DELAY_US later by dead reckoning, so it lands as the carrier
comes back without having to watch for the rise.

That makes DROP_MS below and ESYNC_FIRE_DELAY_US in the firmware's
config.h the same number, written out in two places that cannot check
each other. Keep them equal, and keep N here equal to ESYNC_TIME_SCALE
there, with the tag reflashed to match: the detector works in absolute
microseconds and cannot infer the scale off the air.

Getting it wrong does NOT stop the tag firing, which is what makes it
worth checking deliberately. The tag commits at the falling edge, so a
mismatch just moves when it fires -- early if the tag's number is short,
out past the end of the blank if it is long. `esyncr` reports low_us, the
blank as the tag actually measured it; that is the check, and it should
read back as DROP_MS.

The gate is generated in baseband and applied sample-by-sample, so the
blank length is exact. The *latency* between your keypress and the blank
is not exact -- it's however much is already queued in the sink's
buffers, typically a few tens of ms. That latency does not matter: the
tag times off the edge itself, not off when you pressed the key.
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

N           = 1              # == ESYNC_TIME_SCALE in config.h

# MUST equal ESYNC_FIRE_DELAY_US in config.h -- the tag dead-reckons this
# length from the falling edge rather than measuring it, so the two are
# one number kept in two files. Also has to stay above
# ESYNC_BLANK_MIN_US, or the tag throws the blank away as a fade.
DROP_MS     = 50.0 * N       # length of the sync blank

# Deliberately NOT scaled by N. The tag latches the falling edge when the
# signal reaches its floor (ESYNC_MIN_BASELINE_MV), so a longer ramp just
# moves that instant later by roughly the ramp length and drags every
# tag's fire along with it (measured on the host bench: +2 us with a fixed
# ramp at any N, -7.4 ms at N=100 with a scaled one). Stretch the blank if
# you need to; keep its edges sharp.
RAMP_MS     = 0              # >0 = edge softening; 0 = hard keying

RF_GAIN     = 80             # overall / VGA2
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

        # Carrier either side of the blank, wide enough to hold a ramp.
        pad  = max(ramp, 1)
        runs = [(1.0, pad), (0.0, drop), (1.0, pad)]
        env  = np.concatenate([np.full(n, lvl, dtype=np.float32)
                               for lvl, n in runs])

        if ramp >= 2:
            # Each ramp is centred on its boundary, not laid outside it, so
            # the 50 % crossings stay exactly `drop` apart however long the
            # ramp is. That matters now that the tag dead-reckons DROP_MS
            # from the falling crossing: a ramp laid outside the boundary
            # would stretch the real blank to drop + ramp and put every
            # tag's fire a half-ramp late.
            w = 0.5 * (1 - np.cos(np.pi * np.arange(1, ramp + 1) / (ramp + 1)))
            pos = 0
            for i in range(len(runs) - 1):
                pos += runs[i][1]
                a, b = runs[i][0], runs[i + 1][0]
                start = pos - ramp // 2
                env[start:start + ramp] = a + (b - a) * w

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
        gr.top_block.__init__(self, "bladeRF CW TX with sync blank")

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
    print(f"Press Enter for a {DROP_MS:.0f} ms blank.  'q' + Enter to quit.\n")

    n = 0
    try:
        while True:
            line = sys.stdin.readline()
            if not line or line.strip().lower() in ("q", "quit", "exit"):
                break
            tb.gate.trigger()
            n += 1
            print(f"  blank #{n}")
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()
        print("stopped")