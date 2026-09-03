#include "esync.h"

#include <string.h>

#include "commands.h"
#include "config.h"
#include "hardware.h"

static bool     listen_for_esync = false;

/* Moving window of raw ADC codes, oldest sample at esync_head. */
static uint16_t esync_buf[ESYNC_BUF_LEN];
static uint32_t esync_head = 0;      /* next write position */
static uint32_t esync_count = 0;     /* valid samples, saturates at the len */
static uint32_t esync_sum = 0;       /* running sum, seeds the baseline */
static uint32_t esync_sample_us = 0; /* micros() of the newest sample */

/* Detector state. */
static uint16_t esync_drop_thr = 0;     /* fixed floor, in raw codes */
static bool     esync_primed = false;   /* baseline seeded, level state known */
static int32_t  esync_base_acc = 0;     /* baseline << ESYNC_BASELINE_SHIFT */

/* Packet state machine. Every rejection lands back in PKT_IDLE with the
 * listener still armed: a tag that misfires on a half-seen packet would
 * run its queued command at a time no other tag shares, which is worse
 * for the experiment than a tag that simply never fired. */
typedef enum {
    PKT_IDLE = 0,   /* carrier up, waiting for a blank to start */
    PKT_SFD,        /* inside a blank, timing it */
    PKT_PAYLOAD     /* SFD accepted, binning chip samples off t_sfd */
} PktState;

static PktState esync_state = PKT_IDLE;

/* Stats for the blank currently in progress. */
static uint32_t esync_low_enter_us = 0;
static uint16_t esync_low_min = 0;
static uint32_t esync_sfd_len_us = 0;

/* Payload capture. */
static uint32_t esync_t_sfd_us = 0;
static uint32_t esync_chip_sum[ESYNC_PKT_CHIPS];
static uint16_t esync_chip_n[ESYNC_PKT_CHIPS];

/* Rejection tallies, so a run that never fires can be diagnosed without
 * a scope: which test the environment keeps failing says what to widen. */
static uint16_t esync_rej_sfd = 0;    /* blank, but the wrong length */
static uint16_t esync_rej_chip = 0;   /* payload not well-formed Manchester */
static uint16_t esync_rej_id = 0;     /* decoded, but not the pinned id */
static uint16_t esync_rej_late = 0;   /* decode overran the fire instant */

/* Last packet, latched for esyncReport(). Printing from the detector
 * would put a blocking USB/UART write between the edge and
 * runQueuedCommand(), which is exactly the latency this path exists to
 * avoid. */
static bool     esync_rep_valid = false;
static uint32_t esync_rep_t_us = 0;      /* the fire instant */
static uint32_t esync_rep_sfd_us = 0;    /* t_sfd, the timing reference */
static uint32_t esync_rep_low_us = 0;    /* SFD blank length */
static uint16_t esync_rep_min = 0;
static uint16_t esync_rep_base = 0;
static uint8_t  esync_rep_id = 0;

void esyncListen(void)
{
    switchChannel(ESYNC_CHANNEL);
    esyncReset();
    /* A new run invalidates the last one: better to report nothing than
     * to hand back a stale edge or a stale reply. */
    esyncClearReport();
    clearQueuedReply();
    esync_rej_sfd = 0;
    esync_rej_chip = 0;
    esync_rej_id = 0;
    esync_rej_late = 0;
    listen_for_esync = true;
}

void esyncClearReport(void)
{
    esync_rep_valid = false;
}

void esyncReport(Print &out)
{
    if (!esync_rep_valid) {
        /* Nothing has fired. Report what the detector is actually doing so
         * a silent run can be told apart from a stuck one: not primed means
         * the warmup window has not filled, state names how far into a
         * packet it got, and the reject tallies say what it kept throwing
         * away. */
        int32_t base = esync_base_acc >> ESYNC_BASELINE_SHIFT;
        out.printf("{\"info\":\"esync\",\"pending\":0,\"listening\":%d,"
                   "\"primed\":%d,\"state\":%d,\"samples\":%lu,"
                   "\"base_mv\":%.3f,\"rej_sfd\":%u,\"rej_chip\":%u,"
                   "\"rej_id\":%u,\"rej_late\":%u}\n",
                   listen_for_esync ? 1 : 0,
                   esync_primed ? 1 : 0,
                   (int)esync_state,
                   (unsigned long)esync_count,
                   rawToMilliVolts((uint16_t)base),
                   esync_rej_sfd, esync_rej_chip,
                   esync_rej_id, esync_rej_late);
        return;
    }
    out.printf("{\"info\":\"esync\",\"edge\":\"packet\",\"t_us\":%lu,"
               "\"t_sfd_us\":%lu,\"low_us\":%lu,\"id\":%u,"
               "\"min_mv\":%.3f,\"base_mv\":%.3f}\n",
               (unsigned long)esync_rep_t_us,
               (unsigned long)esync_rep_sfd_us,
               (unsigned long)esync_rep_low_us,
               esync_rep_id,
               rawToMilliVolts(esync_rep_min),
               rawToMilliVolts(esync_rep_base));
}

