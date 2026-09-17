"""Host-side exciter sync for MPP over WiFi.

Why this exists
---------------
Over serial, telling the Tx tag to sweep and the Rx tags to capture lands
close enough together that the capture contains the sweep. Over WiFi it does
not: the association, lwIP and the AP's scheduling put tens of milliseconds
of jitter between the four sockets, against MPP dwells of 3 ms. The Rx
captures end up straddling the sweep, or missing it.

esync removes the host from the timing path entirely. Every tag is told in
advance what to run, then arms a detector and puts its radio to sleep; the
exciter blanks its carrier once; every tag sees the same falling edge and
fires its staged command ESYNC_FIRE_DELAY_US later by dead reckoning. The
host's own jitter no longer matters, because the host is not what starts the
sweep -- the edge is, and all the tags share it.

The blank itself is BladeRFCode/null_sync/manual_null_exciter.py's, which is
the shape the firmware's detector was written against but needs a keypress
per shot -- a frequency sweep cannot press keys. Two blanks here can be
fired from code instead: the rf_gen power drop, and the same bladeRF gate
as a method call on bladerf_cw.CWExciter.

See esyncMPPTesting_wifi.ipynb for the two-tag manual version of this flow.
"""

import time

# --- mirrored from XIAO-ESP32-C6_firmware_wifi/config.h -------------------
# Nothing here is read off the tag; these are copies, and a firmware rebuild
# that changes them has to change them here too. They are used to check that
# a blank we generate is one the tags can actually act on.
ESYNC_FIRE_DELAY_US = 50000    # ESYNC_FIRE_DELAY_US: when the tag fires,
                               # measured from the falling edge
ESYNC_BLANK_MIN_US = 10000     # ESYNC_BLANK_MIN_US: shorter than this and
                               # the tag calls it a fade and does not fire
ESYNC_WIFI_QUIET_MS = 50       # ESYNC_WIFI_QUIET_MS: ack drain before the
                               # radio goes down
ESYNC_WIFI_TIMEOUT_MS = 30000  # ESYNC_WIFI_TIMEOUT_MS: firmware gives up

# --- rf_gen null ---------------------------------------------------------
NULL_POWER_DBM = -30.0

# How long to hold the generator at NULL_POWER_DBM.
#
# This is NOT ESYNC_FIRE_DELAY_US, and deliberately so. The bladeRF exciter
# gates in baseband, so its blank is exactly DROP_MS and can sit right on the
# fire delay. A GPIB write cannot: there is command latency either side, and
# the tag's rectifier takes ~4 time constants (tau ~ 2.5 ms) to fall from the
# carrier to ESYNC_MIN_BASELINE_MV, so the edge the tag latches is ~10 ms
# after the generator actually drops.
#
# What that buys is room to be wrong in the safe direction. The tag fires at
# t_fall + 50 ms either way:
#
#   blank shorter than 50 ms  the carrier is already back when the tag
#                             fires. Harmless -- MPP's four leading ch1
#                             dwells are padding for exactly this, and every
#                             tag is late by the same amount, so they stay
#                             in sync with each other, which is the point.
#   blank longer than 50 ms   the tag fires into a dead carrier and sweeps
#                             with nothing illuminating it. The trace is
#                             garbage, and only esyncr's "returned":0 says
#                             so afterwards.
#
# So the target is comfortably short of the fire delay, and comfortably clear
# of ESYNC_BLANK_MIN_US at the other end. 30 ms commanded measures back as
# roughly 20 ms of low_us, in the middle of the usable 10..50 ms window.
NULL_HOLD_S = 0.030

# Margin between the last tag acking `esync` and the blank. The firmware
# waits ESYNC_WIFI_QUIET_MS after the ack before suspending the radio, and
# the detector needs ESYNC_WARMUP_SAMPLES (~15 ms) of carrier to seed its
# baseline before a drop means anything. Firing inside that window means
# some tags never see the edge.
#
# What makes this worth over-paying for is that firing early is invisible.
# Before the warmup window fills, esyncListening() returns ahead of every
# threshold test, and priming then adopts the current level instead of
# calling it an edge -- so a blank that lands there is not rejected and
# not tallied, it simply never happened as far as the tag is concerned.
# The round comes back as "pending":0 with rej_short at 0, which reads
# exactly like an exciter that never blanked.
#
# The nominal cost is ~65 ms (50 ms quiet + ~15 ms warmup) measured from
# each tag's own ack, so 1 s is far more than the budget needs. It is
# deliberate: the wait is per MPP round against a 30 s ESYNC_WIFI_TIMEOUT_MS,
# the tags all sit in the same steady carrier while it elapses, and nothing
# about the sync degrades by waiting longer -- the baseline integrator just
# settles further. Lower it only with esyncr's "primed" in hand.
ARM_SETTLE_S = 1.0

