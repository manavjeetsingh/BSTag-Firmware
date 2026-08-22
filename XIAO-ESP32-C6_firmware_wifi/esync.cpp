#include "esync.h"

#include "commands.h"
#include "config.h"
#include "hardware.h"

static bool     listen_for_esync = false;

/* Moving window of raw ADC codes, oldest sample at esync_head. */
static uint16_t esync_buf[ESYNC_BUF_LEN];
static uint32_t esync_head = 0;      /* next write position */
static uint32_t esync_count = 0;     /* valid samples, saturates at the len */
static uint32_t esync_sum = 0;       /* running sum, seeds the baseline */

/* Detector state. */
static uint16_t esync_drop_thr = 0;     /* fixed floor, in raw codes */
static bool     esync_primed = false;   /* baseline seeded, level state known */
static bool     esync_high = true;      /* false while the signal is dropped */
static int32_t  esync_base_acc = 0;     /* baseline << ESYNC_BASELINE_SHIFT */

/* Stats for the drop currently in progress. */
static uint32_t esync_low_enter_us = 0;
static uint16_t esync_low_min = 0;

void esyncListen(void)
{
    switchChannel(ESYNC_CHANNEL);
    esyncReset();
    listen_for_esync = true;
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
    esync_high = true;
    esync_base_acc = 0;
    esync_low_enter_us = 0;
    esync_low_min = 0;
}

void esyncSample(void)
{
    uint16_t s = readAdcRaw();

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
 * logic to check exc sync signal in the
 * esync_buf.
 *
 * The idle level is tracked with a leaky integrator that is frozen while
 * the signal is down, so it settles on the baseline no matter how often
 * the drops come or how long they last.
 *
 * Arming is an absolute test: the signal has to reach the floor,
 * ESYNC_MIN_BASELINE_MV or below. The rising edge then fires at
 * ESYNC_REARM_PCT below the tracked baseline, so the return is judged
 * against the current idle level rather than a fixed number. The gap
 * between the two is the hysteresis.
 */
void esyncListening(void)
{
    if (esync_count == 0) {
        return;
    }

    uint16_t newest = esync_buf[(esync_head + ESYNC_BUF_LEN - 1) % ESYNC_BUF_LEN];

    /* Seed the baseline from the warmup window, then adopt the current
     * level without calling it an edge. */
    if (!esync_primed) {
        if (esync_count < ESYNC_WARMUP_SAMPLES) {
            return;
        }
        int32_t seed = (int32_t)(esync_sum / esync_count);
        esync_base_acc = seed << ESYNC_BASELINE_SHIFT;
        esync_primed = true;
        esync_high = (newest > esync_drop_thr);
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
        return;
    }

    if (esync_high) {
        if (newest <= drop_thr) {
            esync_high = false;                 /* falling edge, arm */
            esync_low_enter_us = micros();
            esync_low_min = newest;
        } else if (newest >= rearm_thr) {
            /* Clearly at rest: the only place the baseline moves. Samples
             * mid-transition are skipped so an edge cannot pull it down. */
            esync_base_acc += (int32_t)newest - baseline;
        }
        return;
    }

    if (newest < esync_low_min) {
        esync_low_min = newest;
    }

    if (newest >= rearm_thr) {
        uint32_t now_us = micros();
        uint32_t low_us = now_us - esync_low_enter_us;

        Serial.printf("{\"info\":\"esync\",\"edge\":\"rise\",\"t_us\":%lu,"
                      "\"low_us\":%lu,\"min_mv\":%.3f,\"base_mv\":%.3f}\n",
                      (unsigned long)now_us,
                      (unsigned long)low_us,
                      rawToMilliVolts(esync_low_min),
                      rawToMilliVolts((uint16_t)baseline));

        /* One shot: disarm before dispatching, so a queued esync can
         * re-arm us cleanly instead of being undone by the stop. */
        esyncStop();
        runQueuedCommand();
    }
}
