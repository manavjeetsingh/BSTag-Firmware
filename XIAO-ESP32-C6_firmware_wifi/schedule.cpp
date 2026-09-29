#include "schedule.h"

#include "config.h"
#include "hardware.h"

static const uint8_t SCHED_CHANNELS[] = MPP_CHANNELS_SCHED;
#define SCHED_CHANNEL_COUNT (sizeof(SCHED_CHANNELS) / sizeof(SCHED_CHANNELS[0]))

struct SchedSlot {
    uint8_t  kind;
    uint32_t off;        /* first sample in pool, LISTEN only */
    uint32_t count;      /* samples actually taken */
    uint8_t  ch;         /* channel the slot ran on */
    uint32_t start_us;   /* slot start, relative to the trigger */
    uint32_t dur_us;     /* time `count` samples took, LISTEN only */
    bool     truncated;  /* the pool ran out before the slot did */
};

static SchedSlot slots[SCHED_MAX_SLOTS];
static uint8_t   slot_count = 0;

/* The one big allocation. See SCHED_POOL_SAMPLES in config.h. */
static uint16_t  pool[SCHED_POOL_SAMPLES];

static uint32_t  slot_us        = SCHED_SLOT_US;
static uint32_t  listen_samples = SCHED_LISTEN_SAMPLES;
static bool      raw_unit       = false;

static bool      have_result = false;
static uint32_t  run_heap    = 0;

void schedClear(void)
{
    slot_count = 0;
    have_result = false;
    run_heap = 0;
}

bool schedAddSlot(uint8_t kind)
{
    if (slot_count >= SCHED_MAX_SLOTS) {
        return false;
    }
    if (kind != SCHED_KIND_MPP && kind != SCHED_KIND_LISTEN) {
        return false;
    }
    /* Loading a new program invalidates the last one's results: better to
     * answer sqr with "nothing ran" than to hand back a trace from a
     * program that is no longer the one loaded. */
    have_result = false;

    SchedSlot &s = slots[slot_count++];
    s.kind = kind;
    s.off = 0;
    s.count = 0;
    s.ch = 0;
    s.start_us = 0;
    s.dur_us = 0;
    s.truncated = false;
    return true;
}

uint32_t schedSetSlotUs(uint32_t us)
{
    /* Floor at the sweep itself: a slot shorter than the command it holds
     * would put every later slot behind the grid rather than on it. */
    uint32_t min_us = (uint32_t)SCHED_CHANNEL_COUNT * MPP_DWELL_QUEUED_US;
    if (us < min_us) {
        us = min_us;
    }
    slot_us = us;
    return slot_us;
}

uint32_t schedSetListenSamples(uint32_t n)
{
    if (n == 0) {
        n = 1;
    }
    if (n > SCHED_POOL_SAMPLES) {
        n = SCHED_POOL_SAMPLES;
    }
    listen_samples = n;
    return listen_samples;
}

void schedSetRawUnit(bool raw)
{
    raw_unit = raw;
}

bool schedLoaded(void)
{
    return slot_count > 0;
}

uint8_t schedSlotCount(void)
{
    return slot_count;
}

bool schedHasResult(void)
{
    return have_result;
}

/* Is slot i this tag's transmit slot? Used to decide whether to pre-switch
 * to the sweep's first channel during the previous slot's padding. */
static bool isMppSlot(int i)
{
    return i >= 0 && i < (int)slot_count && slots[i].kind == SCHED_KIND_MPP;
}

/* Spin until `deadline` (micros(), relative to the run's t0), optionally
 * settling on the sweep's entry channel while we wait.
 *
 * The signed comparison is what makes a micros() wrap harmless: the
 * difference stays correct across the rollover where `now > deadline`
 * would not. */
static void padUntil(uint32_t t0, uint32_t deadline_us)
{
    while ((int32_t)((micros() - t0) - deadline_us) < 0) {
        /* nothing: the point of the pad is that the RF path is left alone */
    }
}