# The firmware resumes on its own after ESYNC_WIFI_TIMEOUT_MS if no edge
# arrives, so waiting past that is how a missed blank is told from a slow
# one rather than something to sit through.
RECONNECT_DEADLINE_S = ESYNC_WIFI_TIMEOUT_MS / 1000.0 + 30.0


class NullExciter:
    """Fires the sync blank: the one thing a wireless run needs an exciter for.

    Subclass this to support a new exciter. The contract is small on purpose,
    because the tags do not care HOW the carrier goes away -- they latch the
    first sample at or under ESYNC_MIN_BASELINE_MV (2 mV) and dead-reckon
    from there. Any mechanism that takes the carrier to the floor and brings
    it back is a valid blank.

    What an implementation must provide:

      fire()      take the carrier down, hold it there for about hold_s,
                  bring it back. Returns (t_drop, t_restore) as host wall
                  clock, used only for the CSV's bookkeeping columns -- the
                  tags' timing comes off the edge, not off these.
      describe()  one short phrase for the run log, so the operator can see
                  which blank they are getting.

    and then register it for the EXCITER values it handles:

        @register_null_exciter('my_gen')
        class MyNullExciter(NullExciter):
            def __init__(self, hold_s=NULL_HOLD_S, exc=None, **_):
                super().__init__(hold_s)
                self.exc = exc
            def fire(self): ...
            def describe(self): ...

    Factories are called with every resource mainMultiWays has (exc,
    run_power, ...) as keyword arguments; take the ones you need and
    absorb the rest with **_, so adding a resource later does not break the
    implementations that do not want it.

    Two properties matter more than the mechanism, and are worth checking
    for any new one:

      depth   the blank must reach ESYNC_MIN_BASELINE_MV AT EVERY TAG, not
              just at the nearest. esyncr's min_mv against base_mv is the
              check; a tag the blank does not reach never fires at all.
      edges   keep the fall sharp. The tag latches at the floor, so a slow
              decay moves t_fall later and drags every tag's fire with it --
              a shared offset rather than jitter, but one that can walk the
              fire off the end of the blank.
    """

    def __init__(self, hold_s=NULL_HOLD_S):
        self.hold_s = hold_s

    def fire(self):
        raise NotImplementedError

    def describe(self):
        return f"{type(self).__name__}, {self.hold_s*1e3:g} ms"


_NULL_EXCITERS = {}

# EXCITER values that name a real exciter which simply cannot be driven from
# here, kept apart from plain typos so the error can say why. Empty now:
# 'bladerf' lived here while the only bladeRF blank was manual_null_exciter.py
# and its one keypress per shot, and moved out when bladerf_cw.py made the
# same gate a method call.
_NO_AUTO_BLANK = {}


def register_null_exciter(*exciter_types):
    """Register a NullExciter subclass (or any factory) for EXCITER values.

    This is the only place an exciter name is wired to a blank, so adding a
    new one is one decorator here -- nothing in collection.py or
    measurePhasesMultiThreadedMultiTags.py knows the names.
    """
    def register(factory):
        for name in exciter_types:
            _NULL_EXCITERS[name] = factory
        return factory
    return register


def registered_null_exciters():
    return sorted(_NULL_EXCITERS)