void esyncStop(void)
{
    listen_for_esync = false;
    esyncReset();
}

bool esyncActive(void)
{
    return listen_for_esync;
}

void esyncReset(void)
{
    esync_head = 0;
    esync_count = 0;
    esync_sum = 0;
    esync_drop_thr = milliVoltsToRaw(ESYNC_MIN_BASELINE_MV);
    esync_primed = false;
    esync_base_acc = 0;
    esync_state = PKT_IDLE;
    esync_low_enter_us = 0;
    esync_low_min = 0;
    esync_sfd_len_us = 0;
    esync_t_sfd_us = 0;
    memset(esync_chip_sum, 0, sizeof(esync_chip_sum));
    memset(esync_chip_n, 0, sizeof(esync_chip_n));
}

void esyncSample(void)
{
    uint16_t s = readAdcRaw();
    /* Timestamped at the read, not where it is looked at, so the gap
     * between the two never lands in a chip boundary or an edge time. */
    esync_sample_us = micros();

    if (esync_count == ESYNC_BUF_LEN) {
        esync_sum -= esync_buf[esync_head];   /* evict the oldest */
    } else {
        esync_count++;
    }

    esync_buf[esync_head] = s;
    esync_sum += s;
    esync_head = (esync_head + 1) % ESYNC_BUF_LEN;
}

/*
 * Slice the binned chips and decode. Fails on a chip the sample loop
 * stalled through, and on any bit whose two halves came out the same --
 * that is not a Manchester transition, so whatever produced it was not
 * our exciter.
 */
static bool esyncDecode(uint32_t rearm_thr, uint8_t *id_out)
{
    uint8_t level[ESYNC_PKT_CHIPS];

    for (int i = 0; i < ESYNC_PKT_CHIPS; i++) {
        if (esync_chip_n[i] < ESYNC_CHIP_MIN_SAMPLES) {
            return false;
        }
        uint32_t mean = esync_chip_sum[i] / esync_chip_n[i];
        level[i] = (mean >= rearm_thr) ? 1 : 0;
    }

    uint8_t id = 0;
    for (int k = 0; k < ESYNC_PKT_BITS; k++) {
        uint8_t first  = level[2 * k];
        uint8_t second = level[2 * k + 1];
        if (first == second) {
            return false;
        }
        /* IEEE 802.3: low-then-high is a 1. MSB first, matching the
         * exciter's manchester_chips(). */
        id = (uint8_t)((id << 1) | second);
    }

    *id_out = id;
    return true;
}

/*
 * Packet detector, driven one sample per loop() pass.
 *
 * The idle level is tracked with a leaky integrator that is frozen while
 * the signal is down or a packet is in flight, so it settles on the
 * carrier level no matter how often packets come or how long the SFD
 * lasts.
 *
 * Falling into a blank is an absolute test: the signal has to reach the
 * floor, ESYNC_MIN_BASELINE_MV or below. Coming back out is judged at
 * ESYNC_REARM_PCT below the tracked baseline, so the return is measured
 * against the current idle level rather than a fixed number. The gap
 * between the two thresholds is the hysteresis.
 */
