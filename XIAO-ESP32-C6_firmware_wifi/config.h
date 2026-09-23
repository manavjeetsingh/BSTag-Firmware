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
/* How long serviceWifi() lets the link stay down before it forces a fresh
 * WiFi.begin(). The Arduino core retries on its own after a disconnect, but
 * only for the reasons in its _is_staReconnectableReason() -- an AUTH_FAIL
 * after the first attempt, or an ASSOC_LEAVE, leaves the STA down with
 * nothing left to bring it back, and nothing else in the firmware calls
 * begin() again. Without this the tag sits there with no IP and no TCP
 * server until it is power cycled. The "net" command counts them as
 * "retries". */
#define WIFI_RETRY_MS          5000
#define WIFI_LOW_LATENCY       1      /* 1 = disable modem sleep (more power) */

/* Rejoin after an esync suspend using the channel, BSSID and address the
 * link had before it, instead of scanning every channel and asking DHCP
 * for a lease we already hold. A wireless run suspends and resumes once
 * per MPP round, so the cold join is charged to every reading. Set to 0 to
 * always rejoin the long way (see wifiResume() in net.cpp).
 *
 * WIFI_FAST_RESUME_MS is how long the fast path gets before net.cpp gives
 * up on it and falls back -- long enough for a retry or two of a normal
 * association, short enough to beat the cold join it replaces.
 *
 * The one thing to know: reusing the address means the tag stops renewing
 * its DHCP lease for as long as a run keeps resuming this way. That is
 * what the lab wants (ip-mac-mapping.json already assumes the addresses
 * hold still for the run) but it does assume nothing else is handed the
 * same address meanwhile. A conflict shows up as the fast path failing to
 * associate, which falls back and logs "resume":"slow" rather than
 * silently misbehaving. */
#define WIFI_FAST_RESUME       1
#define WIFI_FAST_RESUME_MS    1500

/* The same trick applied to the boot join. WiFi.persistent(false) in
 * wifiStart() keeps the driver out of NVS on every begin(), which also
 * means nothing about the last AP survives a reset: every boot re-scans all
 * of 2.4 GHz before it can even authenticate, and the TCP server does not
 * open until that has finished and DHCP has answered. (Dropping
 * persistent(false) would not fix it -- the core memsets the channel and
 * BSSID out of wifi_config_t on every begin() regardless, so the driver's
 * own stored hint is overwritten before it can be used.) The hint is kept
 * here instead: net.cpp saves the BSSID and channel it associated on, once
 * per AP rather than once per join, and starts the next boot on that
 * channel.
 *
 * Unlike a resume this does NOT reuse the address -- a lease is fair to
 * assume across the milliseconds of a suspend, not across however long the
 * tag was powered off -- so the boot still goes through DHCP and
 * WIFI_FAST_JOIN_MS has to leave room for it. Keep it under WIFI_RETRY_MS
 * so the fallback gets its turn before the retry timer does.
 *
 * Fails soft the same way: an AP that moved channel costs one slow join and
 * a "fast":"fallback" notice, then the hint is dropped and relearned. */
#define WIFI_FAST_JOIN         1
#define WIFI_FAST_JOIN_MS      4000

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

/* Buffered capture (rdb/rds). Stored as raw codes: 2 bytes/sample.
 *
 * Sized against what the capture is for, an esync-fired MPP sweep: nine
 * dwells of MPP_DWELL_QUEUED_US at the ~66.7 kSa/s sample loop is 1800
 * samples (27 ms), of which mpp_segment.py measures the last six. It does
 * not start at sample 0 -- the rx tag begins capturing at its own fire,
 * which leads the tx tag's sweep by whatever the two detectors disagree
 * by. Over a 2-tag wireless run that put the sweep's start at samples
 * 807..1119 and its end at 1971..2283, so the whole thing was always over
 * inside the first quarter of a 10000-sample buffer. 3000 covers the worst
 * of that with ~700 samples to spare; re-measure with fit_sweep_grid()
 * before trimming further, and raise it if the tags' fire offsets diverge
 * (compare t_us across tags).
 *
 * Not free to oversize. loop() will not let wifiResume() run while the
 * capture is still going (see the .ino), so every sample past the sweep is
 * dead time charged to every round of the run, and it is then printed as
 * mV over TCP and again into the run's CSV. */
