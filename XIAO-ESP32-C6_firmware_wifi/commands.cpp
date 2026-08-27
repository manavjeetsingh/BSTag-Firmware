#include "commands.h"

#include <WiFi.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "acquisition.h"
#include "buffered_out.h"
#include "config.h"
#include "esync.h"
#include "hardware.h"
#include "net.h"
#include "session.h"

/* Deferred command slot. */
static char queued_cmd[CMD_BUF_LEN] = {0};
static bool queued_valid = false;
static int  queued_session = 0;

/* Deferred reply, captured when the staging session is gone. */
static char   reply_buf[QUEUED_REPLY_BUF_LEN];
static size_t reply_len = 0;
static bool   reply_valid = false;
static bool   reply_overflow = false;
static char   reply_for[CMD_BUF_LEN] = {0};

/* Print sink over reply_buf. Truncates rather than growing; the overflow
 * flag turns a silently-cut JSON blob into an explicit error on qr. */
class DeferredOut : public Print {
public:
    size_t write(uint8_t c) override
    {
        if (reply_len >= sizeof(reply_buf)) {
            reply_overflow = true;
            return 0;
        }
        reply_buf[reply_len++] = (char)c;
        return 1;
    }

    size_t write(const uint8_t *data, size_t len) override
    {
        size_t space = sizeof(reply_buf) - reply_len;
        size_t take  = (len < space) ? len : space;
        if (take < len) {
            reply_overflow = true;
        }
        memcpy(reply_buf + reply_len, data, take);
        reply_len += take;
        return len;   /* claim it all; the flag carries the loss */
    }
};

void clearQueuedReply(void)
{
    reply_len = 0;
    reply_valid = false;
    reply_overflow = false;
    reply_for[0] = '\0';
}

void dumpQueuedReply(Print &out)
{
    if (!reply_valid) {
        out.println("{\"info\":\"qr\",\"pending\":0}");
        return;
    }
    if (reply_overflow) {
        out.printf("{\"info\":\"qr\",\"pending\":1,\"err\":\"overflow\","
                   "\"cmd\":\"%s\"}\n", reply_for);
        return;
    }
    out.write((const uint8_t *)reply_buf, reply_len);
}

void printHelp(Print &out)
{
    out.println("Commands:");
    out.println("  ch_<1-8>     switch RF/tag channel");
    out.println("  adc          read one ADC sample");
    out.println("  adc_<count>  read ADC samples, max 1000");
    out.println("  adcraw       read one raw ADC code");
    out.println("  adcraw_<n>   read raw ADC codes, max 1000");
    out.println("  rdb          begin buffered capture on ch 2");
    out.println("  rds          stop capture and dump buffer");
    out.println("  spl          start plotter stream on ch 2");
    out.println("  epl          stop plotter stream");
    out.println("  esync        listen for exciter sync");
    out.println("  esyncs       stop listening for exciter sync");
    out.println("  esyncr       report the last exciter sync edge");
    out.println("  qr           reply the queued command left behind");
    out.println("  q_<cmd>      queue <cmd>, run it on the next esync edge");
    out.println("  q            show the queued command");
    out.println("  qc           clear the queued command");
    out.println("  mpp          one MPP channel sweep");
    out.println("  mpp_<n>      n MPP sweeps, max 1000");
    out.println("  net          show wifi status");
    out.println("  help         show this message");
}

void queueCommand(const char *cmd, int session_idx)
{
    strncpy(queued_cmd, cmd, sizeof(queued_cmd) - 1);
    queued_cmd[sizeof(queued_cmd) - 1] = '\0';
    queued_session = session_idx;
    queued_valid = true;
}

bool hasQueuedCommand(void)
{
    return queued_valid;
}

const char *queuedCommand(void)
{
    return queued_valid ? queued_cmd : "";
}

void clearQueuedCommand(void)
{
    queued_cmd[0] = '\0';
    queued_valid = false;
}

void runQueuedCommand(void)
{
    if (!queued_valid) {
        return;
    }
    /* Take a mutable copy and empty the slot before dispatching:
     * handleCommand() trims in place, and running against a clear queue
     * lets the command stage the next one itself. */
    char cmd[CMD_BUF_LEN];
    strncpy(cmd, queued_cmd, sizeof(cmd) - 1);
    cmd[sizeof(cmd) - 1] = '\0';
    int idx = queued_session;
    clearQueuedCommand();

    Stream *io = NULL;
    if (idx >= 0 && idx < SESSION_COUNT) {
        io = sessions[idx].io;
    }

    if (io != NULL) {
        BufferedOut out(*(Print *)io);
        handleCommand(cmd, out, idx, true);
        out.done();
        return;
    }

    /* No session left -- almost always because the esync window took the
     * radio down. Capture the reply so qr can hand it over once the host
     * is back, instead of writing it into a closed socket. */
    clearQueuedReply();
    strncpy(reply_for, cmd, sizeof(reply_for) - 1);
    reply_for[sizeof(reply_for) - 1] = '\0';

    DeferredOut dout;
    handleCommand(cmd, dout, idx, true);
    reply_valid = true;
}

/* Strip leading/trailing whitespace (incl. CR from CRLF line endings). */
static void trimInPlace(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && isspace((unsigned char)s[n - 1])) {
        s[--n] = '\0';
    }
    char *start = s;
    while (*start != '\0' && isspace((unsigned char)*start)) {
        start++;
    }
    if (start != s) {
        memmove(s, start, strlen(start) + 1);
    }
}

