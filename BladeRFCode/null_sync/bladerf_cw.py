#!/usr/bin/env python3
"""
The bladeRF CW exciter, as a thing other code can hold and drive.

manual_null_exciter.py is this same transmitter with a keypress for a
trigger, which a frequency sweep cannot press. This module is the flowgraph
and the gate with no trigger attached, so a caller can supply its own:

    exc = CWExciter(freq_mhz=915, gain_db=60)
    exc.start()
    exc.set_freq(920)        # MHz, as emitted
    exc.set_pwr(55)          # EXC_POWER -- TX gain in dB here, see below
    exc.blank(0.030)         # one sync blank
    exc.close()

`data_collection/exciters.py` drives it exactly like that, in the same
process as the run, which is the normal case: one machine, the bladeRF in
its USB port. `bladerf_exciter_server.py` wraps the same object in a line
protocol for the other case, a bladeRF on a different machine from the tags.

    idle        blank        idle
    /////|                 |////////
         |_________________|
         ^<--- 50 ms  --->^
      t_fall          the tag fires here

Same contract as the manual version: the tag times off the FALLING edge and
fires ESYNC_FIRE_DELAY_US later by dead reckoning, so the blank length is
the caller's to choose (ESYNC_NULL_HOLD_S in configurations.json) rather
than being fixed here. Keep N below equal to ESYNC_TIME_SCALE in config.h.

This module imports gnuradio and osmosdr, which are GRC-provided and not in
the repo's .venv, so whatever runs it has to be a GNU Radio python -- on the
lab Windows box, C:\\ProgramData\\radioconda\\python.exe.

POWER IS GAIN
-------------
EXC_POWER in configurations.json is handed to whichever exciter a run has,
and it means whatever that exciter's level control means: dBm on the GPIB
generator, which has a calibrated output, and TX GAIN IN dB here, because a
bladeRF does not. Nothing is mapped or converted -- what you set is what
goes to the radio, clamped to GAIN_MIN_DB..GAIN_MAX_DB.

So an EXC_POWER carried over from an rf_gen run is not a level here, just a
small gain: 12.9 dBm of generator is a normal carrier, 12.9 dB of bladeRF
gain is nearly nothing. Set EXC_POWER for the exciter you are running (60 is
a reasonable starting point, DEFAULT_GAIN_DB below), and take any dB figure
from a bladeRF run as a knob position, not a measured level.

A request below GAIN_MIN_DB has no gain to mean, so it means off: the
carrier is muted in baseband. That is what the tooling's "park the exciter
at -30 between runs" does here, and a zeroed sample is a deeper off than
minimum gain.
"""

import threading
import time

import numpy as np

from gnuradio import analog, blocks, gr
import osmosdr

# ---------------- settings ----------------
FREQ            = 915e6      # TX center frequency (LO), Hz -- retunable
SAMP_RATE       = 2e6        # sample rate, sps
TONE_OFFSET     = 100e3      # CW tone offset from LO, Hz (0 = carrier at LO)
AMPLITUDE       = 0.7        # 0..1, keep below 1.0 to avoid clipping

N               = 1          # == ESYNC_TIME_SCALE in config.h

# Deliberately NOT scaled by N, and 0 by default. The tag latches the
# falling edge when the signal reaches its floor, so a longer ramp just
# moves that instant later and drags every tag's fire along with it. Stretch
# the blank if you need to; keep its edges sharp.
RAMP_MS         = 0          # >0 = edge softening; 0 = hard keying

# Blank lengths a caller is allowed to ask for. The low end is
# ESYNC_BLANK_MIN_US (10 ms): shorter and the tag throws the blank away as a
# fade. The high end is only a stop against a fat-fingered number -- whether
# a length is USEFUL (roughly, whether it is under ESYNC_FIRE_DELAY_US, or
# the tags sweep an unlit scene) is checked in esync_mpp.py, which is where
# the firmware's constants are mirrored.
MIN_BLANK_MS    = 10.0 * N
MAX_BLANK_MS    = 2000.0 * N

# Level -- see POWER IS GAIN above. A run sets this itself (EXC_POWER);
# DEFAULT_GAIN_DB is only what the carrier comes up at before it does.
DEFAULT_GAIN_DB = 60         # overall TX gain at startup
GAIN_MIN_DB     = 0
GAIN_MAX_DB     = 89         # osmosdr's overall TX gain ceiling for bladeRF

