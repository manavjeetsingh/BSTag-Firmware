#include "esync.h"

#include <math.h>

#include "commands.h"
#include "config.h"
#include "hardware.h"
#include "schedule.h"

static const int8_t CODE[] = ESYNC_CODE;

#define CHIPS      ((int32_t)(sizeof(CODE) / sizeof(CODE[0])))
#define CHIP_BINS  ((int32_t)(ESYNC_CHIP_US / ESYNC_BIN_US))  /* bins per chip */
#define NBIN       (CHIPS * CHIP_BINS)                         /* bins per preamble */
#define RING       (NBIN + 1)                                  /* prefix sums: one extra */
#define BIN_SHIFT  4                                           /* bins kept as raw << 4 */

static_assert(ESYNC_CHIP_US % ESYNC_BIN_US == 0,
              "ESYNC_BIN_US must divide ESYNC_CHIP_US");
static_assert(ESYNC_FIRE_DELAY_US > ESYNC_CONFIRM_US + ESYNC_BIN_US,
              "the fire must land after the peak is confirmed");

struct EsyncLock {
    uint32_t t_us;       /* end of the preamble, from the correlation peak */
    float    rho;        /* correlation at the peak, 0..1 */
    float    on_mv;      /* mean level over the ON chips */
    float    off_mv;     /* mean level over the OFF chips */
    float    resid_mv;   /* per-bin RMS the code does not explain: noise */
};

static bool     listen_for_esync = false;

/* Newest sample, timestamped at the read so the gap between reading and
 * looking at it never lands in the timing. */
static uint16_t sample_raw = 0;
static uint32_t sample_us = 0;
static bool     sample_new = false;

/* Code constants, filled in by esyncReset(). */
static int32_t  code_sum = 0;     /* ON chips minus OFF chips */
static int32_t  code_on = 0;      /* number of ON chips */
static float    tmpl_energy = 0;  /* sum over bins of (code - mean)^2 */
static float    min_swing = 0;    /* ESYNC_MIN_SWING_MV in bin units */

/* Running prefix sums of the bin values and their squares. A window sum is
 * the difference of two entries; unsigned wraparound keeps the difference
 * right no matter how long the listener runs. */
static uint64_t p_x[RING];
static uint64_t p_xx[RING];
static uint32_t ring_head = 0;
static uint32_t bins_filled = 0;

/* The bin being accumulated. */
static bool     started = false;
static uint32_t bin_start_us = 0;
static uint32_t bin_acc = 0;
static uint32_t bin_cnt = 0;
static uint32_t last_val = 0;     /* an empty bin repeats the one before */

static uint32_t samples = 0;
static uint16_t stalls = 0;       /* gaps longer than a preamble: window restarted */

/* Peak tracking. Correlations are kept squared: the ordering is the same
 * for positive scores, and the square roots are only needed at the lock. */
static float    prev_r2 = 0;
static bool     have_best = false;
static float    best_r2 = 0;
static float    best_prev_r2 = 0;
static float    best_next_r2 = 0;
static uint32_t best_end_us = 0;
static uint32_t bins_since_best = 0;
static EsyncLock best;

/* Best score seen since arming, lockable or not: the "how close" number. */
static float    seen_r2 = 0;
static float    seen_swing_mv = 0;
static float    level_mv = 0;

/* The pending fire. Committed to from the lock. */
static bool     locked = false;
static uint32_t fire_at_us = 0;
static EsyncLock lock;

/* Last fire, latched for esyncReport(). */
static bool     rep_valid = false;
static uint32_t rep_fire_us = 0;
static EsyncLock rep;

static float binToMv(float v)
{
    return v * (ADC_REF_MV / 65535.0f / (float)(1 << BIN_SHIFT));
}

static uint32_t back(uint32_t n)
{
    return (ring_head + RING - n) % RING;
}

static void restartWindow(uint32_t t_us)
{
    ring_head = 0;
    p_x[0] = 0;
    p_xx[0] = 0;
    bins_filled = 0;
    bin_start_us = t_us;
    bin_acc = 0;
    bin_cnt = 0;
    prev_r2 = 0;
    have_best = false;
}

