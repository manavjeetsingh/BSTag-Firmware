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
#define NET_ENABLED             1

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

/* Exciter sync (esync). The exciter keys the carrier on/off through
 * ESYNC_CODE, one chip per ESYNC_CHIP_US
 * (BladeRFCode/ASK_sync/manual_ask_sync_exciter.py), and the tag
 * correlates the whole preamble against the code instead of testing single
 * samples against a floor:
 *
 *      idle  [1 1 1 1 1 0 0 1 1 0 1 0 1]  idle
 *      ────┐ ┌───────┐   ┌──┐ ┌┐ ┌──────────
 *          └─┘       └───┘  └─┘└─┘
 *                                  ^<- FIRE_DELAY ->^
 *                          correlation peak     fires here
 *
 * Samples are averaged into ESYNC_BIN_US time bins first, so the
 * correlator works on a uniform time grid however unevenly loop() runs.
 * The score is the Pearson correlation of the last preamble's worth of
 * bins against the mean-removed code: it depends on the signal's shape and
 * not its level, so the same threshold serves a 10 mV tag and a 500 mV
 * one, and a steady offset or interferer cancels out.
 *
 * The timing reference is the correlation peak -- the instant the window
 * lined up with the end of the preamble -- interpolated between bins. It
 * sits a fixed few hundred us after the true end, set by the detector's
 * time constant: shared by tags with the same front end, not by tags
 * without one.
 *
 * A mismatch with the exciter fails safe: a wrong chip length or code does
 * not fire at the wrong time, it just never correlates. ESYNC_CHIP_US and
 * ESYNC_CODE must still match CHIP_MS and BARKER_CODE in the exciter. */
#define ESYNC_CHANNEL          2      /* RX channel forced on esync */
#define ESYNC_CODE             { 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1 }
#define ESYNC_CHIP_US          2000   /* == CHIP_MS in the exciter */
#define ESYNC_BIN_US           100    /* must divide ESYNC_CHIP_US */
#define ESYNC_MIN_RHO          0.80f  /* correlation needed to lock, 0..1. Lower
                                         finds weaker preambles and is fooled
                                         more easily by on/off traffic near the
                                         chip rate; 0.85 stops that but loses
                                         ~5 mV preambles. */
#define ESYNC_MIN_SWING_MV     1.0f   /* on-minus-off needed to lock. A floor
                                         under the correlation, not the test:
                                         it only stops a flat carrier's noise
                                         from locking, and sits 10x under the
                                         weakest tag this is meant for. */
#define ESYNC_CONFIRM_US       1000   /* a peak must stay the best this long */
#define ESYNC_FIRE_DELAY_US    5000   /* fire this long after the peak */
/* ESYNC_WIFI_QUIET_MS / ESYNC_WIFI_TIMEOUT_MS are gone with the suspend: the
 * radio stays up through the window, so there is no ack to drain before it
 * drops and nothing to time out and bring back. See loop(). */

/* Deferred reply. Used when the staging session is gone by the time the
 * queued command fires -- a Serial-staged command after a reset, or a TCP
 * client that dropped -- so its reply is captured here and handed over with
 * qr. The esync path no longer needs it (the radio stays up and the reply
 * goes back over the live socket), but the slot still backs that case.
 * Sized for the worst case, MAX_ADC_SAMPLES in mV: each
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
