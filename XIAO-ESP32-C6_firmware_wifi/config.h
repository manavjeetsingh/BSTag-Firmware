#ifndef CONFIG_H
#define CONFIG_H

/* Compile WiFi/TCP support in at all. 0 strips net.cpp's WiFi.h use and
 * the TCP session slots out of the build -- smaller flash/RAM, and
 * secrets.h is not needed to compile. The "mac" command still works: it
 * reads efuse directly instead of going through the WiFi driver.
 *
 * This is independent of (and stronger than) the runtime switch: with
 * this at 1, an empty WIFI_SSID in secrets.h still disables the radio at
 * runtime (see wifiStart()) but the WiFi/TCP code is still linked in. */
#define NET_ENABLED             0

#if NET_ENABLED
#include "secrets.h"
#endif

#define TCP_PORT               3333
#define MAX_TCP_CLIENTS        2
#define WIFI_RETRY_MS          5000
#define WIFI_LOW_LATENCY       1      /* 1 = disable modem sleep (more power) */

/* XIAO ESP32-C6 pin mapping by physical XIAO header position. */
#define PIN_RF_V1              20   /* D9 */
#define PIN_RF_V2              19   /* D8 */
#define PIN_RF_V3              17   /* D7 */
#define PIN_SPI_MISO           2    /* D2 */
#define PIN_SPI_MOSI           22   /* D4 */
#define PIN_SPI_SCLK           21   /* D3 */
#define PIN_SPI_CS             1    /* D1 */
#define PIN_POWER_EN           23   /* D5 */
#define PIN_STATUS_LED         16   /* D6 */
#define PIN_USER_LED           15   /* Built-in LED, active-low */
#define PIN_D0                  0   /* D0 */
#define POWER_EN_ACTIVE_LEVEL  HIGH

#define SERIAL_BAUD            921600UL
#define SPI_CLOCK_HZ           40000000UL
#define MAX_ADC_SAMPLES        1500
#define ADC_REF_MV             5000.0f
#define CMD_BUF_LEN            32     /* max command length incl. terminator */
#define OUT_CHUNK_LEN          1024   /* TX coalescing buffer, see BufferedOut */

/* Buffered capture (rdb/rds). Stored as raw codes: 2 bytes/sample. */
#define CAPTURE_BUF_LEN        10000
#define CAPTURE_CHANNEL        2      /* channel forced on rdb */

/* Exciter sync (esync). Moving window of raw codes: 2 bytes/sample.
 *
 * The exciter sends one blank of a known length. The tag times off the
 * FALLING edge that starts it and fires ESYNC_FIRE_DELAY_US later, which
 * lands on the far side of the blank without needing to see the rise:
 *
 *      idle        blank        idle
 *      ────┐                 ┌────────
 *          └─────────────────┘
 *          ^<-- FIRE_DELAY -->^
 *      t_fall              fires here
 *
 * Timing off the fall rather than the rise costs the length test, and
 * that is the whole trade. Firing on the rise meant the blank's length
 * was already known when the edge arrived, so a fade could be rejected
 * for free. Committing on the fall means committing before the length is
 * known, so a blank that runs LONG -- an outage, or an ESYNC_TIME_SCALE
 * mismatch with the exciter -- can no longer be rejected: the tag has
 * already fired by the time that shows. What survives is the one thing
 * decidable inside the delay window: a blank that ends before
 * ESYNC_BLANK_MIN_US (rej_short) is thrown away without firing. The
 * measured length is reported afterwards by esyncr so a bad sync is
 * still diagnosable after the fact.
 *
 * The falling edge is the timing reference, and it is simply the first
 * sample at or under ESYNC_MIN_BASELINE_MV. The exciter drives the
 * carrier to the floor every time, so that single absolute test does
 * double duty: it is the timing reference AND the proof the blank is
 * real, since a sag that never reaches the floor never latches an edge.
 *
 * Judging the fall against a fraction of baseline instead would zero the
 * spread between tags receiving different amplitudes, but that spread is
 * already a fraction of a percent of the fall time -- under a
 * microsecond, against a sample period of tens -- so it would buy nothing
 * measurable while making the timing reference depend on the tracked
 * baseline being right. The baseline is still used for the RETURN, which
 * is not timing-critical; it only follows the signal while it is at rest,
 * so a blank cannot drag it down however long it lasts.
 *
 * Mirrored in BladeRFCode/null_sync/manual_null_exciter.py; the blank
 * length has to be edited in both places at once. */
#define ESYNC_BUF_LEN          10000
#define ESYNC_CHANNEL          2      /* RX channel forced on esync */
#define ESYNC_WARMUP_SAMPLES   1000   /* samples used to seed the baseline */
#define ESYNC_MIN_BASELINE_MV  2.0f   /* floor level: at or under this is a drop,
                                        and the falling edge the tag times
                                        off. Measured blanks bottom out around
                                        0.8 mV, so this clears the floor with
                                        room to spare while staying far below
                                        any real carrier. */
#define ESYNC_REARM_PCT        50     /* % below baseline that counts as back
                                        up. Used for the RISE only -- the fall
                                        is the absolute floor test above. This
                                        is hysteresis, not a timing reference:
                                        it only has to sit far enough above
                                        the floor that the state machine
                                        cannot rattle, so the exact value is
                                        not critical. Needs a baseline above
                                        2*ESYNC_MIN_BASELINE_MV to stay clear
                                        of the floor. */
