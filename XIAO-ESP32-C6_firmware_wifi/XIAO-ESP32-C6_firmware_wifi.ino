/* ------------------------------------------------------------------ */
/* TagV93 firmware for the XIAO ESP32-C6.                              */
/*                                                                     */
/*   config.h        pin map and tuning constants                      */
/*   secrets.h       WiFi credentials (gitignored)                     */
/*   hardware.*      power rail, LEDs, RF switch, ADC over SPI         */
/*   acquisition.*   buffered capture, plotter stream, MPP sweep       */
/*   esync.*         exciter sync listener, ASK preamble correlator    */
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

    /* The esync window used to run with the radio off (wifiSuspend() here,
     * wifiResume() after the capture), because the WiFi driver and lwIP
     * tasks outrank loopTask and their preemption lands straight on the
     * sample loop as sync error between tags.
     *
     * It is not worth what it cost: every round took the radio down and
     * re-associated it, and with two tags doing that per round the link gave
     * out after ~15 rounds -- both tags stopped answering their TCP server at
     * the same moment, mid-run. The radio stays up through the window now, so
     * the jitter is back: watch `stalls` and `rho` in esyncr, and the dwell
     * `score` out of segmentation, since the queued capture is jittered too.
     *
     * With the session alive the fired command's reply goes straight back
     * over it (see runQueuedCommand()), so nothing lands in the deferred
     * buffer and there is no reconnect to wait on. wifiSuspend()/Resume()
     * stay for the wifi_off/wifi_on commands. */
    if (esyncActive()) {
        esyncSample();
        esyncListening();
    }
}
