#!/usr/bin/env python3
"""
bladeRF CW transmitter with a manually triggered Manchester sync packet.

Transmits a continuous CW tone. Press Enter and the output emits one sync
packet, then goes back to plain CW. Type 'q' + Enter (or Ctrl-C) to quit.

The packet is what XIAO-ESP32-C6_firmware_wifi/esync.cpp looks for, so its
shape has to agree with the ESYNC_* constants in that sketch's config.h.
The settings block below mirrors them; the two must be edited together.

    idle      SFD (40 ms)     guard   8 chips, 2 ms each        idle
    ────┐                   ┌─────┐ ┌──┐    ┌──┐  ┌────┐  ┌───────────
        └───────────────────┘     └─┘  └────┘  └──┘    └──┘
                            ▲
                          t_sfd -- the tag's one timing reference

Only that first rising edge is timed. The 40 ms blank is a deliberate
Manchester code violation: inside the payload no run can exceed one bit
period, so a blank that long cannot be payload, and the frame start stays
unambiguous no matter where in the cycle a tag began listening. That is
also why the payload itself is free to be any 4-bit value, including the
degenerate 1010 -- phase comes from the SFD, never from the payload.

Everything after the SFD is validation. The tag bins the chips, checks
that both halves of every bit differ, and then fires its queued command at
a fixed offset from t_sfd rather than on any payload edge -- so all tags
fire at the same instant regardless of how their decode timing fell.

The id increments on every trigger, which is how a run confirms that every
tag locked onto the *same* packet: compare the id each one hands back in
its `esyncr` reply.
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

# --- packet shape: mirrors ESYNC_* in the firmware's config.h ---
SFD_MS      = 40.0           # ESYNC_SFD_MIN_US .. ESYNC_SFD_MAX_US
GUARD_MS    = 4.0            # ESYNC_GUARD_US
CHIP_MS     = 2.0            # ESYNC_CHIP_US   (half a bit period)
PKT_BITS    = 4              # ESYNC_PKT_BITS

RAMP_MS     = 0.5            # raised-cosine edge, centred on each boundary
LEAD_MS     = 1.0            # carrier before the SFD, holds the first ramp
TAIL_MS     = 2.0            # carrier after the payload, holds the last ramp

RF_GAIN     = 80             # overall / VGA2
IF_GAIN     = 20
BB_GAIN     = 20
DEVICE_ARGS = "bladerf=0"
# ------------------------------------------


def manchester_chips(value, nbits):
    """
    Chip sequence for `value`, MSB first, IEEE 802.3 convention:
    a 1 bit is low-then-high, a 0 bit is high-then-low. Two chips per bit,
    so every bit carries a transition at its centre and no run anywhere in
    the payload is longer than one bit period.
    """
    chips = []
    for k in range(nbits - 1, -1, -1):
        bit = (value >> k) & 1
        chips += [0, 1] if bit else [1, 0]
    return chips


def build_packet(samp_rate, packet_id):
    """One packet as a 0..1 envelope, starting and ending with carrier on."""
    def nsamp(ms):
        return int(round(samp_rate * ms / 1000.0))

    runs = [(1.0, LEAD_MS), (0.0, SFD_MS), (1.0, GUARD_MS)]
    runs += [(float(c), CHIP_MS) for c in manchester_chips(packet_id, PKT_BITS)]
    runs += [(1.0, TAIL_MS)]

    env = np.concatenate([np.full(nsamp(ms), lvl, dtype=np.float32)
                          for lvl, ms in runs])

    ramp = nsamp(RAMP_MS)
    if ramp >= 2:
        # Centred on the boundary, so the 50 % point stays exactly where the
        # run list puts it. That instant is what the tag times off, and it
        # is also the steepest part of the edge -- where a mismatch in
        # received amplitude between tags costs the least timing error.
        w = 0.5 * (1 - np.cos(np.pi * np.arange(1, ramp + 1) / (ramp + 1)))
        pos = 0
        for i in range(len(runs) - 1):
            pos += nsamp(runs[i][1])
            a, b = runs[i][0], runs[i + 1][0]
            if a == b:
                continue
            start = pos - ramp // 2
            env[start:start + ramp] = a + (b - a) * w

    return env


class SyncGate(gr.sync_block):
    """Outputs 1.0 continuously; emits one packet envelope per trigger."""

    def __init__(self, samp_rate):
        gr.sync_block.__init__(self, name="manchester_sync_gate",
                               in_sig=None, out_sig=[np.complex64])
        self.samp_rate = samp_rate
        self.env = np.zeros(0, dtype=np.complex64)
        self.pos = 0
        self.lock = threading.Lock()

    def trigger(self, packet_id):
        # Built here rather than in work(): the id changes per trigger, and
        # the flowgraph thread must not be kept waiting on numpy.
        env = build_packet(self.samp_rate, packet_id).astype(np.complex64)
        with self.lock:
            self.env = env
            self.pos = 0

    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)

        with self.lock:
            env = self.env
            pos = self.pos
            take = max(0, min(len(env) - pos, n))
            self.pos = pos + take

        if take:
            out[:take] = env[pos:pos + take]
        out[take:] = 1.0
        return n


class TX(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "bladeRF CW TX with Manchester sync packet")

        self.tone = analog.sig_source_c(
            SAMP_RATE, analog.GR_COS_WAVE, TONE_OFFSET, AMPLITUDE, 0, 0)

        self.gate = SyncGate(SAMP_RATE)
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
    packet_ms = LEAD_MS + SFD_MS + GUARD_MS + 2 * PKT_BITS * CHIP_MS + TAIL_MS

    tb = TX()
    tb.start()

    print(f"TX CW at {(FREQ + TONE_OFFSET)/1e6:.3f} MHz.")
    print(f"Press Enter to send a {packet_ms:.0f} ms sync packet.  "
          f"'q' + Enter to quit.\n")

    packet_id = 0
    try:
        while True:
            line = sys.stdin.readline()
            if not line or line.strip().lower() in ("q", "quit", "exit"):
                break
            tb.gate.trigger(packet_id)
            print(f"  packet id={packet_id} ({packet_id:0{PKT_BITS}b})")
            packet_id = (packet_id + 1) % (1 << PKT_BITS)
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()
        print("stopped")