#define CAPTURE_BUF_LEN        3000
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

/* Slotted schedule (the sq commands, sgo, sqr): the "multiple" collection mode.
 *
 * Instead of one transmitter per round, every tag is loaded with an ordered
 * program of equal-length slots and they all start it on the same trigger --
 * the exciter's ASK preamble over the air, or sgo over Serial. With three
 * tags the programs are
 *
 *      tag1: [ mpp    , listen , listen ]
 *      tag2: [ listen , mpp    , listen ]
 *      tag3: [ listen , listen , mpp    ]
 *
 * so one round measures every direction at once. A slot that finishes early
 * is padded to SCHED_SLOT_US, which is what keeps the tags in step: the
 * boundaries are absolute offsets from the trigger, not a running total of
 * however long each command happened to take. */
#define SCHED_MAX_SLOTS        25     /* one per tag; ~16 B of metadata each */
#define SCHED_SLOT_US          50000  /* per slot; must exceed the MPP sweep */
#define SCHED_LISTEN_SAMPLES   2400   /* per listen slot, unless sqn_<n> lowers it */

/* Raw codes for every listen slot of a round, 2 B each. This is the one big
 * allocation in the firmware, so it is a budget rather than
 * SCHED_MAX_SLOTS * SCHED_LISTEN_SAMPLES: 25 tags at full length would be
 * 115 KB, and the free DRAM after globals is the FreeRTOS heap that WiFi and
 * lwIP allocate from (~90-110 KB of it in use once associated). 96 KB here
 * leaves ~65 KB spare. The host divides it with sqn_<n>, so small runs get
 * long traces and only a 25-tag run is squeezed; schedReport() prints the
 * free heap so the margin is visible rather than a crash. */
#define SCHED_POOL_SAMPLES     48000

/* Channel order for the scheduled sweep. Shorter than runMppSweep()'s table:
 * the leading ch1 dwells there exist to let the rectifier settle after the
 * ch2->ch1 switch, and in a slotted round that settling happens for free in
 * the previous slot's padding (see schedRun()). One ch1 pad dwell is kept
 * because mpp_segment.fit_sweep_grid() pins the grid phase on a flat
 * same-channel boundary immediately before the measured window.
 *
 * 8 dwells * MPP_DWELL_QUEUED_US = 24 ms, against 27 ms for the interactive
 * table. Two things set that length, and both were measured rather than
 * guessed:
 *
 *   - Without the pre-switch, cutting to one leading ch1 puts ch1 out by a
 *     median 1.8 mV and up to 24 mV, against a 2.5 mV median gap between
 *     adjacent channels (196 traces). The pre-switch removes that, so the
 *     leading dwells are no longer paying for settling.
 *   - What they still pay for is fit_sweep_grid's margin. It needs a whole
 *     same-channel dwell before the measured window, so the pad has to
 *     survive the skew between the tags' triggers. With ONE pad dwell and
 *     the transmitter firing 300 us early, segmentation broke on 75% of
 *     traces with errors up to 80 mV; with TWO, every trace segmented
 *     exactly across -600..+600 us of skew.
 *
 * So two leading ch1 dwells, not one. Shortening this further needs the
 * transmitter to delay its sweep inside the slot, to make the skew
 * one-sided -- which is a timing dependency this does not need. */
#define MPP_CHANNELS_SCHED     { 1, 1, 1, 3, 4, 6, 7, 8 }

/* Time on ch1 before slot 0's sweep, for the one tag that transmits first
 * and so has no previous slot to settle in. ~3 tau of the rectifier. Paid
 * once per round, by every tag, so the slot grid stays common. */
#define SCHED_PREROLL_US       9000

#if NET_ENABLED
#define SESSION_COUNT          (1 + MAX_TCP_CLIENTS)   /* slot 0 is Serial */
#else
#define SESSION_COUNT          1                       /* Serial only */
#endif

#endif /* CONFIG_H */
