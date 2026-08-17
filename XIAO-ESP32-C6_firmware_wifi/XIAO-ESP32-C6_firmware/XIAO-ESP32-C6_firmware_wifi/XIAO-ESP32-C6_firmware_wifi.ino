#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>

/* ------------------------------------------------------------------ */
/* Network credentials. Move these to a secrets.h that is gitignored.  */
/* Leave WIFI_SSID empty ("") to build with WiFi disabled at runtime.  */
/* ------------------------------------------------------------------ */
#define WIFI_SSID              "HackersParadise"
#define WIFI_PASS              "SANful786"
#define WIFI_HOSTNAME          "tagv93"
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

/* Streaming plotter (spl/epl). Emits bare numbers for Arduino Serial Plotter. */
#define PLOTTER_CHANNEL        2      /* channel forced on spl */
#define PLOTTER_PERIOD_MS      50

/* MPP sweep. */
#define MPP_DWELL_MS           1
#define MPP_MAX_PASSES         1000

#define SESSION_COUNT          (1 + MAX_TCP_CLIENTS)   /* slot 0 is Serial */

static SPIClass adc_spi(FSPI);
static uint8_t current_channel = 2;

static const uint8_t RF_SWITCH_TABLE[8][3] = {
    {0, 0, 0},
    {0, 0, 1},
    {0, 1, 0},
    {0, 1, 1},
    {1, 0, 0},
    {1, 0, 1},
    {1, 1, 0},
    {1, 1, 1},
};

static const uint8_t MPP_CHANNELS[] = {1, 3, 4, 6, 7, 8};
#define MPP_CHANNEL_COUNT (sizeof(MPP_CHANNELS) / sizeof(MPP_CHANNELS[0]))

/* Runtime state. */
static uint16_t capture_buf[CAPTURE_BUF_LEN];
static uint32_t capture_count = 0;
static bool     capture_active = false;
static bool     capture_overflow = false;

static bool     plotter_active = false;
static uint32_t plotter_last_ms = 0;
static int      plotter_session = -1;   /* which session asked for the stream */

/* One command-line assembler per transport. Slot 0 is always Serial. */
typedef struct {
    Stream *io;                 /* NULL when the slot is idle */
    char    buf[CMD_BUF_LEN];
    size_t  len;
    bool    discard;            /* set when a line exceeds CMD_BUF_LEN */
} Session;

static Session    sessions[SESSION_COUNT];
static WiFiServer tcp_server(TCP_PORT);
static WiFiClient tcp_clients[MAX_TCP_CLIENTS];
static bool       wifi_enabled = false;
static bool       wifi_up = false;
static bool       server_started = false;

/* ------------------------------------------------------------------ */
/* TX coalescing.                                                      */
/*                                                                     */
/* WiFiClient has no write buffer: every print() becomes its own       */
/* lwIP send(), and with TCP_NODELAY set that is one packet per        */
/* number. Dumping 10k samples that way is ~10k packets. This wraps    */
/* the sink and flushes in OUT_CHUNK_LEN blocks instead.               */
/* ------------------------------------------------------------------ */
class BufferedOut : public Print {
public:
    explicit BufferedOut(Print &sink) : _sink(sink), _n(0) {}
    ~BufferedOut() { done(); }

    size_t write(uint8_t c) override
    {
        if (_n == sizeof(_buf)) {
            flushChunk();
        }
        _buf[_n++] = c;
        return 1;
    }

    size_t write(const uint8_t *data, size_t len) override
    {
        size_t remaining = len;
        while (remaining > 0) {
            if (_n == sizeof(_buf)) {
                flushChunk();
            }
            size_t space = sizeof(_buf) - _n;
            size_t take  = (remaining < space) ? remaining : space;
            memcpy(_buf + _n, data, take);
            _n   += take;
            data += take;
            remaining -= take;
        }
        return len;
    }

    void done() { flushChunk(); }

private:
    void flushChunk()
    {
        if (_n > 0) {
            _sink.write(_buf, _n);
            _n = 0;
        }
    }

    Print  &_sink;
    uint8_t _buf[OUT_CHUNK_LEN];
    size_t  _n;
};

/* ------------------------------------------------------------------ */
/* Hardware                                                            */
/* ------------------------------------------------------------------ */

static void setStatusLed(bool on)
{
    digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
}

static void setUserLed(bool on)
{
    digitalWrite(PIN_USER_LED, on ? LOW : HIGH);
}

static bool switchChannel(uint8_t channel)
{
    if (channel < 1 || channel > 8) {
        return false;
    }

    const uint8_t *levels = RF_SWITCH_TABLE[channel - 1];
    digitalWrite(PIN_RF_V1, levels[0]);
    digitalWrite(PIN_RF_V2, levels[1]);
    digitalWrite(PIN_RF_V3, levels[2]);
    current_channel = channel;
    return true;
}

