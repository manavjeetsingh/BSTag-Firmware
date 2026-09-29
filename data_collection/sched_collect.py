"""Host side of the slotted schedule -- the "multiple" collection mode.

In "individual" mode a round has one transmitter: it sweeps, everyone else
captures, and the round is repeated once per tag per frequency. In "multiple"
mode every tag is loaded with an ordered program of equal-length slots before
the round, and one trigger starts all of them at once:

    tag1: [ mpp    , listen , listen ]
    tag2: [ listen , mpp    , listen ]
    tag3: [ listen , listen , mpp    ]

so slot k is tag k+1 transmitting and everyone else listening, and one trigger
measures every direction. N tags take one round instead of N.

The trigger is the exciter's ASK preamble over the air (wireless) or a
barrier-released "sgo" down each cable (wired). What keeps the tags in step
afterwards is that the slot grid is absolute -- boundary k sits at
k * slot_us from the trigger, and a slot that finishes early is padded out to
it -- so the only error that accumulates is the disagreement between the
triggers themselves, not between the commands.

This module holds the geometry and the checks. The run loop is
multiple_collection.py; the firmware is XIAO-ESP32-C6_firmware_wifi/schedule.cpp.
"""

from ribbn_scripts.hardware_api.hardware import SCHED_LISTEN, SCHED_MPP


# ---------------------------------------------------------------------------
# Mirrors of the firmware's config.h.
#
# Nothing checks these against the tag at startup, the way ESYNC_CODE is not
# checked against the exciter, so they are wrong in the same quiet way: the
# geometry here stops describing the round that actually ran. What does get
# checked is the one that matters most -- check_rate() below compares the
# sample rate the firmware reports against SAMPLE_US, and segmentation uses
# the reported rate rather than this file's idea of it.
# ---------------------------------------------------------------------------

# SCHED_MAX_SLOTS: one slot per tag, so this is the most tags a single round
# can hold. A longer program is refused here rather than by "sq:full" partway
# through loading it.
MAX_SLOTS = 20

# SCHED_POOL_SAMPLES: raw codes for every listen slot of a round, shared.
POOL_SAMPLES = 48000

# SCHED_SLOT_US and SCHED_LISTEN_SAMPLES: the defaults a tag boots with. Both
# are overridable per run (SCHED_SLOT_US / SCHED_LISTEN_SAMPLES in
# configurations.json) and are sent explicitly every round either way.
DEFAULT_SLOT_US = 50000
DEFAULT_LISTEN_SAMPLES = 1200

# SCHED_SAMPLE_US: the paced sample period inside a listen slot.
SAMPLE_US = 25

# MPP_DWELL_QUEUED_US and the length of MPP_CHANNELS_SCHED {1,1,1,3,4,6,7,8}:
# what one scheduled sweep costs. Two of those eight dwells are the leading
# ch1 padding that mpp_segment.fit_sweep_grid() pins the grid phase on; the
# other six are the measured channels.
MPP_DWELL_US = 3000
SWEEP_DWELLS = 8

# SCHED_PREROLL_US: dead time before slot 0, paid once per round by every tag
# so the grid stays common.
PREROLL_US = 9000


# ---------------------------------------------------------------------------
# Timing of a round, as the host sees it.
# ---------------------------------------------------------------------------

# Wireless only: after the last arm ack, before the preamble. Same job as
# esync_mpp.ARM_SETTLE_S and the same floor under it -- the tag needs 26 ms of
# carrier to fill its correlator before a preamble can be heard at all.
ARM_SETTLE_S = 0.06

# How long the wire is left alone after the preamble before the host starts
# asking whether anything fired. Covers the whole round in the case where the
# preamble reaches the air immediately: the grid's own length, the lock and
# fire delay (~5 ms), the spread between the tags' fires, and the preroll
# being charged after the trigger rather than before it.
#
# It is a quiet period, not a deadline -- see FIRE_DEADLINE_S.
RESULT_SETTLE_S = 0.25

# After that, how often to ask again, and for how long.
#
# The host cannot simply sleep for the round and then read the result, which
# is what this did at first. exc.sync() does not put the preamble on the air;
# it queues it. SyncGate.trigger() drops the envelope into the flowgraph and
# it reaches the antenna whenever GNU Radio's buffers and libbladeRF's stream
# buffers have drained -- "tens of ms", says bladerf_cw.py, but nothing bounds
# it and at 2 Msps it can be far more. An individual round never had to care,
# because its tags sit in await_fired_wifi() for FIRE_DEADLINE_S and the
# preamble lands whenever it lands. A fixed sleep here did have to care, and
# got it wrong: every tag reported listening=1, primed=1, ~7300 samples and a
# peak_rho at the noise floor, which is exactly what a tag that was asked
# before the preamble existed looks like.
#
# So the round waits for the fire instead of predicting it, and the two
# transports now agree: FIRE_DEADLINE_S is esync_mpp's, the same patience an
# individual round has always had.
#
# Polling is not free -- servicing a command costs the sample loop a
# millisecond or two, and a poll that lands inside the 26 ms preamble blanks a
# couple of the 260 bins. The correlator carries empty bins forward, and the
# margin between a typical rho of 0.99 and ESYNC_MIN_RHO of 0.80 is wide
# enough to absorb that, so this is deliberately slow rather than eager.
FIRE_POLL_S = 0.25

