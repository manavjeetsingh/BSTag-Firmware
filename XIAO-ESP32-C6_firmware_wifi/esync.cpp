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
static uint32_t esync_sample_us = 0; /* micros() of the newest sample */

/* Detector state. */
static uint16_t esync_drop_thr = 0;     /* fixed floor, in raw codes */
static bool     esync_primed = false;   /* baseline seeded, level state known */
static bool     esync_high = true;      /* false while the signal is dropped */
static int32_t  esync_base_acc = 0;     /* baseline << ESYNC_BASELINE_SHIFT */

/* The pending fire. Latched at the falling edge and committed to from
 * that moment: once armed, the only thing that can call it off is the
 * carrier coming back early. */
static bool     esync_fire_armed = false;
static uint32_t esync_fire_at_us = 0;

/* Stats for the blank currently in progress. */
static uint32_t esync_low_enter_us = 0;
static uint16_t esync_low_min = 0;
/* The blank's measured length, filled in if the carrier returns before
 * the fire deadline. Zero at fire time means it had not come back yet. */
static uint32_t esync_low_us = 0;

/* Lowest sample since arming. A run where nothing fires and this never
 * drops near the floor means the blank is not reaching ESYNC_MIN_BASELINE_MV
 * at all, which no amount of widening the length window will fix. */
static uint16_t esync_seen_min = 0xFFFF;

/* Rejection tally, so a run that never fires says why without a scope:
 * the floor was reached but the carrier came back before
 * ESYNC_BLANK_MIN_US -- a fade rather than a blank. It is decided inside
 * the delay window, while calling the fire off is still possible.
 *
 * A blank too SHALLOW to be real needs no tally: the falling edge is the
 * floor test, so a sag that never reaches ESYNC_MIN_BASELINE_MV never
 * latches an edge in the first place. min_mv against base_mv is what says
 * so, exactly as it did before anything fired.
 *
 * There is deliberately no rej_long counterpart either. A blank that
 * overruns is only knowable after the tag has already fired against it,
 * so it cannot be a rejection -- it comes back as the "long" flag on the
 * fired report. */
static uint16_t esync_rej_short = 0;

/* Last edge, latched for esyncReport(). Printing from the detector would
 * put a blocking USB/UART write between the edge and runQueuedCommand(),
 * which is exactly the latency this whole path exists to avoid. */
static bool     esync_rep_valid = false;
static uint32_t esync_rep_t_us = 0;
static uint32_t esync_rep_fire_us = 0;
static uint32_t esync_rep_low_us = 0;
static uint16_t esync_rep_min = 0;
static uint16_t esync_rep_base = 0;

void esyncListen(void)
{
    switchChannel(ESYNC_CHANNEL);
    esyncReset();
    /* A new run invalidates the last one: better to report nothing than
     * to hand back a stale edge or a stale reply. */
    esyncClearReport();
    clearQueuedReply();
    esync_rej_short = 0;
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
         * the warmup window has not filled, in_blank means it is inside a
         * blank, armed means a falling edge is latched and the fire is
         * already committed, and min_mv against base_mv says whether the
         * signal is reaching the floor at all. */
        int32_t base = esync_base_acc >> ESYNC_BASELINE_SHIFT;
        out.printf("{\"info\":\"esync\",\"pending\":0,\"listening\":%d,"
                   "\"primed\":%d,\"in_blank\":%d,\"armed\":%d,\"samples\":%lu,"
                   "\"base_mv\":%.3f,\"min_mv\":%.3f,"
                   "\"rej_short\":%u}\n",
                   listen_for_esync ? 1 : 0,
                   esync_primed ? 1 : 0,
                   esync_high ? 0 : 1,
                   esync_fire_armed ? 1 : 0,
                   (unsigned long)esync_count,
                   rawToMilliVolts((uint16_t)base),
                   rawToMilliVolts(esync_seen_min == 0xFFFF ? 0 : esync_seen_min),
                   esync_rej_short);
        return;
    }
    /* t_us is the falling edge the timing came off; fire_us is when the
     * queued command actually ran, and should sit ESYNC_FIRE_DELAY_US past
     * it. low_us is the blank as measured -- the after-the-fact check on
     * everything the fall-triggered detector could not check in advance.
     * It reads 0 when the carrier had not returned by the time we fired,
     * which is the loud case: the blank was longer than the tag assumed.
     * "long" folds both overruns into one flag for a host that just wants
     * to know whether to trust the shot. */
    bool never_returned = (esync_rep_low_us == 0);
    bool ran_long = never_returned || (esync_rep_low_us > ESYNC_BLANK_MAX_US);
    out.printf("{\"info\":\"esync\",\"edge\":\"fall\",\"t_us\":%lu,"
               "\"fire_us\":%lu,\"delay_us\":%lu,\"low_us\":%lu,"
               "\"returned\":%d,\"long\":%d,"
               "\"min_mv\":%.3f,\"base_mv\":%.3f}\n",
               (unsigned long)esync_rep_t_us,
               (unsigned long)esync_rep_fire_us,
               (unsigned long)ESYNC_FIRE_DELAY_US,
               (unsigned long)esync_rep_low_us,
               never_returned ? 0 : 1,
               ran_long ? 1 : 0,
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
    esync_high = true;
    esync_base_acc = 0;
    esync_fire_armed = false;
    esync_fire_at_us = 0;
    esync_low_enter_us = 0;
    esync_low_min = 0;
    esync_low_us = 0;
    esync_seen_min = 0xFFFF;
}