static void runSchedSweep(void)
{
    for (size_t i = 0; i < SCHED_CHANNEL_COUNT; i++) {
        switchChannel(SCHED_CHANNELS[i]);
        /* Whole milliseconds through delay() so the sweep keeps yielding,
         * as runMppSweep() does; only the remainder is spun. */
        uint32_t us = MPP_DWELL_QUEUED_US;
        if (us >= 1000UL) {
            delay(us / 1000UL);
        }
        uint32_t rem = us % 1000UL;
        if (rem > 0) {
            delayMicroseconds(rem);
        }
    }
}

void schedRun(void)
{
    if (slot_count == 0) {
        return;
    }

    setStatusLed(false);

    uint32_t next_off = 0;
    for (uint8_t i = 0; i < slot_count; i++) {
        slots[i].count = 0;
        slots[i].truncated = false;
        slots[i].off = 0;
        slots[i].dur_us = 0;
    }

    /* Slot 0's transmitter has no previous slot to settle in, so everyone
     * waits out one preroll before the grid starts. Waiting on every tag
     * and not just the transmitter is deliberate: the grid has to be
     * common, and a tag that skipped it would start slot 0 early.
     *
     * Everyone else hops onto CAPTURE_CHANNEL here rather than at the top of
     * slot 0, so their own rectifier settles during the preroll instead of
     * during the trace. Leaving it to the slot meant slot 0's listeners
     * started sampling mid-transient whenever they came into the round on
     * some other channel. */
    if (isMppSlot(0)) {
        switchChannel(SCHED_CHANNELS[0]);
    } else {
        switchChannel(CAPTURE_CHANNEL);
    }
    uint32_t preroll_start = micros();
    while ((int32_t)(micros() - preroll_start - (uint32_t)SCHED_PREROLL_US) < 0) {
    }

    uint32_t t0 = micros();

    for (uint8_t i = 0; i < slot_count; i++) {
        SchedSlot &s = slots[i];
        uint32_t slot_start = (uint32_t)i * slot_us;
        uint32_t slot_end   = slot_start + slot_us;
        s.start_us = slot_start;

        if (s.kind == SCHED_KIND_MPP) {
            runSchedSweep();
        } else {
            /* Already here: the previous slot's padding put us on
             * CAPTURE_CHANNEL and let the rectifier settle (see the pad
             * below, and the preroll above). This is the no-op that proves
             * it -- if it ever switches for real, the trace opens with a
             * settling transient and segmentation pays for it. */
            switchChannel(CAPTURE_CHANNEL);
            s.off = next_off;
            uint32_t room = (next_off < SCHED_POOL_SAMPLES)
                          ? (SCHED_POOL_SAMPLES - next_off) : 0;
            uint32_t want = listen_samples;
            if (want > room) {
                want = room;
                s.truncated = true;
            }
            /* Stop on whichever comes first, the sample budget or the slot
             * boundary -- the budget normally, since it is sized to cover
             * the peer's sweep and not the whole slot. */
            uint32_t sample_start = micros();
            while (s.count < want &&
                   (int32_t)((micros() - t0) - slot_end) < 0) {
                /* Paced, not free-running: see SCHED_SAMPLE_US. The due time
                 * is counted from sample_start rather than added up as we go,
                 * so a sample that lands late costs only itself and does not
                 * push the rest of the slot along with it. */
                if (s.count > 0) {
                    uint32_t due = sample_start + s.count * SCHED_SAMPLE_US;
                    while ((int32_t)(micros() - due) < 0) {
                    }
                }
                pool[s.off + s.count++] = readAdcRaw();
            }
            /* How long those samples actually took. The host divides it into
             * `count` to get the slot's sample rate, and from that the dwell
             * length in samples that mpp_segment has to look for.
             *
             * It has to be measured rather than assumed because this loop is
             * nothing like the one that fills a rdb capture: that one takes a
             * single sample per loop() pass, behind serviceWifi(), serviceTcp()
             * and every session poll, and mpp_segment's DWELL_SAMPLES (45
             * wireless, 90 wired) are measurements of *that*. Here the slot
             * owns the CPU and does nothing but readAdcRaw(), so the rate is
             * several times higher and is not the same number on the two
             * transports either. Reporting it means no third constant to
             * measure by hand and leave to go stale -- and it is measured
             * even though SCHED_SAMPLE_US now sets the rate, because that
             * constant is a floor the loop can fall below, not a promise. */
            s.dur_us = micros() - sample_start;
            next_off += s.count;
        }

        s.ch = current_channel;

        /* Spend the pad settling on whatever the NEXT slot needs, so no slot
         * ever opens on a channel the rectifier has not caught up with.
         *
         * Transmitting next: sit on the sweep's first channel, which is what
         * lets MPP_CHANNELS_SCHED drop the leading settle dwells.
         *
         * Listening next, having just transmitted: the hop to CAPTURE_CHANNEL
         * cannot happen yet. Every other tag is still sampling our sweep's
         * tail, and our switch would land in their traces as a step that is
         * not a channel of the sweep. So it waits until their windows have
         * closed -- listen_samples at SCHED_SAMPLE_US, the same budget they
         * were given -- and takes whatever is left of the pad to settle in.
         * Whichever of the two bounds is later wins, so a slot too short to
         * satisfy both keeps its neighbours' traces clean and gives up
         * settling time instead. */
        if (isMppSlot(i + 1)) {
            switchChannel(SCHED_CHANNELS[0]);
        } else if (s.kind == SCHED_KIND_MPP) {
            uint32_t rx_done = slot_start + listen_samples * SCHED_SAMPLE_US;
            uint32_t settle  = slot_end - SCHED_RX_SETTLE_US;
            uint32_t at = ((int32_t)(rx_done - settle) > 0) ? rx_done : settle;
            if ((int32_t)(at - slot_end) > 0) {
                at = slot_end;
            }
            padUntil(t0, at);
            switchChannel(CAPTURE_CHANNEL);
        }
        padUntil(t0, slot_end);
    }

    run_heap = (uint32_t)ESP.getFreeHeap();
    have_result = true;
    setStatusLed(true);
}