# Re-shoots per round before the run gives up. A preamble that lands on a
# burst of traffic is worth another go; one that misses every time is the
# setup, and more tries say nothing new. Matches esync_mpp.MAX_ROUND_ATTEMPTS.
MAX_ROUND_ATTEMPTS = 10

# How long to wait for every tag's sqr. This is the biggest transfer the
# run makes -- nineteen traces per tag, printed a sample at a time -- and all
# the tags do it at once, so it is sized for the slowest rather than the sum.
REPORT_GATHER_S = 120

# Pause before each re-shoot. Same reasoning as
# measurePhasesMultiThreadedMultiTags.RETRY_BACKOFF_S: most of what makes a
# round fail clears on its own given a moment.
RETRY_BACKOFF_S = 2.0

# How far the measured sample rate may sit from 1 / SAMPLE_US before
# check_rate() complains. Wide on purpose -- it is there to catch a slot that
# fell back to free-running or a pacing constant that has gone stale, not to
# police jitter, and segmentation uses the measured rate regardless.
RATE_TOLERANCE = 0.25


def sweep_us():
    """How long one scheduled MPP sweep takes."""
    return SWEEP_DWELLS * MPP_DWELL_US


def build_programs(tag_names):
    """{tag: [slot kind, ...]} -- tag i transmits in slot i, listens elsewhere.

    The order of `tag_names` is the slot order, and it is the only thing that
    says which tag a listener's slot k was listening to. Callers pass a sorted
    list and keep it for the whole run: renumbering the slots between rounds
    would relabel every Rx/Tx pair with nothing to notice it.
    """
    names = list(tag_names)
    if not names:
        raise ValueError("no tags to build a schedule for")
    if len(names) > MAX_SLOTS:
        raise ValueError(
            f"{len(names)} tags, but a round has room for {MAX_SLOTS} slots "
            f"(SCHED_MAX_SLOTS in config.h). Raise it and reflash, or split "
            f"the run.")
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate tag names in {names}")

    return {name: [SCHED_MPP if j == i else SCHED_LISTEN
                   for j in range(len(names))]
            for i, name in enumerate(names)}


def listen_samples_for(n_slots, slot_us=DEFAULT_SLOT_US,
                       want=DEFAULT_LISTEN_SAMPLES):
    """Samples per listen slot: what the pool and the slot can both afford.

    Three ceilings, and the lowest wins:

      - the pool, shared by every listen slot of the round (POOL_SAMPLES).
        A slot that runs past its share is flagged "full":1 and cut short.
      - the slot itself, since the firmware stops sampling at the boundary
        whatever budget it was given. Asking for more than this is harmless,
        just pointless.
      - `want`, so a two-tag run does not spend a 50 ms slot's worth of
        samples on a 24 ms sweep and then print all of them back.

    It is never allowed below what a sweep needs: a trace that cannot contain
    the sweep cannot be segmented, and a round of those is worse than a round
    that refuses to start. If the floor is the binding constraint the caller
    is told, because the fix is a shorter slot or fewer tags, not a retry.
    """
    n_listen = max(1, n_slots - 1)
    pool_share = POOL_SAMPLES // n_listen
    slot_share = slot_us // SAMPLE_US
    n = int(min(want, pool_share, slot_share))

    floor = sweep_samples()
    if n < floor:
        raise ValueError(
            f"a listen slot can only hold {n} samples here, but a whole sweep "
            f"is {floor} -- the trace could not be segmented. "
            f"{n_listen} listen slots share {POOL_SAMPLES} samples "
            f"({pool_share} each) and a {slot_us} us slot holds {slot_share}. "
            f"Run fewer tags, or raise SCHED_POOL_SAMPLES / SCHED_SLOT_US in "
            f"config.h.")
    return n


def sweep_samples():
    """One whole scheduled sweep, in listen-slot samples."""
    return sweep_us() // SAMPLE_US