void esyncListen(void)
{
    switchChannel(ESYNC_CHANNEL);
    esyncReset();
    /* A new run invalidates the last one: better to report nothing than
     * to hand back a stale lock or a stale reply. */
    esyncClearReport();
    clearQueuedReply();
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

void esyncClearReport(void)
{
    rep_valid = false;
}

void esyncReset(void)
{
    code_sum = 0;
    code_on = 0;
    for (int32_t k = 0; k < CHIPS; k++) {
        code_sum += CODE[k];
        code_on += (CODE[k] > 0);
    }
    tmpl_energy = (float)CHIP_BINS *
                  (float)(CHIPS * CHIPS - code_sum * code_sum) / (float)CHIPS;
    min_swing = ESYNC_MIN_SWING_MV * 65535.0f * (float)(1 << BIN_SHIFT) /
                ADC_REF_MV;

    sample_new = false;
    started = false;
    restartWindow(0);
    last_val = 0;
    samples = 0;
    stalls = 0;
    seen_r2 = 0;
    seen_swing_mv = 0;
    level_mv = 0;
    locked = false;
    fire_at_us = 0;
}

void esyncReport(Print &out)
{
    if (!rep_valid) {
        /* Nothing has fired. primed means a full preamble's worth of bins
         * is in, armed means a peak is locked and the fire is committed,
         * and peak_rho is the best score seen: near ESYNC_MIN_RHO means the
         * preamble was heard but too noisy, near zero means it never was. */
        out.printf("{\"info\":\"esync\",\"pending\":0,\"listening\":%d,"
                   "\"primed\":%d,\"armed\":%d,\"samples\":%lu,\"stalls\":%u,"
                   "\"peak_rho\":%.3f,\"peak_swing_mv\":%.3f,\"level_mv\":%.3f}\n",
                   listen_for_esync ? 1 : 0,
                   bins_filled >= (uint32_t)NBIN ? 1 : 0,
                   locked ? 1 : 0,
                   (unsigned long)samples,
                   stalls,
                   sqrtf(seen_r2), seen_swing_mv, level_mv);
        return;
    }
    /* t_us is the end of the preamble as the correlation peak put it;
     * fire_us should sit ESYNC_FIRE_DELAY_US past it. swing_mv against
     * resid_mv is the margin: how far the pattern stood above what it
     * could not explain. */
    float swing = rep.on_mv - rep.off_mv;
    float snr_db = (rep.resid_mv > 0.0f && swing > 0.0f)
                   ? 20.0f * log10f(swing / rep.resid_mv) : 0.0f;
    out.printf("{\"info\":\"esync\",\"t_us\":%lu,\"fire_us\":%lu,"
               "\"delay_us\":%lu,\"rho\":%.3f,\"on_mv\":%.3f,\"off_mv\":%.3f,"
               "\"swing_mv\":%.3f,\"resid_mv\":%.3f,\"snr_db\":%.1f}\n",
               (unsigned long)rep.t_us,
               (unsigned long)rep_fire_us,
               (unsigned long)ESYNC_FIRE_DELAY_US,
               rep.rho, rep.on_mv, rep.off_mv, swing, rep.resid_mv, snr_db);
}

void esyncSample(void)
{
    sample_raw = readAdcRaw();
    sample_us = micros();
    sample_new = true;
}

/* Parabola through the peak and its neighbours, in bins, within +-0.5. */
static float peakOffset(float ym, float y0, float yp)
{
    float den = ym - 2.0f * y0 + yp;
    if (den >= 0.0f) {
        return 0.0f;
    }
    float d = 0.5f * (ym - yp) / den;
    return d > 0.5f ? 0.5f : (d < -0.5f ? -0.5f : d);
}

static void commitLock(void)
{
    float d = peakOffset(sqrtf(best_prev_r2), sqrtf(best_r2),
                         sqrtf(best_next_r2));
    lock = best;
    lock.t_us = best_end_us + (int32_t)lroundf(d * ESYNC_BIN_US);
    fire_at_us = lock.t_us + ESYNC_FIRE_DELAY_US;
    locked = true;
}

/* Score the window that ends at end_us: the last NBIN bins, oldest chip
 * first, against the code. */
static void evaluate(uint32_t end_us)
{
    const int64_t n = NBIN;
    int64_t x  = (int64_t)(p_x[ring_head] - p_x[back(NBIN)]);
    int64_t xx = (int64_t)(p_xx[ring_head] - p_xx[back(NBIN)]);

    int64_t on = 0, off = 0;
    for (int32_t k = 0; k < CHIPS; k++) {
        uint32_t from = NBIN - k * CHIP_BINS;
        int64_t s = (int64_t)(p_x[back(from - CHIP_BINS)] - p_x[back(from)]);
        if (CODE[k] > 0) {
            on += s;
        } else {
            off += s;
        }
    }

    /* Both scaled up to stay exact in integers: num is CHIPS times the
     * covariance sum, var_n is n times the variance sum. */
    int64_t num   = (int64_t)CHIPS * (on - off) - (int64_t)code_sum * x;
    int64_t var_n = n * xx - x * x;

    float r2 = 0.0f;
    float var = 0.0f;
    if (num > 0 && var_n > 0) {
        float cov = (float)num / (float)CHIPS;
        var = (float)var_n / (float)n;
        r2 = cov * cov / (tmpl_energy * var);
    }

    float on_mean  = (float)on  / (float)(code_on * CHIP_BINS);
    float off_mean = (float)off / (float)((CHIPS - code_on) * CHIP_BINS);
    float swing = on_mean - off_mean;

    level_mv = binToMv((float)x / (float)n);
    if (r2 > seen_r2) {
        seen_r2 = r2;
        seen_swing_mv = binToMv(swing);
    }

    bool passes = r2 >= ESYNC_MIN_RHO * ESYNC_MIN_RHO && swing >= min_swing;

    if (passes && (!have_best || r2 > best_r2)) {
        have_best = true;
        best_r2 = r2;
        best_prev_r2 = prev_r2;
        best_next_r2 = 0.0f;
        best_end_us = end_us;
        bins_since_best = 0;
        best.rho = sqrtf(r2);
        best.on_mv = binToMv(on_mean);
        best.off_mv = binToMv(off_mean);
        float resid = var / (float)n * (1.0f - r2);
        best.resid_mv = binToMv(sqrtf(resid > 0.0f ? resid : 0.0f));
    } else if (have_best) {
        if (bins_since_best == 0) {
            best_next_r2 = r2;
        }
        bins_since_best++;
        if (bins_since_best * ESYNC_BIN_US >= ESYNC_CONFIRM_US) {
            commitLock();
        }
    }
    prev_r2 = r2;
}

static void closeBin(uint32_t end_us)
{
    uint32_t val = bin_cnt ? (bin_acc << BIN_SHIFT) / bin_cnt : last_val;
    last_val = val;
    bin_acc = 0;
    bin_cnt = 0;

    uint32_t nh = (ring_head + 1) % RING;
    p_x[nh]  = p_x[ring_head]  + val;
    p_xx[nh] = p_xx[ring_head] + (uint64_t)val * val;
    ring_head = nh;

    if (bins_filled < (uint32_t)NBIN) {
        bins_filled++;
    }
    if (bins_filled >= (uint32_t)NBIN) {
        evaluate(end_us);
    }
}

static void feed(uint16_t raw, uint32_t t_us)
{
    samples++;

    if (!started) {
        started = true;
        restartWindow(t_us);
        last_val = (uint32_t)raw << BIN_SHIFT;
    }

    uint32_t nclose = (t_us - bin_start_us) / ESYNC_BIN_US;
    if (nclose > (uint32_t)NBIN) {
        /* The loop stalled for longer than a whole preamble. Filling that
         * many bins with a held value would only feed the correlator a flat
         * line with a step at each end, so start the window over. */
        stalls++;
        restartWindow(t_us);
        last_val = (uint32_t)raw << BIN_SHIFT;
        nclose = 0;
    }
    for (uint32_t i = 0; i < nclose; i++) {
        bin_start_us += ESYNC_BIN_US;
        closeBin(bin_start_us);
        if (locked) {
            return;
        }
    }

    bin_acc += raw;
    bin_cnt++;
}

/*
 * Preamble detector, driven one sample per loop() pass.
 *
 * The committed fire is checked ahead of everything else, and once locked
 * the correlator takes no more samples: nothing can sit between the
 * deadline and the dispatch, and nothing seen after the lock can move it.
 */
void esyncListening(void)
{
    if (!sample_new) {
        return;
    }
    sample_new = false;

    /* Signed difference so a micros() wrap inside the delay still
     * compares correctly. */
    if (locked) {
        if ((int32_t)(sample_us - fire_at_us) >= 0) {
            rep = lock;
            rep_fire_us = sample_us;
            rep_valid = true;

            /* One shot: disarm before dispatching, so a queued esync can
             * re-arm us cleanly instead of being undone by the stop. */
            esyncStop();
            /* A loaded schedule takes the fire instead of the single
             * queued command -- that is the whole difference between the
             * "multiple" and "individual" collection modes at this end.
             * sqc puts it back. */
            if (schedLoaded()) {
                schedRun();
            } else {
                runQueuedCommand();
            }
        }
        return;
    }

    feed(sample_raw, sample_us);
}