void schedPrint(Print &out)
{
    out.printf("{\"info\":\"sq\",\"slots\":%u,\"slot_us\":%lu,"
               "\"listen_samples\":%lu,\"unit\":\"%s\",\"program\":\"",
               (unsigned)slot_count, (unsigned long)slot_us,
               (unsigned long)listen_samples, raw_unit ? "raw" : "mV");
    for (uint8_t i = 0; i < slot_count; i++) {
        out.print(slots[i].kind == SCHED_KIND_MPP ? "mpp" : "lis");
        if (i + 1 < slot_count) {
            out.print(",");
        }
    }
    out.println("\"}");
}

void schedReport(Print &out)
{
    if (!have_result) {
        out.println("{\"info\":\"sched\",\"pending\":0}");
        return;
    }

    out.printf("{\"info\":\"sched\",\"pending\":1,\"slots\":%u,\"slot_us\":%lu,"
               "\"unit\":\"%s\",\"heap\":%lu,\"results\":[",
               (unsigned)slot_count, (unsigned long)slot_us,
               raw_unit ? "raw" : "mV", (unsigned long)run_heap);

    for (uint8_t i = 0; i < slot_count; i++) {
        SchedSlot &s = slots[i];
        if (i > 0) {
            out.print(",");
        }
        if (s.kind == SCHED_KIND_MPP) {
            out.printf("{\"i\":%u,\"kind\":\"mpp\",\"ch\":%u,\"t_us\":%lu,\"ok\":1}",
                       (unsigned)i, (unsigned)s.ch, (unsigned long)s.start_us);
            continue;
        }

        out.printf("{\"i\":%u,\"kind\":\"listen\",\"ch\":%u,\"t_us\":%lu,"
                   "\"count\":%lu,\"dur_us\":%lu,\"full\":%u,\"data\":\"",
                   (unsigned)i, (unsigned)s.ch, (unsigned long)s.start_us,
                   (unsigned long)s.count, (unsigned long)s.dur_us,
                   s.truncated ? 1 : 0);
        for (uint32_t k = 0; k < s.count; k++) {
            if (raw_unit) {
                out.print(pool[s.off + k]);
            } else {
                out.print(rawToMilliVolts(pool[s.off + k]), 3);
            }
            if (k + 1 < s.count) {
                out.print(",");
            }
        }
        out.print("\"}");
    }

    out.println("]}");
}