#define ESYNC_BASELINE_SHIFT   10     /* baseline IIR time constant, 1<<n samples */
#define ESYNC_WIFI_QUIET_MS    50     /* ack drain before the radio goes down */
#define ESYNC_WIFI_TIMEOUT_MS  30000  /* no packet by now: bring the radio back */

/* Time scale, multiplying the blank length below. MUST equal N in
 * manual_null_exciter.py, with the tag reflashed to match: the detector
 * works in absolute microseconds and cannot infer the scale off the air.
 *
 * Timing off the fall makes a mismatch here worse than it used to be, and
 * worth re-checking whenever either side moves. It no longer merely stops
 * the tag firing: ESYNC_FIRE_DELAY_US scales with this, so a tag flashed
 * for the wrong N still fires, just at the wrong moment -- early if this
 * is low, out past the end of the blank if it is high. Confirm it against
 * esyncr's low_us, which reports what the blank actually measured.
 *
 * The exciter's ramp is deliberately not scaled with this. The tag
 * latches the fall when the signal reaches the floor, so a longer ramp
 * simply moves t_fall later by roughly the ramp length and drags every
 * tag's fire with it. Keep the edges sharp and the offset stays small. */
#define ESYNC_TIME_SCALE           1

/* When the queued command fires, measured from the falling edge. This is
 * the blank's nominal length, so the command lands as the carrier comes
 * back -- the same instant the old rising-edge detector fired on, reached
 * by dead reckoning instead of by watching for the rise.
 *
 * MUST match DROP_MS in manual_null_exciter.py. Nothing on the air can
 * check this for you: the tag commits to the delay at the falling edge,
 * so an exciter sending a different length is not detected, it is just
 * fired against at the wrong time.
 *
 * esyncr's low_us is the check, but read it as an approximation rather
 * than an equality. It is measured between two different thresholds --
 * from the floor on the way down to ESYNC_REARM_PCT on the way up -- so
 * it under-reads DROP_MS by about the fall time plus half the rise, a
 * millisecond or two in practice. What matters is that it is STABLE and
 * agrees across tags. A drift of many ms, or tags disagreeing, is the
 * mismatch this constant cannot otherwise reveal. */
#define ESYNC_FIRE_DELAY_US    ( 50000 * ESYNC_TIME_SCALE)

/* The expected blank length. These two no longer play the same role as
 * each other, which is the visible cost of timing off the fall.
 *
 * MIN is still a gate, and still the main thing separating a deliberate
 * blank from a fade. A blank that ends before it has fired nothing: the
 * carrier came back inside the delay window, which the tag can see and
 * act on while it still has the choice.
 *
 * MAX is no longer a gate. The tag has already fired by the time a blank
 * overruns it, so this only decides when esyncr calls the blank it fired
 * against a long one. It costs nothing to keep and it is the one warning
 * that an outage or a scale mismatch was synced against, so leave it at
 * whatever the exciter should not exceed.
 *
 * Kept wide enough that every exciter in BladeRFCode is accepted:
 * null_sync/manual_null_exciter.py sends 50 ms, and the old 40 ms blank
 * still works. Narrow MIN toward whatever your exciter actually sends
 * only if tags start firing on things that are not it -- and check
 * esyncr's rej_short first, since a MIN that is too tight looks exactly
 * like a tag that cannot hear the exciter.
 *
 * Note periodic_null_exciter.py's 2000 ms blank is outside this on
 * purpose. That one is now a genuine hazard rather than merely an
 * unsupported exciter: a 2 s blank still trips the falling edge and
 * still fires ESYNC_FIRE_DELAY_US later, 1.95 s before the carrier is
 * back, and only esyncr's long flag afterwards says so. */
#define ESYNC_BLANK_MIN_US     ( 10000 * ESYNC_TIME_SCALE)
#define ESYNC_BLANK_MAX_US     (120000 * ESYNC_TIME_SCALE)

/* Deferred reply. The queued command fires while the radio is down, so
 * its reply is captured here and handed over with qr after the host
 * reconnects. Sized for the worst case, MAX_ADC_SAMPLES in mV: each
 * sample prints as up to "5000.000," = 9 B, plus the header. Raising
 * MAX_ADC_SAMPLES without raising this makes qr answer with an overflow
 * error instead of the trace. */
#define QUEUED_REPLY_BUF_LEN   16384

/* Streaming plotter (spl/epl). Emits "mV,d0_level" per line for Arduino
 * Serial Plotter, mV as the ADC channel voltage, d0_level as 0 or 1. */
#define PLOTTER_CHANNEL        2      /* channel forced on spl */
#define PLOTTER_PERIOD_MS      50

/* MPP sweep. */
/* Per-channel dwell. The esync-fired sweep gets its own value so the
 * synchronised run can dwell differently from an interactive probe. */
#define MPP_DWELL_US           3000   /* interactive mpp/mpp_<n> */
#define MPP_DWELL_QUEUED_US    3000   /* mpp fired by the esync detector */
#define MPP_MAX_PASSES         1000

#if NET_ENABLED
#define SESSION_COUNT          (1 + MAX_TCP_CLIENTS)   /* slot 0 is Serial */
#else
#define SESSION_COUNT          1                       /* Serial only */
#endif

#endif /* CONFIG_H */