void handleCommand(char *command, Print &out, int session_idx, bool from_queue)
{
    trimInPlace(command);
    if (command[0] == '\0') {
        return;
    }

    if (strcmp(command, "help") == 0 || strcmp(command, "?") == 0) {
        printHelp(out);
        return;
    }

    if (strcmp(command, "net") == 0) {
        printNetStatus(out);
        return;
    }
    if (strcmp(command, "mac") == 0) {
        out.printf("{\"mac\":\"%s\"}\n", WiFi.macAddress().c_str());
        return;
    }

    if (strncmp(command, "ch_", 3) == 0) {
        if (pathIsBusy()) {
            out.println("ch:busy, stop capture/plotter first");
            return;
        }
        uint8_t channel = (uint8_t)atoi(command + 3);
        if (switchChannel(channel)) {
            out.printf("ch: %u, ok\r\n", current_channel);
        } else {
            out.println("ch:invalid, use ch_1 ... ch_8");
        }
        return;
    }

    /* --- get ready for exciter sync ---*/

    if (strcmp(command, "esync") == 0) {
        esyncListen();
        out.println("esync:listening");
        return;
    }

    if (strcmp(command, "esyncs") == 0) {
        esyncStop();
        out.println("esync:stopped");
        return;
    }

    /* --- deferred command --- */

    if (strncmp(command, "q_", 2) == 0) {
        const char *staged = command + 2;
        if (staged[0] == '\0') {
            out.println("q:empty, use q_<command>");
            return;
        }
        queueCommand(staged, session_idx);
        out.printf("q:queued, %s\n", queuedCommand());
        return;
    }

    if (strcmp(command, "q") == 0) {
        if (hasQueuedCommand()) {
            out.printf("q:queued, %s\n", queuedCommand());
        } else {
            out.println("q:empty");
        }
        return;
    }

    if (strcmp(command, "qc") == 0) {
        clearQueuedCommand();
        out.println("q:cleared");
        return;
    }

    /* Reply the queued command produced while the radio was down, and the
     * edge that triggered it. Both survive until the next esync arms. */
    if (strcmp(command, "qr") == 0) {
        dumpQueuedReply(out);
        return;
    }

    if (strcmp(command, "esyncr") == 0) {
        esyncReport(out);
        return;
    }

    /* --- buffered capture --- */

    if (strcmp(command, "rdb") == 0) {
        captureStart();
        out.println("rdb");
        return;
    }

    if (strcmp(command, "rds") == 0) {
        captureStop();
        dumpCapture(out);
        captureReset();
        return;
    }

    /* --- plotter stream --- */

    if (strcmp(command, "spl") == 0) {
        plotterStart(session_idx);
        return;
    }

    if (strcmp(command, "epl") == 0) {
        plotterStop();
        return;
    }

    /* --- MPP sweep --- */

    if (strcmp(command, "mpp") == 0 || strncmp(command, "mpp_", 4) == 0) {
        if (pathIsBusy()) {
            out.println("{\"info\":\"mpp\",\"ok\":0,\"err\":\"busy, stop capture/plotter first\"}");
            return;
        }
        uint16_t passes = 1;
        if (command[3] == '_') {
            passes = (uint16_t)atoi(command + 4);
            if (passes == 0) {
                passes = 1;
            }
            if (passes > MPP_MAX_PASSES) {
                passes = MPP_MAX_PASSES;
            }
        }

        if (from_queue) {
            delay(5);
        }

        runMppSweep(passes, from_queue ? MPP_DWELL_QUEUED_US : MPP_DWELL_US, out);
        return;
    }

    /* --- one-shot / burst ADC reads --- */

    bool is_adcraw_n = strncmp(command, "adcraw_", 7) == 0;
    bool is_adcraw   = strcmp(command, "adcraw") == 0;
    bool is_adc_n    = strncmp(command, "adc_", 4) == 0;
    bool is_adc      = strcmp(command, "adc") == 0;

    if (is_adc || is_adc_n || is_adcraw || is_adcraw_n) {
        bool raw_output = is_adcraw || is_adcraw_n;
        uint16_t count = 1;

        if (is_adcraw_n) {
            count = (uint16_t)atoi(command + 7);
        } else if (is_adc_n) {
            count = (uint16_t)atoi(command + 4);
        }
        if (is_adcraw_n || is_adc_n) {
            if (count == 0) {
                count = 1;
            }
            if (count > MAX_ADC_SAMPLES) {
                count = MAX_ADC_SAMPLES;
            }
        }

        out.print("{\"info\":\"adc\",\"ch\":");
        out.print(current_channel);
        out.print(",\"unit\":\"");
        out.print(raw_output ? "raw" : "mV");
        out.print("\",\"data\":\"");
        for (uint16_t i = 0; i < count; i++) {
            uint16_t raw = readAdcRaw();
            if (raw_output) {
                out.print(raw);
            } else {
                out.print(rawToMilliVolts(raw), 3);
            }
            if (i + 1 < count) {
                out.print(",");
            }
        }
        out.println("\"}");
        return;
    }

    out.print("cmd:not found, ");
    out.println(command);
}