@register_null_exciter('rf_gen')
class PowerDropNullExciter(NullExciter):
    """Blank by dropping the generator's output power and putting it back.

    Named for the mechanism, not the instrument: it works for anything with
    a settable output level, which is why it is registered for rf_gen rather
    than being rf_gen's only possible implementation.

    ~43 dB down from a typical run power puts the tag's rectifier far under
    ESYNC_MIN_BASELINE_MV, which is the floor the tag reads as the start of
    a blank.

    Unlike the bladeRF gate the length is timed by the host, so it is
    approximate -- see NULL_HOLD_S for why that is tolerable and which way
    to err. It does not affect sync BETWEEN tags at all: they all time off
    the same falling edge, whenever it happens to land.
    """

    def __init__(self, hold_s=NULL_HOLD_S, exc=None, run_power=None,
                 null_power_dbm=NULL_POWER_DBM, **_):
        super().__init__(hold_s)
        if exc is None:
            raise Exception("PowerDropNullExciter needs an exciter handle (exc)")
        if run_power is None:
            raise Exception("PowerDropNullExciter needs run_power (the run's "
                            "EXC_POWER) to restore the carrier to")
        self.exc = exc
        self.run_power = run_power
        self.null_power_dbm = null_power_dbm

    def fire(self):
        t_drop = time.time()
        self.exc.set_pwr(self.null_power_dbm)
        time.sleep(self.hold_s)
        self.exc.set_pwr(self.run_power)
        return t_drop, time.time()

    def describe(self):
        return (f"{self.hold_s*1e3:g} ms power drop to "
                f"{self.null_power_dbm:g} dBm")


@register_null_exciter('bladerf')
class GatedBlankNullExciter(NullExciter):
    """Blank by gating the carrier to zero in baseband and back.

    Named for the mechanism: it works for any exciter whose handle offers
    blank(hold_s), which today is the bladeRF (bladerf_cw.CWExciter, held
    directly by this process or reached over the control link of
    bladerf_exciter_server.py). That is the blank the firmware's detector
    was written against -- see BladeRFCode/null_sync/manual_null_exciter.py
    -- with the keypress replaced by a call.

    It differs from the power drop in one way that matters, and it is not
    depth: the length is applied sample-by-sample in baseband, so it is
    exact, where a pair of GPIB writes is however long the writes took. So
    hold_s can sit close to ESYNC_FIRE_DELAY_US here instead of being kept
    short to leave room for the host's timing to be wrong.

    What it does not change is the tag's rectifier: the carrier goes to zero
    at the sample it says, and the tag's envelope still takes ~10 ms to fall
    from there to ESYNC_MIN_BASELINE_MV. The tag latches THAT instant, so it
    fires ~10 ms later than the gate suggests and a blank of exactly
    ESYNC_FIRE_DELAY_US still lands the fire safely after the carrier is
    back.
    """

    def __init__(self, hold_s=NULL_HOLD_S, exc=None, **_):
        super().__init__(hold_s)
        if exc is None or not hasattr(exc, 'blank'):
            raise Exception(
                "GatedBlankNullExciter needs an exciter handle (exc) that can "
                "blank its carrier. For the bladeRF that is a "
                "bladerf_cw.CWExciter -- see exciters.make_bladerf, which "
                "builds one here or reaches one on another machine.")

        hold_us = hold_s * 1e6
        if hold_us <= ESYNC_BLANK_MIN_US:
            # The tag would throw every one of these away as a fade, and
            # nothing would fire all run. Cheaper to say so now than to
            # discover it one 30 s esync window at a time.
            raise Exception(
                f"ESYNC_NULL_HOLD_S is {hold_s:g} s, at or under the "
                f"firmware's ESYNC_BLANK_MIN_US ({ESYNC_BLANK_MIN_US} us): "
                f"the tags read a blank this short as a fade and never fire.")
        if hold_us > ESYNC_FIRE_DELAY_US:
            # Survivable -- the rectifier's fall buys ~10 ms -- but this is
            # the edge of the window, and past it every tag sweeps an unlit
            # scene and only esyncr's "returned":0 says so.
            print(f"WARNING ESYNC_NULL_HOLD_S is {hold_s:g} s, longer than the "
                  f"firmware's ESYNC_FIRE_DELAY_US "
                  f"({ESYNC_FIRE_DELAY_US/1e3:g} ms). Watch for "
                  f"\"returned\":0 in the sync line.")
        self.exc = exc

    def fire(self):
        return self.exc.blank(self.hold_s)

    def describe(self):
        return f"{self.hold_s*1e3:g} ms baseband gate to zero"