def dwell_samples_from_slot(slot):
    """Nominal dwell length, in samples, for this listen slot's trace.

    The number mpp_segment needs is "how many samples does one 3 ms dwell
    take", and that is a property of the slot that recorded the trace, not of
    the run: a scheduled listen loop is nothing like the loop()-driven capture
    its DWELL_SAMPLES table was measured under. So it is derived from what the
    firmware timed -- `count` samples over `dur_us` -- rather than looked up.

    Falls back to the pacing constant when a slot reports no duration, which
    is what an older firmware without "dur_us" in sqr does.
    """
    rate_hz = slot.get("rate_hz")
    if not rate_hz:
        return MPP_DWELL_US / SAMPLE_US
    return rate_hz * MPP_DWELL_US / 1e6


def check_rate(reports):
    """Warn if any slot sampled far off the paced rate. Returns the warnings.

    Not fatal. A slot that free-ran because readAdcRaw() costs more than
    SCHED_SAMPLE_US still segments correctly -- dwell_samples_from_slot() uses
    what was measured -- it just fills its trace faster than the geometry here
    expects and so may not cover the whole sweep. That shows up as a
    segmentation failure and a re-shoot, which is a confusing way to find out,
    hence the warning.
    """
    expected = 1e6 / SAMPLE_US
    warnings = []
    for name, report in reports.items():
        rates = [s["rate_hz"] for s in report["slots"]
                 if s.get("rate_hz")]
        if not rates:
            continue
        worst = max(rates, key=lambda r: abs(r - expected))
        if abs(worst - expected) > RATE_TOLERANCE * expected:
            warnings.append(
                f"{name} sampled a listen slot at {worst / 1e3:.0f} kSa/s, "
                f"not the {expected / 1e3:.0f} kSa/s SCHED_SAMPLE_US asks for")
    return warnings


def round_duration_s(n_slots, slot_us):
    """How long the grid runs, from trigger to the last boundary."""
    return (PREROLL_US + n_slots * slot_us) / 1e6


def result_deadline_s(n_slots, slot_us):
    """How long to leave the wire alone after the preamble before asking a tag
    about a round it was not told to report on -- i.e. every wireless round.
    See RESULT_SETTLE_S."""
    return round_duration_s(n_slots, slot_us) + RESULT_SETTLE_S


def check_reports(reports, n_slots, tag_order):
    """Raise unless every tag came back with a whole, usable round.

    Anything wrong here fails the round and re-shoots it, so the checks are
    deliberately strict: a partial round carried into the results is a set of
    readings labelled with a frequency and a tag pair they do not belong to.
    """
    problems = []
    for name, report in reports.items():
        slots = report["slots"]
        if len(slots) != n_slots:
            problems.append(f"{name} reported {len(slots)} slots, not {n_slots}")
            continue

        want_mpp = tag_order.index(name)
        for slot in slots:
            i = slot["i"]
            kind_wanted = "mpp" if i == want_mpp else "listen"
            if slot["kind"] != kind_wanted:
                problems.append(
                    f"{name} slot {i} came back as {slot['kind']!r}, expected "
                    f"{kind_wanted!r} -- the program loaded is not the one "
                    f"this round assumes")
                continue
            if slot["kind"] != "listen":
                continue
            if slot["truncated"]:
                problems.append(
                    f"{name} slot {i} ran out of pool ({slot['count']} "
                    f"samples) -- lower SCHED_LISTEN_SAMPLES or run fewer tags")
            if slot["trace"] is None or len(slot["trace"]) == 0:
                problems.append(f"{name} slot {i} came back empty")

    if problems:
        raise Exception("; ".join(problems))


def pair_traces(reports, tag_order):
    """{(rx, tx): trace} for every ordered pair the round measured.

    Slot k is tag_order[k] transmitting, so a listener's slot k is its reading
    of that tag. The transmitter's own slot k is its sweep and has no trace,
    which is why a tag never appears as its own receiver.
    """
    pairs = {}
    for rx, report in reports.items():
        for slot in report["slots"]:
            if slot["kind"] != "listen":
                continue
            tx = tag_order[slot["i"]]
            pairs[(rx, tx)] = slot
    return pairs


def slot_summary(reports):
    """One line of round diagnostics for the per-round log."""
    rates, counts, heaps = [], [], []
    for report in reports.values():
        for slot in report["slots"]:
            if slot["kind"] != "listen":
                continue
            counts.append(slot["count"])
            if slot.get("rate_hz"):
                rates.append(slot["rate_hz"])
        if report.get("heap"):
            heaps.append(report["heap"])
    if not counts:
        return "no listen slots"
    out = f"{len(counts)} traces, {min(counts)}..{max(counts)} samples"
    if rates:
        out += f", {min(rates) / 1e3:.0f}..{max(rates) / 1e3:.0f} kSa/s"
    if heaps:
        out += f", heap {min(heaps) / 1024:.0f} KB"
    return out
