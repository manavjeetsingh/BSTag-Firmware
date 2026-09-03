#include "acquisition.h"

#include "config.h"
#include "hardware.h"
#include "session.h"

static const uint8_t MPP_CHANNELS[] = {1, 1, 1, 1,  3, 4, 6, 7, 8};
#define MPP_CHANNEL_COUNT (sizeof(MPP_CHANNELS) / sizeof(MPP_CHANNELS[0]))

static uint16_t capture_buf[CAPTURE_BUF_LEN];
static uint32_t capture_count = 0;
static bool     capture_active = false;
static bool     capture_overflow = false;

static bool     plotter_active = false;
static uint32_t plotter_last_ms = 0;
static int      plotter_session = -1;   /* which session asked for the stream */

bool pathIsBusy(void)
{
    return capture_active || plotter_active;
}

/* ------------------------------------------------------------------ */
/* Buffered capture                                                    */
/* ------------------------------------------------------------------ */

bool captureActive(void)
{
    return capture_active;
}

void captureReset(void)
{
    capture_count = 0;
    capture_overflow = false;
}

void captureStart(void)
{
    plotter_active = false;
    plotter_session = -1;
    switchChannel(CAPTURE_CHANNEL);
    captureReset();
    capture_active = true;
    setStatusLed(false);
}

void captureStop(void)
{
    capture_active = false;
    setStatusLed(true);
}

void captureSample(void)
{
    if (capture_count < CAPTURE_BUF_LEN) {
        capture_buf[capture_count++] = readAdcRaw();
        if (capture_count == CAPTURE_BUF_LEN) {
            capture_overflow = true;
            capture_active = false;   /* stop cleanly when full */
        }
    }
}

void dumpCapture(Print &out)
{
    out.print("{\"info\":\"buf\",\"ch\":");
    out.print(current_channel);
    out.print(",\"unit\":\"mV\",\"count\":");
    out.print(capture_count);
    out.print(",\"full\":");
    out.print(capture_overflow ? 1 : 0);
    out.print(",\"data\":\"");
    for (uint32_t i = 0; i < capture_count; i++) {
        out.print(rawToMilliVolts(capture_buf[i]), 3);
        if (i + 1 < capture_count) {
            out.print(",");
        }
    }
    out.println("\"}");
}

/* ------------------------------------------------------------------ */
/* Plotter stream                                                      */
/* ------------------------------------------------------------------ */

bool plotterActive(void)
{
    return plotter_active;
}

void plotterStart(int session_idx)
{
    capture_active = false;
    switchChannel(PLOTTER_CHANNEL);
    plotter_active = true;
    plotter_session = session_idx;                   /* stream back here */
    plotter_last_ms = millis() - PLOTTER_PERIOD_MS;  /* emit immediately */
    setStatusLed(false);
}

void plotterStop(void)
{
    plotter_active = false;
    plotter_session = -1;
    setStatusLed(true);
}

void plotterSessionClosed(int session_idx)
{
    if (plotter_session == session_idx) {
        plotterStop();
    }
}

void servicePlotter(void)
{
    if (plotter_session < 0 || sessions[plotter_session].io == NULL) {
        plotterStop();
        return;
    }

    uint32_t now = millis();
    if (now - plotter_last_ms >= PLOTTER_PERIOD_MS) {
        plotter_last_ms = now;
        Print &io = *sessions[plotter_session].io;
        io.print(rawToMilliVolts(readAdcRaw()), 3);
        io.print(",");
        io.println(readD0() ? 1 : 0);
    }
}

/* ------------------------------------------------------------------ */
/* MPP sweep                                                           */
/* ------------------------------------------------------------------ */

/* Whole milliseconds go through delay() so the sweep keeps yielding and
 * WiFi/TCP stay serviced; only the sub-millisecond remainder is spun. */
static void mppDwell(uint32_t us)
{
    if (us >= 1000UL) {
        delay(us / 1000UL);
    }
    uint32_t rem = us % 1000UL;
    if (rem > 0) {
        delayMicroseconds(rem);
    }
}

void runMppSweep(uint16_t passes, uint32_t dwell_us, Print &out)
{
    for (uint16_t pass = 0; pass < passes; pass++) {
        for (size_t i = 0; i < MPP_CHANNEL_COUNT; i++) {
            switchChannel(MPP_CHANNELS[i]);
            mppDwell(dwell_us);
        }
    }
    out.printf("{\"info\":\"mpp\",\"ch\":%u,\"passes\":%u,\"ok\":1}\n",
               current_channel, passes);
}
