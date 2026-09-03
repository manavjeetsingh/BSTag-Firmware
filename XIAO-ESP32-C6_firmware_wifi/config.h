#ifndef CONFIG_H
#define CONFIG_H

#include "secrets.h"

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
 * The exciter sends a framed packet, not a bare drop:
 *
 *      idle    SFD (40 ms)   guard   8 chips, 2 ms each      idle
 *      ────┐               ┌─────┐ ┌──┐    ┌──┐  ┌────┐  ┌────────
 *          └───────────────┘     └─┘  └────┘  └──┘    └──┘
 *                          ^
 *                        t_sfd
 *
 * Detection and timing are deliberately split. The SFD is a long blank --
 * a Manchester code violation, since no run inside the payload can exceed
 * one bit period -- so it cannot be mistaken for payload and marks the
 * frame start unambiguously however late a tag started listening. Its
 * rising edge is the single timing reference: it follows a deep, settled
 * blank, so it is the sharpest, highest-SNR edge in the packet.
 *
 * The payload is validation only. Both halves of every Manchester bit
 * must differ, which a fade or a stray dropout will not produce, and the
 * 4 bits it carries are an id the exciter increments per packet, so a run
 * can prove every tag locked onto the same one. The queued command then
 * fires at ESYNC_FIRE_OFFSET_US past t_sfd rather than on any payload
 * edge -- a fixed offset off a crystal costs ~1 us of drift over 24 ms,
 * far less than the spread in where each tag's decode happens to finish.
 *
 * The baseline only follows the signal while it is at rest, so a drop
 * cannot drag it down however long it lasts.
 *
 * Everything here is mirrored in BladeRFCode/manchester_sync/manual_sync.py;
 * the packet shape has to be edited in both places at once. */
#define ESYNC_BUF_LEN          10000
#define ESYNC_CHANNEL          2      /* RX channel forced on esync */
#define ESYNC_WARMUP_SAMPLES   1000   /* samples used to seed the baseline */
#define ESYNC_MIN_BASELINE_MV  2.0f   /* floor level: at or under this is a drop */
#define ESYNC_REARM_PCT        50     /* % below baseline that counts as back up.
                                        50 puts the crossing at the steepest
                                        point of the exciter ramp, where a
                                        given amplitude mismatch between tags
                                        costs the least timing error. Needs a
                                        baseline above 2*ESYNC_MIN_BASELINE_MV
                                        to stay clear of the arm floor. */
#define ESYNC_BASELINE_SHIFT   10     /* baseline IIR time constant, 1<<n samples */
#define ESYNC_WIFI_QUIET_MS    50     /* ack drain before the radio goes down */
#define ESYNC_WIFI_TIMEOUT_MS  30000  /* no packet by now: bring the radio back */

/* Packet shape. The SFD window is wide because it only has to separate a
 * 40 ms blank from anything the environment produces by accident; the
 * tags' own measurement error is a sample period, some tens of us. */
#define ESYNC_SFD_MIN_US       35000  /* accepted SFD blank, low end */
#define ESYNC_SFD_MAX_US       45000  /* accepted SFD blank, high end */
#define ESYNC_GUARD_US          4000  /* carrier between the SFD and chip 0 */
#define ESYNC_CHIP_US           2000  /* one chip; a bit is two of them */
#define ESYNC_PKT_BITS             4
#define ESYNC_PKT_CHIPS        (2 * ESYNC_PKT_BITS)
#define ESYNC_PAYLOAD_END_US   (ESYNC_GUARD_US + ESYNC_PKT_CHIPS * ESYNC_CHIP_US)

/* When the queued command fires, measured from t_sfd. Must clear
 * ESYNC_PAYLOAD_END_US so the packet is fully validated first; the slack
 * is what the decode and the dispatch have to fit into. */
#define ESYNC_FIRE_OFFSET_US   24000

/* Chips are sliced from the middle half of their window only -- the edges
 * are ramp and loop jitter. A chip that collected fewer samples than this
 * means the sample loop stalled through it, so the packet is dropped
 * rather than sliced on thin evidence. */
#define ESYNC_CHIP_MIN_SAMPLES     8

/* Require a particular id, or -1 to accept any well-formed packet and
 * report whichever id it carried. Pin it only when something else on the
 * band is also sending packets this detector would accept. */
#define ESYNC_PKT_REQUIRE_ID      (-1)

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
#define MPP_DWELL_US           1000   /* interactive mpp/mpp_<n> */
#define MPP_DWELL_QUEUED_US    3000   /* mpp fired by the esync detector */
#define MPP_MAX_PASSES         1000

#define SESSION_COUNT          (1 + MAX_TCP_CLIENTS)   /* slot 0 is Serial */

#endif /* CONFIG_H */