static uint16_t readAdcRaw(void)
{
    adc_spi.beginTransaction(SPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0));
    digitalWrite(PIN_SPI_CS, LOW);
    uint16_t raw = adc_spi.transfer16(0);
    digitalWrite(PIN_SPI_CS, HIGH);
    adc_spi.endTransaction();
    return raw;
}

static float rawToMilliVolts(uint16_t raw)
{
    return (float)raw * ADC_REF_MV / 65535.0f;
}

/* True while the RF path must not be disturbed. */
static bool pathIsBusy(void)
{
    return capture_active || plotter_active;
}

/* ------------------------------------------------------------------ */
/* Command handling (transport agnostic)                               */
/* ------------------------------------------------------------------ */

static void printHelp(Print &out)
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
    out.println("  mpp          one MPP channel sweep");
    out.println("  mpp_<n>      n MPP sweeps, max 1000");
    out.println("  net          show wifi status");
    out.println("  help         show this message");
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

static void captureReset(void)
{
    capture_count = 0;
    capture_overflow = false;
}

static void captureSample(void)
{
    if (capture_count < CAPTURE_BUF_LEN) {
        capture_buf[capture_count++] = readAdcRaw();
        if (capture_count == CAPTURE_BUF_LEN) {
            capture_overflow = true;
            capture_active = false;   /* stop cleanly when full */
        }
    }
}

static void dumpCapture(Print &out)
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

static void runMppSweep(uint16_t passes, Print &out)
{
    for (uint16_t pass = 0; pass < passes; pass++) {
        for (size_t i = 0; i < MPP_CHANNEL_COUNT; i++) {
            switchChannel(MPP_CHANNELS[i]);
            delay(MPP_DWELL_MS);
        }
    }
    out.printf("mpp: %u pass, ch:%u, ok\n", passes, current_channel);
}

static void printNetStatus(Print &out)
{
    if (!wifi_enabled) {
        out.println("{\"net\":\"disabled\"}");
        return;
    }
    if (!wifi_up) {
        out.println("{\"net\":\"down\"}");
        return;
    }
    out.printf("{\"net\":\"up\", \"ip\":\"%s\", \"port\":%u, \"rssi\":%d}\n",
               WiFi.localIP().toString().c_str(),
               (unsigned)TCP_PORT,
               (int)WiFi.RSSI());
}