def make_null_exciter(exciter_type, hold_s=NULL_HOLD_S, **resources):
    """Build the blank source for this run, or say why there isn't one.

    Wireless MPP has no fallback here: without a blank the tags never fire
    and every capture times out, so this refuses up front rather than
    letting a sweep discover it one 30 s window at a time.

    `resources` is whatever the caller can offer (exc, run_power, ...);
    each registered implementation takes what it needs and ignores the rest.
    """
    factory = _NULL_EXCITERS.get(exciter_type)
    if factory is not None:
        return factory(hold_s=hold_s, **resources)

    known = ", ".join(repr(n) for n in registered_null_exciters())

    if exciter_type is None:
        raise Exception(
            f"wireless MPP needs an exciter that can blank its carrier to "
            f"sync the tags, but EXCITER is None. Set EXCITER to one of "
            f"{known}, or run this CONNECTION: wired.")

    why = _NO_AUTO_BLANK.get(str(exciter_type).lower())
    if why:
        raise Exception(
            f"no automatic sync blank for EXCITER={exciter_type!r}: {why} "
            f"Use one of {known}, or run this CONNECTION: wired.")

    raise Exception(
        f"no sync blank registered for EXCITER={exciter_type!r}. Known: "
        f"{known}. Add one with @esync_mpp.register_null_exciter"
        f"({exciter_type!r}), or run this CONNECTION: wired.")


def check_fired(reports, raise_on_blind=True):
    """Confirm every tag fired, and against the same blank.

    Adapted from esyncMPPTesting_wifi.ipynb. A tag that never fired answers
    with "pending":0 and two reject tallies. Which one climbed says what to
    change:

      rej_short  the floor was reached but the carrier came back before
                 ESYNC_BLANK_MIN_US -- the blank is too short. Raise
                 NULL_HOLD_S.
      it stays 0 nothing ever reached the floor, so no edge was latched.
                 The falling edge IS the floor test, so this is the only
                 other thing it can be. Compare min_mv against base_mv: if
                 min_mv never approaches ESYNC_MIN_BASELINE_MV the blank is
                 not deep enough at this tag.

    A tag that DID fire still has to be checked. The detector times off the
    falling edge and commits before the blank's length is known, so an
    overrunning blank cannot be rejected -- it is fired against and only
    flagged afterwards. "returned":0 means the carrier had not come back by
    the time the tag fired, i.e. it swept an unlit scene.

    Returns the measured blank lengths. Raises on anything that makes the
    round's data untrustworthy.
    """
    problems = []

    for name, r in reports.items():
        if "low_us" in r and "pending" not in r:
            continue
        problems.append(
            f"{name} never fired: rej_short={r.get('rej_short')} "
            f"in_blank={r.get('in_blank')} armed={r.get('armed')} "
            f"min_mv={r.get('min_mv')} base_mv={r.get('base_mv')}")

    if problems:
        raise Exception("; ".join(problems))

    for name, r in reports.items():
        if not r.get("returned"):
            msg = (f"{name} fired blind: the carrier still had not returned "
                   f"{r.get('delay_us')} us after the falling edge, so the "
                   f"tag swept with nothing illuminating it. The blank is "
                   f"longer than ESYNC_FIRE_DELAY_US assumes -- lower "
                   f"NULL_HOLD_S / ESYNC_NULL_HOLD_S.")
            if raise_on_blind:
                raise Exception(msg)
            print(f"WARNING {msg}")
        if r.get("long"):
            print(f"WARNING {name}: blank ran long ({r['low_us']} us) -- "
                  f"synced against an outage?")

    # t_us is each tag's own uptime, so it is NOT comparable between tags.
    # low_us is a measured duration, so it is. Read it as an approximation:
    # it spans the floor on the way down to ESYNC_REARM_PCT on the way up,
    # two different thresholds, so it under-reads the commanded hold. What
    # matters is that the tags AGREE -- a wide spread means one of them
    # timed off a different edge, and its trace does not line up with the
    # others even though both fired.
    lows = [r["low_us"] for r in reports.values()]
    return lows


def blank_summary(reports):
    """One line of sync diagnostics, for the per-round log."""
    lows = [r.get("low_us") for r in reports.values() if r.get("low_us") is not None]
    if not lows:
        return "no blank measured"
    return (f"blank {min(lows)}..{max(lows)} us "
            f"(spread {max(lows) - min(lows)} us, fire delay {ESYNC_FIRE_DELAY_US} us)")