void esyncSample(void)
{
    uint16_t s = readAdcRaw();
    /* Timestamped at the read, not where it is looked at, so the gap
     * between the two never lands in the measured blank length. */
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
 * Blank detector, driven one sample per loop() pass.
 *
 * The idle level is tracked with a leaky integrator that is frozen while
 * the signal is down, so it settles on the baseline no matter how often
 * the blanks come or how long they last.
 *
 * Timing comes off the FALLING edge, taken as the first sample at or
 * under ESYNC_MIN_BASELINE_MV. That one absolute test is both the timing
 * reference and the proof the blank is real: the exciter drives the
 * carrier to the floor, so a sag that never gets there never latches an
 * edge, and no separate depth check is needed.
 *
 * An absolute floor rather than a fraction of baseline costs almost
 * nothing here. Tags receiving different amplitudes cross a fixed low
 * threshold at slightly different points down the fall, but with the
 * threshold this close to zero the spread is a fraction of a percent of
 * the fall time -- well under a microsecond, against a sample period of
 * tens. A proportional threshold would zero that term and buy nothing
 * measurable, while making the reference depend on the tracked baseline
 * being right.
 *
 * The queued command then runs ESYNC_FIRE_DELAY_US after that edge, by
 * dead reckoning, which lands where the carrier is due back.
 *
 * The order of business inside a blank is what makes this safe. The fire
 * deadline is checked before anything else, so nothing -- not a
 * threshold recomputation, not a baseline collapse -- can sit between the
 * deadline and the dispatch. The one thing that can still call the fire
 * off is decided strictly earlier than that deadline: the early-return
 * test at ESYNC_BLANK_MIN_US, where a blank that is already over was a
 * fade, and the carrier coming back is proof of it while there is still
 * time to act on the proof.
 *
 * What cannot be checked is the other end. The tag is committed from the
 * falling edge, so a blank that overruns -- an outage, a scale mismatch
 * -- is fired against regardless and only shows up afterwards, in the
 * measured low_us that esyncReport() hands back. That is the price of
 * timing off the fall, and it is why low_us is worth reading on any run
 * whose results look off.
 */
void esyncListening(void)
{
    if (esync_count == 0) {
        return;
    }

    uint16_t newest = esync_buf[(esync_head + ESYNC_BUF_LEN - 1) % ESYNC_BUF_LEN];
    uint32_t now_us = esync_sample_us;

    /* Seed the baseline from the warmup window, then adopt the current
     * level without calling it an edge. Priming never arms a fire: the
     * transition into a known state is not a falling edge. */
    if (!esync_primed) {
        if (esync_count < ESYNC_WARMUP_SAMPLES) {
            return;
        }
        int32_t seed = (int32_t)(esync_sum / esync_count);
        esync_base_acc = seed << ESYNC_BASELINE_SHIFT;
        esync_primed = true;
        esync_high = (newest > esync_drop_thr);
        esync_fire_armed = false;
        return;
    }

    /* The committed fire, ahead of every other consideration. Signed
     * difference so a micros() wrap inside the delay window still
     * compares correctly. */
    if (esync_fire_armed && (int32_t)(now_us - esync_fire_at_us) >= 0) {
        esync_rep_t_us    = esync_low_enter_us;
        esync_rep_fire_us = now_us;
        esync_rep_low_us  = esync_low_us;
        esync_rep_min     = esync_low_min;
        esync_rep_base    = (uint16_t)(esync_base_acc >> ESYNC_BASELINE_SHIFT);
        esync_rep_valid   = true;

        /* One shot: disarm before dispatching, so a queued esync can re-arm
         * us cleanly instead of being undone by the stop. */
        esyncStop();
        runQueuedCommand();
        return;
    }

    if (newest < esync_seen_min) {
        esync_seen_min = newest;
    }

    int32_t  baseline = esync_base_acc >> ESYNC_BASELINE_SHIFT;
    uint32_t drop_thr = esync_drop_thr;
    /* Only the RETURN is judged against the baseline. It is not a timing
     * reference -- it just has to mean "clearly back up", far enough above
     * the floor to be hysteresis rather than chatter. */
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
            /* Falling edge: at the floor, so this is a blank and not a
             * sag. This instant is the whole timing reference, so the
             * deadline is set from it here and never revised. */
            esync_high = false;
            esync_low_enter_us = now_us;
            esync_low_min = newest;
            esync_low_us = 0;
            esync_fire_at_us = now_us + ESYNC_FIRE_DELAY_US;
            esync_fire_armed = true;
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

    if (!esync_fire_armed) {
        /* Down, but with no fire pending: primed into a dark band, or a
         * blank already thrown out. Wait for a settled return before
         * calling the signal high again -- going high while still under
         * the threshold would re-trigger on the tail of the same blank. */
        if (newest >= rearm_thr) {
            esync_high = true;
        }
        return;
    }

    if (esync_low_us == 0 && newest >= rearm_thr) {
        /* The carrier is back, and early enough that we still have the
         * choice. Under ESYNC_BLANK_MIN_US this was a fade: throw the fire
         * away. Otherwise record the length and let the deadline stand --
         * a blank that ends slightly early still fires where the exciter
         * meant it to. */
        uint32_t low_us = now_us - esync_low_enter_us;
        if (low_us < ESYNC_BLANK_MIN_US) {
            esync_rej_short++;
            esync_fire_armed = false;
            esync_high = true;
            return;
        }
        esync_low_us = low_us;
    }
}