static void handleCommand(char *command, Print &out, int session_idx)
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

    /* --- buffered capture --- */

    if (strcmp(command, "rdb") == 0) {
        plotter_active = false;
        plotter_session = -1;
        switchChannel(CAPTURE_CHANNEL);
        captureReset();
        capture_active = true;
        setStatusLed(false);
        out.println("rdb");
        return;
    }

    if (strcmp(command, "rds") == 0) {
        capture_active = false;
        setStatusLed(true);
        dumpCapture(out);
        captureReset();
        return;
    }

    /* --- plotter stream --- */

    if (strcmp(command, "spl") == 0) {
        capture_active = false;
        switchChannel(PLOTTER_CHANNEL);
        plotter_active = true;
        plotter_session = session_idx;                   /* stream back here */
        plotter_last_ms = millis() - PLOTTER_PERIOD_MS;  /* emit immediately */
        setStatusLed(false);
        return;
    }

    if (strcmp(command, "epl") == 0) {
        plotter_active = false;
        plotter_session = -1;
        setStatusLed(true);
        return;
    }

    /* --- MPP sweep --- */

    if (strcmp(command, "mpp") == 0 || strncmp(command, "mpp_", 4) == 0) {
        if (pathIsBusy()) {
            out.println("mpp:busy, stop capture/plotter first");
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
        runMppSweep(passes, out);
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

/* ------------------------------------------------------------------ */
/* Session plumbing                                                    */
/* ------------------------------------------------------------------ */

static void sessionReset(int idx)
{
    sessions[idx].len = 0;
    sessions[idx].discard = false;
}

static void sessionClose(int idx)
{
    sessions[idx].io = NULL;
    sessionReset(idx);
    if (plotter_session == idx) {
        plotter_active = false;
        plotter_session = -1;
        setStatusLed(true);
    }
}

/* Drain one transport without blocking; dispatch on each complete line. */
static void pollSession(int idx)
{
    Session &s = sessions[idx];
    if (s.io == NULL) {
        return;
    }

    while (s.io->available() > 0) {
        int c = s.io->read();
        if (c < 0) {
            break;
        }

        if (c == '\n' || c == '\r') {
            if (s.discard) {
                s.discard = false;
                s.len = 0;
                s.io->println("cmd:too long");
                continue;
            }
            if (s.len > 0) {
                s.buf[s.len] = '\0';
                s.len = 0;
                setUserLed(true);
                {
                    BufferedOut out(*s.io);
                    handleCommand(s.buf, out, idx);
                    out.done();
                }
                setUserLed(false);
            }
            continue;
        }

        if (s.discard) {
            continue;
        }
        if (s.len < CMD_BUF_LEN - 1) {
            s.buf[s.len++] = (char)c;
        } else {
            s.discard = true;   /* swallow the rest of the line */
        }
    }
}

static void servicePlotter(void)
{
    if (plotter_session < 0 || sessions[plotter_session].io == NULL) {
        plotter_active = false;
        plotter_session = -1;
        setStatusLed(true);
        return;
    }

    uint32_t now = millis();
    if (now - plotter_last_ms >= PLOTTER_PERIOD_MS) {
        plotter_last_ms = now;
        sessions[plotter_session].io->println(rawToMilliVolts(readAdcRaw()), 3);
    }
}

/* ------------------------------------------------------------------ */
/* WiFi                                                                */
/* ------------------------------------------------------------------ */

static void wifiStart(void)
{
    if (strlen(WIFI_SSID) == 0) {
        wifi_enabled = false;
        Serial.println("wifi:disabled, no ssid");
        return;
    }

    wifi_enabled = true;
    WiFi.persistent(false);          /* skip NVS writes on every begin() */
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(WIFI_HOSTNAME);
    WiFi.setAutoReconnect(true);
#if WIFI_LOW_LATENCY
    WiFi.setSleep(false);            /* no modem sleep: lower RTT, more mA */
#endif
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.println("wifi:connecting");
}

static void serviceWifi(void)
{
    static uint32_t last_attempt_ms = 0;

    if (!wifi_enabled) {
        return;
    }

    bool connected = (WiFi.status() == WL_CONNECTED);

    if (connected && !wifi_up) {
        wifi_up = true;
        if (!server_started) {
            tcp_server.begin();
            tcp_server.setNoDelay(true);
            server_started = true;
        }
        Serial.printf("wifi:up, ip:%s, port:%u\n",
                      WiFi.localIP().toString().c_str(), (unsigned)TCP_PORT);
    } else if (!connected && wifi_up) {
        wifi_up = false;
        Serial.println("wifi:down");
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            tcp_clients[i].stop();
            sessionClose(i + 1);
        }
    }

    if (!connected) {
        uint32_t now = millis();
        if (now - last_attempt_ms >= WIFI_RETRY_MS) {
            last_attempt_ms = now;
            WiFi.begin(WIFI_SSID, WIFI_PASS);
        }
    }
}

static void serviceTcp(void)
{
    if (!server_started) {
        return;
    }

    /* Reap dropped clients. */
    for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
        if (sessions[i + 1].io != NULL && !tcp_clients[i].connected()) {
            tcp_clients[i].stop();
            sessionClose(i + 1);
        }
    }

    /* Accept at most one new client per pass. */
    WiFiClient incoming = tcp_server.accept();
    if (!incoming) {
        return;
    }

    int slot = -1;
    for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
        if (sessions[i + 1].io == NULL) {
            slot = i;
            break;
        }
    }

    if (slot < 0) {
        incoming.println("busy:too many clients");
        incoming.stop();
        return;
    }

    tcp_clients[slot] = incoming;
    tcp_clients[slot].setNoDelay(true);
    sessions[slot + 1].io = &tcp_clients[slot];
    sessionReset(slot + 1);
    tcp_clients[slot].println("TagV93 ready");
}

/* ------------------------------------------------------------------ */

void setup(void)
{
    Serial.begin(SERIAL_BAUD);
    uint32_t serial_wait_start = millis();
    while (!Serial && millis() - serial_wait_start < 2000) {
        delay(10);
    }

    pinMode(PIN_POWER_EN, OUTPUT);
    digitalWrite(PIN_POWER_EN, POWER_EN_ACTIVE_LEVEL);
    delay(100);

    pinMode(PIN_STATUS_LED, OUTPUT);
    pinMode(PIN_USER_LED, OUTPUT);
    setStatusLed(true);
    setUserLed(false);

    pinMode(PIN_RF_V1, OUTPUT);
    pinMode(PIN_RF_V2, OUTPUT);
    pinMode(PIN_RF_V3, OUTPUT);
    switchChannel(current_channel);

    adc_spi.begin(PIN_SPI_SCLK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
    pinMode(PIN_SPI_CS, OUTPUT);
    digitalWrite(PIN_SPI_CS, HIGH);
    pinMode(PIN_SPI_MOSI, OUTPUT);
    digitalWrite(PIN_SPI_MOSI, HIGH);

    captureReset();

    for (int i = 0; i < SESSION_COUNT; i++) {
        sessions[i].io = NULL;
        sessionReset(i);
    }
    sessions[0].io = &Serial;

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

    if (capture_active) {
        captureSample();
    }

    if (plotter_active) {
        servicePlotter();
    }
}