void esyncListening(void)
{
    if (esync_count == 0) {
        return;
    }

    uint16_t newest = esync_buf[(esync_head + ESYNC_BUF_LEN - 1) % ESYNC_BUF_LEN];
    uint32_t now_us = esync_sample_us;

    /* Seed the baseline from the warmup window, then adopt the current
     * level without calling it an edge. */
    if (!esync_primed) {
        if (esync_count < ESYNC_WARMUP_SAMPLES) {
            return;
        }
        int32_t seed = (int32_t)(esync_sum / esync_count);
        esync_base_acc = seed << ESYNC_BASELINE_SHIFT;
        esync_primed = true;
        esync_state = PKT_IDLE;
        return;
    }

    int32_t  baseline  = esync_base_acc >> ESYNC_BASELINE_SHIFT;
    uint32_t drop_thr  = esync_drop_thr;
    uint32_t rearm_thr = (uint32_t)baseline * (100 - ESYNC_REARM_PCT) / 100;

    if (rearm_thr <= drop_thr) {
        /* No carrier, or one so weak that the two thresholds cross over
         * and the state machine would rattle between them. Keep letting
         * the baseline climb so we recover if the signal comes back. */
        esync_base_acc += (int32_t)newest - baseline;
        esync_state = PKT_IDLE;
        return;
    }

    switch (esync_state) {
    case PKT_IDLE:
        if (newest <= drop_thr) {
            esync_state = PKT_SFD;
            esync_low_enter_us = now_us;
            esync_low_min = newest;
        } else if (newest >= rearm_thr) {
            /* Clearly at rest: the only place the baseline moves. Samples
             * mid-transition are skipped so an edge cannot pull it down. */
            esync_base_acc += (int32_t)newest - baseline;
        }
        break;

    case PKT_SFD:
        if (newest < esync_low_min) {
            esync_low_min = newest;
        }
        if (newest >= rearm_thr) {
            uint32_t low_us = now_us - esync_low_enter_us;
            if (low_us >= ESYNC_SFD_MIN_US && low_us <= ESYNC_SFD_MAX_US) {
                /* This crossing is t_sfd: everything downstream is timed
                 * from here, including the fire instant. */
                esync_t_sfd_us = now_us;
                esync_sfd_len_us = low_us;
                memset(esync_chip_sum, 0, sizeof(esync_chip_sum));
                memset(esync_chip_n, 0, sizeof(esync_chip_n));
                esync_state = PKT_PAYLOAD;
            } else {
                esync_rej_sfd++;
                esync_state = PKT_IDLE;
            }
        }
        break;

    case PKT_PAYLOAD: {
        uint32_t elapsed = now_us - esync_t_sfd_us;

        if (elapsed < ESYNC_GUARD_US) {
            break;              /* still in the guard, nothing to bin */
        }

        if (elapsed < ESYNC_PAYLOAD_END_US) {
            uint32_t into  = elapsed - ESYNC_GUARD_US;
            uint32_t chip  = into / ESYNC_CHIP_US;
            uint32_t phase = into - chip * ESYNC_CHIP_US;

            /* Middle half only. The outer quarters are ramp and whatever
             * the loop period smeared across the boundary. */
            if (phase >= ESYNC_CHIP_US / 4 &&
                phase < (3 * ESYNC_CHIP_US) / 4) {
                esync_chip_sum[chip] += newest;
                esync_chip_n[chip]++;
            }
            break;
        }

        /* Payload window is over. */
        uint8_t id = 0;
        if (!esyncDecode(rearm_thr, &id)) {
            esync_rej_chip++;
            esync_state = PKT_IDLE;
            break;
        }
        if (ESYNC_PKT_REQUIRE_ID >= 0 && id != (uint8_t)ESYNC_PKT_REQUIRE_ID) {
            esync_rej_id++;
            esync_state = PKT_IDLE;
            break;
        }
        if (micros() - esync_t_sfd_us > ESYNC_FIRE_OFFSET_US) {
            /* Decode ran past the instant every other tag is firing at.
             * Firing now would put this tag's command somewhere no one
             * else is, so drop the packet and wait for the next one. */
            esync_rej_late++;
            esync_state = PKT_IDLE;
            break;
        }

        /* Spin out the remaining slack. The wait is what makes the fire
         * instant identical across tags: it is a fixed offset from an
         * edge they all saw, rather than whenever each one's decode
         * happened to finish. */
        while (micros() - esync_t_sfd_us < ESYNC_FIRE_OFFSET_US) {
            /* nothing */
        }

        esync_rep_t_us   = micros();
        esync_rep_sfd_us = esync_t_sfd_us;
        esync_rep_low_us = esync_sfd_len_us;
        esync_rep_min    = esync_low_min;
        esync_rep_base   = (uint16_t)baseline;
        esync_rep_id     = id;
        esync_rep_valid  = true;

        /* One shot: disarm before dispatching, so a queued esync can
         * re-arm us cleanly instead of being undone by the stop. */
        esyncStop();
        runQueuedCommand();
        break;
    }
    }
}
