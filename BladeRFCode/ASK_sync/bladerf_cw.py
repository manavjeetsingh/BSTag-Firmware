#!/usr/bin/env python3
"""
The bladeRF CW exciter with the ASK sync preamble, as an object code can drive.

    exc = CWExciter(freq_mhz=915, gain_db=60)
    exc.start()
    exc.set_freq(920)   # MHz, as emitted
    exc.set_pwr(55)     # EXC_POWER: TX gain in dB, see below
    exc.sync()          # one Barker preamble
    exc.close()

manual_ask_sync_exciter.py is this with a keypress for the trigger;
data_collection/exciters.py builds one in the run's own process, and
bladerf_exciter_server.py serves one to a run on another machine.

BARKER_CODE and CHIP_MS must match ESYNC_CODE and ESYNC_CHIP_US in the tag's
config.h. A mismatch never fires the tags at the wrong time -- they just never
lock -- so check these first when esyncr's peak_rho stays low.

Needs gnuradio and osmosdr, which are GRC-provided and not in the repo's .venv:
run it under a GNU Radio python (on the lab Windows box,
C:\\ProgramData\\radioconda\\python.exe).

POWER IS GAIN: a bladeRF has no calibrated output level, so EXC_POWER is TX
gain in dB here, clamped to GAIN_MIN_DB..GAIN_MAX_DB. Anything below
GAIN_MIN_DB means off, and mutes the carrier in baseband.
"""

import threading
import time

import numpy as np

from gnuradio import analog, blocks, gr
import osmosdr

FREQ            = 915e6      # TX LO, Hz
SAMP_RATE       = 2e6        # sps
TONE_OFFSET     = 100e3      # tone sits this far above the LO, clear of LO leakage
AMPLITUDE       = 0.7        # 0..1, below 1.0 to avoid clipping

BARKER_CODE     = (1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1)  # == ESYNC_CODE
CHIP_MS         = 2.0                                        # == ESYNC_CHIP_US / 1000
PREAMBLE_S      = len(BARKER_CODE) * CHIP_MS / 1e3

DEFAULT_GAIN_DB = 60
GAIN_MIN_DB     = 0
GAIN_MAX_DB     = 89         # osmosdr's TX gain ceiling for the bladeRF
IF_GAIN         = 20
BB_GAIN         = 20
DEVICE_ARGS     = "bladerf=0"


def preamble_envelope(samp_rate=SAMP_RATE, code=BARKER_CODE, chip_ms=CHIP_MS):
    """One preamble as a 0/1 gate, hard-keyed: the tag's correlator lines
    up against exact chip edges, and soft ones would only blur them."""
    chip = int(round(samp_rate * chip_ms / 1e3))
    return np.repeat(np.array([1.0 if c > 0 else 0.0 for c in code],
                              dtype=np.complex64), chip)


class SyncGate(gr.sync_block):
    """1.0 at rest, 0.0 while muted, one preamble each time it is triggered.

    Applied sample by sample, so chip lengths are exact. The latency from
    trigger() to the air is whatever is queued in the sink (tens of ms),
    which does not matter: the tags time off the preamble, not the host.
    """

    def __init__(self, samp_rate):
        gr.sync_block.__init__(self, name="sync_gate",
                               in_sig=None, out_sig=[np.complex64])
        self.env = preamble_envelope(samp_rate)
        self.pos = len(self.env)     # nothing pending
        self.muted = False
        self.syncs = 0
        self.lock = threading.Lock()

    def trigger(self):
        """Queue one preamble; False (and nothing queued) while muted."""
        with self.lock:
            if self.muted:
                return False
            self.pos = 0
            self.syncs += 1
            return True

    def set_muted(self, muted):
        with self.lock:
            self.muted = bool(muted)

    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)
        with self.lock:
            if self.muted:
                # Drop a preamble cut off by the mute rather than let its
                # tail surface once the carrier is back.
                self.pos = len(self.env)
                out[:] = 0.0
                return n
            pos = self.pos
            take = min(len(self.env) - pos, n)
            self.pos = pos + take
        out[:take] = self.env[pos:pos + take]
        out[take:] = 1.0
        return n


class CWExciter(gr.top_block):
    """A CW carrier on the bladeRF that can be tuned, levelled and synced.

    Same set_freq/set_pwr as the GPIB Exciter, plus sync(), which the GPIB
    generator cannot do: 2 ms chips need sample-exact keying.
    """

    def __init__(self, freq_mhz=None, gain_db=DEFAULT_GAIN_DB,
                 device_args=DEVICE_ARGS, muted=False):
        gr.top_block.__init__(self, "bladeRF CW TX with ASK sync")
        self.freq = FREQ
        self.gain = gain_db
        self._running = False

        tone = analog.sig_source_c(SAMP_RATE, analog.GR_COS_WAVE, TONE_OFFSET,
                                   AMPLITUDE, 0, 0)
        self.gate = SyncGate(SAMP_RATE)
        mult = blocks.multiply_cc(1)

        self.sink = osmosdr.sink(args=f"numchan=1 {device_args}")
        self.sink.set_sample_rate(SAMP_RATE)
        self.sink.set_center_freq(self.freq, 0)
        self.sink.set_freq_corr(0, 0)
        self.sink.set_bandwidth(0.75 * SAMP_RATE, 0)
        self.sink.set_if_gain(IF_GAIN, 0)
        self.sink.set_bb_gain(BB_GAIN, 0)

        self.connect(tone, (mult, 0))
        self.connect(self.gate, (mult, 1))
        self.connect(mult, self.sink)

        if freq_mhz is not None:
            self.set_freq(freq_mhz)
        self.set_gain_db(gain_db)
        self.gate.set_muted(muted)

    def start(self, *args, **kwargs):
        gr.top_block.start(self, *args, **kwargs)
        self._running = True

    def close(self):
        """Stop transmitting. Safe to call twice."""
        if not self._running:
            return
        self._running = False
        self.stop()
        self.wait()

    def set_freq(self, freq):
        """Put the EMITTED tone on `freq` MHz; the LO goes TONE_OFFSET below."""
        self.freq = float(freq) * 1e6 - TONE_OFFSET
        self.sink.set_center_freq(self.freq, 0)
        return self.emitted_mhz()

    def emitted_mhz(self):
        return (self.freq + TONE_OFFSET) / 1e6

    def set_gain_db(self, gain_db):
        self.gain = max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(gain_db)))
        self.sink.set_gain(self.gain, 0)
        return self.gain

    def set_pwr(self, pwr):
        """EXC_POWER as TX gain in dB; below GAIN_MIN_DB mutes. Returns
        (gain_db, muted)."""
        if float(pwr) < GAIN_MIN_DB:
            self.gate.set_muted(True)
            return self.gain, True
        gain = self.set_gain_db(pwr)
        self.gate.set_muted(False)
        return gain, False

    def sync(self):
        """Send one preamble. Returns (t_start, t_end) as host wall clock, for
        bookkeeping only; the sleep keeps the caller from moving on while it
        is still on the air."""
        t_start = time.time()
        if not self.gate.trigger():
            raise ValueError("muted, no carrier to key")
        time.sleep(PREAMBLE_S)
        return t_start, time.time()

    def describe(self):
        with self.gate.lock:
            muted, syncs = self.gate.muted, self.gate.syncs
        return (f"freq={self.emitted_mhz():.6f}MHz gain={self.gain:g}dB "
                f"muted={int(muted)} syncs={syncs}")
