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

    if (esyncActive()) {
        esyncSample();
        esyncListening();
    }
}
