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
#define POWER_EN_ACTIVE_LEVEL  HIGH

#define SERIAL_BAUD            921600UL
#define SPI_CLOCK_HZ           40000000UL
#define MAX_ADC_SAMPLES        1000
#define ADC_REF_MV             5000.0f
#define CMD_BUF_LEN            32     /* max command length incl. terminator */
#define OUT_CHUNK_LEN          1024   /* TX coalescing buffer, see BufferedOut */

/* Buffered capture (rdb/rds). Stored as raw codes: 2 bytes/sample. */
#define CAPTURE_BUF_LEN        10000
#define CAPTURE_CHANNEL        2      /* channel forced on rdb */

/* Exciter sync (esync). Moving window of raw codes: 2 bytes/sample.
 *
 * The signal must fall to the floor, ESYNC_MIN_BASELINE_MV or below, to
 * arm the detector; a rising edge is then reported once it climbs back
 * to within ESYNC_REARM_PCT of the tracked idle level. How often the
 * drops happen never enters into it. The baseline only follows the
 * signal while it is at rest, so a drop cannot drag it down however long
 * it lasts. */
#define ESYNC_BUF_LEN          10000
#define ESYNC_CHANNEL          2      /* RX channel forced on esync */
#define ESYNC_WARMUP_SAMPLES   1000   /* samples used to seed the baseline */
#define ESYNC_MIN_BASELINE_MV  2.0f   /* floor level: at or under this is a drop */
#define ESYNC_REARM_PCT        25     /* % below baseline that counts as back up */
#define ESYNC_BASELINE_SHIFT   10     /* baseline IIR time constant, 1<<n samples */

/* Streaming plotter (spl/epl). Emits bare numbers for Arduino Serial Plotter. */
#define PLOTTER_CHANNEL        2      /* channel forced on spl */
#define PLOTTER_PERIOD_MS      50

/* MPP sweep. */
#define MPP_DWELL_MS           1
#define MPP_MAX_PASSES         1000

#define SESSION_COUNT          (1 + MAX_TCP_CLIENTS)   /* slot 0 is Serial */

#endif /* CONFIG_H */
