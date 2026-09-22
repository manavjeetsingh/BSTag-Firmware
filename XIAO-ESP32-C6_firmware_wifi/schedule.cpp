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
    }

    /* Slot 0's transmitter has no previous slot to settle in, so everyone
     * waits out one preroll before the grid starts. Waiting on every tag
     * and not just the transmitter is deliberate: the grid has to be
     * common, and a tag that skipped it would start slot 0 early. */
    if (isMppSlot(0)) {
        switchChannel(SCHED_CHANNELS[0]);
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
            while (s.count < want &&
                   (int32_t)((micros() - t0) - slot_end) < 0) {
                pool[s.off + s.count++] = readAdcRaw();
            }
            next_off += s.count;
        }

        s.ch = current_channel;

        /* Pad to the boundary. If the NEXT slot is ours to transmit in,
         * spend the pad sitting on the sweep's first channel so the
         * rectifier is settled when it starts -- that is what lets
         * MPP_CHANNELS_SCHED drop the leading settle dwells. */
        if (isMppSlot(i + 1)) {
            switchChannel(SCHED_CHANNELS[0]);
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
                   "\"count\":%lu,\"full\":%u,\"data\":\"",
                   (unsigned)i, (unsigned)s.ch, (unsigned long)s.start_us,
                   (unsigned long)s.count, s.truncated ? 1 : 0);
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