IF_GAIN         = 20
BB_GAIN         = 20
DEVICE_ARGS     = "bladerf=0"
# ------------------------------------------


class ControlGate(gr.sync_block):
    """Outputs 1.0 continuously; 0.0 while muted; a drop envelope once fired.

    The same gate as manual_null_exciter.py's TriggeredGate, with the blank
    length moved from a constant to an argument (the caller owns it now) and
    a mute added, so "carrier off" costs no gain write.

    The blank is generated in baseband and applied sample-by-sample, so its
    LENGTH is exact. Its latency is not: a trigger takes effect only once the
    samples already queued in the sink have drained, typically a few tens of
    ms. That does not matter -- the tag times off the edge itself, not off
    when the host asked for it.
    """

    def __init__(self, samp_rate, ramp_ms):
        gr.sync_block.__init__(self, name="control_gate",
                               in_sig=None, out_sig=[np.complex64])
        self.samp_rate = samp_rate
        self.ramp_ms = ramp_ms
        self.env = None
        self.pos = 0
        self.muted = False
        self.blanks = 0
        self.lock = threading.Lock()

    def _build_env(self, drop_ms):
        drop = int(round(self.samp_rate * drop_ms / 1000.0))
        ramp = int(round(self.samp_rate * self.ramp_ms / 1000.0))

        # Carrier either side of the blank, wide enough to hold a ramp.
        pad = max(ramp, 1)
        runs = [(1.0, pad), (0.0, drop), (1.0, pad)]
        env = np.concatenate([np.full(n, lvl, dtype=np.float32)
                              for lvl, n in runs])

        if ramp >= 2:
            # Each ramp is centred on its boundary, not laid outside it, so
            # the 50 % crossings stay exactly `drop` apart however long the
            # ramp is -- see manual_null_exciter.py for why that matters to
            # a tag that dead-reckons the blank length.
            w = 0.5 * (1 - np.cos(np.pi * np.arange(1, ramp + 1) / (ramp + 1)))
            pos = 0
            for i in range(len(runs) - 1):
                pos += runs[i][1]
                a, b = runs[i][0], runs[i + 1][0]
                start = pos - ramp // 2
                env[start:start + ramp] = a + (b - a) * w

        return env.astype(np.complex64)

    def trigger(self, drop_ms):
        env = self._build_env(drop_ms)
        with self.lock:
            self.env = env
            self.pos = 0
            self.blanks += 1

    def set_muted(self, muted):
        with self.lock:
            self.muted = bool(muted)

    def state(self):
        with self.lock:
            return self.muted, self.blanks

    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)

        with self.lock:
            if self.muted:
                # A blank inside a mute is moot: drop whatever is pending so
                # it cannot surface later, when the carrier is back and some
                # tag is listening for an edge.
                self.env = None
                self.pos = 0
                out[:] = 0.0
                return n
            env = self.env
            pos = self.pos
            take = 0 if env is None else max(0, min(len(env) - pos, n))
            self.pos = pos + take
            if env is not None and self.pos >= len(env):
                self.env = None
                self.pos = 0

        if take:
            out[:take] = env[pos:pos + take]
        out[take:] = 1.0
        return n


