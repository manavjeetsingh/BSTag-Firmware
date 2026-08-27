/* ------------------------------------------------------------------ */
/* TagV93 firmware for the XIAO ESP32-C6.                              */
/*                                                                     */
/*   config.h        pin map and tuning constants                      */
/*   secrets.h       WiFi credentials (gitignored)                     */
/*   hardware.*      power rail, LEDs, RF switch, ADC over SPI         */
/*   acquisition.*   buffered capture, plotter stream, MPP sweep       */
/*   esync.*         exciter sync listener and edge detector           */
/*   commands.*      the text protocol, transport agnostic             */
/*   session.*       per-transport line assembly and dispatch          */
/*   net.*           WiFi link and the TCP command server              */
/*   buffered_out.h  TX coalescing wrapper around a Print sink         */
/* ------------------------------------------------------------------ */

#include <Arduino.h>

#include "acquisition.h"
#include "commands.h"
#include "config.h"
#include "esync.h"
#include "hardware.h"
#include "net.h"
#include "session.h"

void setup(void)
{
    Serial.begin(SERIAL_BAUD);
    uint32_t serial_wait_start = millis();
    while (!Serial && millis() - serial_wait_start < 2000) {
        delay(10);
    }

    hardwareInit();
    captureReset();
    sessionsInit();
    wifiStart();

    Serial.println();
    Serial.println("TagV93 ready");
    printHelp(Serial);
}

void loop(void)
{
    serviceWifi();
    serviceTcp();

    for (int i = 0; i < SESSION_COUNT; i++) {
        pollSession(i);
    }

    if (captureActive()) {
        captureSample();
    }

    if (plotterActive()) {
        servicePlotter();
    }

    /* The esync window runs with the radio off. The WiFi driver and lwIP
     * tasks outrank loopTask, so their preemption lands directly on the
     * sample loop and shows up as milliseconds of sync error between
     * tags. Suspending for the window removes it.
     *
     * Suspend happens here, not in esyncListen(), so the "esync:listening"
     * ack has already been flushed by pollSession() above. Resume waits
     * for the capture to finish as well as the edge, since a queued rdb
     * keeps sampling in loop() long after runQueuedCommand() returns. */
    static uint32_t esync_quiet_since_ms = 0;
    static bool     esync_quiet_done = false;   /* done for this arming */

    if (esyncActive()) {
        /* Gated on our own flag, not on wifiSuspended(): with WiFi built
         * off (empty WIFI_SSID) wifiSuspend() is a no-op and never reports
         * suspended, which would run the quiet delay on every pass and
         * throttle the detector to 20 Hz. */
        if (!esync_quiet_done) {
            delay(ESYNC_WIFI_QUIET_MS);   /* let the ack get out first */
            wifiSuspend();
            esync_quiet_done = true;
            esync_quiet_since_ms = millis();
        } else if (wifiSuspended() &&
                   millis() - esync_quiet_since_ms >= ESYNC_WIFI_TIMEOUT_MS) {
            /* The exciter never fired. Holding the radio down any longer
             * locks the host out over TCP with no way back except Serial,
             * so bring it up and leave it up. Listening continues, but an
             * edge caught from here on is sampled with WiFi running, so
             * its sync is degraded -- this notice marks the run. */
            wifiResume();
            Serial.println("{\"info\":\"esync\",\"wifi\":\"resumed\","
                           "\"reason\":\"timeout\",\"sync\":\"degraded\"}");
        }
        esyncSample();
        esyncListening();
    } else {
        esync_quiet_done = false;         /* next esync arms cleanly */
        if (wifiSuspended() && !captureActive()) {
            wifiResume();
        }
    }
}