class CWExciter(gr.top_block):
    """A CW carrier on the bladeRF that can be tuned, levelled and blanked.

    Answers to the same set_freq/set_pwr the GPIB `Exciter` does, so a run
    does not care which of the two is illuminating the tags, plus blank() --
    the one thing the generator cannot do properly, since a baseband gate is
    exact to the sample where a pair of GPIB writes is however long the
    writes took.
    """

    def __init__(self, freq_mhz=None, gain_db=DEFAULT_GAIN_DB,
                 samp_rate=SAMP_RATE, tone_offset=TONE_OFFSET,
                 amplitude=AMPLITUDE, ramp_ms=RAMP_MS,
                 device_args=DEVICE_ARGS, muted=False):
        gr.top_block.__init__(self, "bladeRF CW TX with triggered sync blank")

        self.samp_rate = samp_rate
        self.tone_offset = tone_offset
        self.freq = FREQ
        self.gain = gain_db
        self._running = False

        self.tone = analog.sig_source_c(
            samp_rate, analog.GR_COS_WAVE, tone_offset, amplitude, 0, 0)

        self.gate = ControlGate(samp_rate, ramp_ms)
        self.mult = blocks.multiply_cc(1)

        self.sink = osmosdr.sink(args=f"numchan=1 {device_args}")
        self.sink.set_sample_rate(samp_rate)
        self.sink.set_center_freq(self.freq, 0)
        self.sink.set_freq_corr(0, 0)
        self.sink.set_bandwidth(0.75 * samp_rate, 0)
        self.sink.set_gain(self.gain, 0)
        self.sink.set_if_gain(IF_GAIN, 0)
        self.sink.set_bb_gain(BB_GAIN, 0)

        self.connect(self.tone, (self.mult, 0))
        self.connect(self.gate, (self.mult, 1))
        self.connect(self.mult, self.sink)

        if freq_mhz is not None:
            self.set_freq(freq_mhz)
        self.set_gain_db(gain_db)
        self.gate.set_muted(muted)

    # --- lifecycle -------------------------------------------------------

    def start(self, *args, **kwargs):
        gr.top_block.start(self, *args, **kwargs)
        self._running = True

    def close(self):
        """Stop transmitting and let the radio go.

        Safe to call twice: a run's teardown path and an interpreter exiting
        both want this, and neither knows about the other.
        """
        if not self._running:
            return
        self._running = False
        self.stop()
        self.wait()

    # --- what a run asks of an exciter -----------------------------------

    def set_freq(self, freq):
        """Put the EMITTED tone on `freq` MHz (same units as Exciter).

        The tone sits tone_offset above the LO (it is there to keep the
        carrier clear of LO leakage), so the LO goes below the request by
        that much. Callers ask for the frequency they are measuring at.
        """
        self.freq = float(freq) * 1e6 - self.tone_offset
        self.sink.set_center_freq(self.freq, 0)
        return self.emitted_mhz()

    def emitted_mhz(self):
        return (self.freq + self.tone_offset) / 1e6

    def set_gain_db(self, gain_db):
        self.gain = max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(gain_db)))
        self.sink.set_gain(self.gain, 0)
        return self.gain

    def set_pwr(self, pwr):
        """The run's EXC_POWER, which for this exciter is TX gain in dB.

        Returns (gain_db, muted). A request below GAIN_MIN_DB is not a gain
        at all, so it is read as off -- see POWER IS GAIN in the module
        docstring.
        """
        if float(pwr) < GAIN_MIN_DB:
            self.gate.set_muted(True)
            return self.gain, True
        gain = self.set_gain_db(pwr)
        self.gate.set_muted(False)
        return gain, False

    def blank(self, hold_s):
        """Take the carrier to zero for `hold_s`, then bring it back.

        This is the sync blank the tags time off: they latch the falling
        edge and dead-reckon from it. Returns (t_drop, t_restore) as host
        wall clock, which is bookkeeping only -- the real blank starts once
        the samples already queued in the sink have drained, a few tens of
        ms after this call, and the tags do not care because they time off
        the edge rather than off anything the host knows.
        """
        ms = hold_s * 1e3
        if not (MIN_BLANK_MS <= ms <= MAX_BLANK_MS):
            # Refused rather than clamped: a clamp would blank for a length
            # nobody asked for, and the tags would still fire -- just at the
            # wrong moment, with nothing on the air to say so.
            raise ValueError(f"blank {ms:g}ms outside "
                             f"{MIN_BLANK_MS:g}..{MAX_BLANK_MS:g}ms")
        if self.gate.state()[0]:
            raise ValueError("muted, no carrier to blank")

        t_drop = time.time()
        self.gate.trigger(ms)
        # The gate's length is exact; this sleep is here so the caller does
        # not go on to the next round while the carrier is still down.
        time.sleep(hold_s)
        return t_drop, time.time()

    def carrier_off(self):
        self.gate.set_muted(True)

    def carrier_on(self):
        self.gate.set_muted(False)

    def describe(self):
        muted, blanks = self.gate.state()
        return (f"freq={self.emitted_mhz():.6f}MHz gain={self.gain:g}dB "
                f"muted={int(muted)} blanks={blanks}")
