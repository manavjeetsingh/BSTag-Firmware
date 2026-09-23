#include "net.h"

#include "config.h"

#if NET_ENABLED

#include <WiFi.h>
#include <string.h>

#include "session.h"

static WiFiServer tcp_server(TCP_PORT);
static WiFiClient tcp_clients[MAX_TCP_CLIENTS];
static bool       wifi_enabled = false;
static bool       wifi_up = false;
static bool       server_started = false;
static bool       wifi_suspended = false;
static uint32_t   last_attempt_ms = 0;   /* last WiFi.begin(), shared with the retry timer */

void wifiStart(void)
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
    last_attempt_ms = millis();
    Serial.println("wifi:connecting");
}

void wifiSuspend(void)
{
    if (!wifi_enabled || wifi_suspended) {
        return;
    }

    for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
        tcp_clients[i].stop();
        sessionClose(i + 1);
    }
    if (server_started) {
        tcp_server.end();
        server_started = false;
    }

    WiFi.disconnect(true);       /* true = power the radio down */
    WiFi.mode(WIFI_OFF);

    wifi_up = false;
    wifi_suspended = true;
}

void wifiResume(void)
{
    if (!wifi_enabled || !wifi_suspended) {
        return;
    }

    wifi_suspended = false;
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);   /* serviceWifi() restarts the server */
    last_attempt_ms = millis();         /* don't let the retry timer double this up */
}

bool wifiSuspended(void)
{
    return wifi_suspended;
}

void serviceWifi(void)
{
    if (!wifi_enabled || wifi_suspended) {
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
        // Serial.printf("wifi:up, ip:%s, port:%u\n",
                    //   WiFi.localIP().toString().c_str(), (unsigned)TCP_PORT);
    } else if (!connected && wifi_up) {
        wifi_up = false;
        // Serial.println("wifi:down");
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            tcp_clients[i].stop();
            sessionClose(i + 1);
        }
    }

    /* WiFi.setAutoReconnect(true) (wifiStart()) already retries internally
     * on disconnect -- calling begin() again here would race that in-flight
     * attempt and trip "sta is connecting, cannot set config" every tick. */
}

void serviceTcp(void)
{
    if (wifi_suspended || !server_started) {
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

void printNetStatus(Print &out)
{
    if (!wifi_enabled) {
        out.println("{\"net\":\"disabled\"}");
        return;
    }
    if (wifi_suspended) {
        out.println("{\"net\":\"suspended\"}");
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

#else /* !NET_ENABLED */

/* WiFi/TCP compiled out. Every entry point below stays, as a no-op or a
 * fixed status, so callers in the .ino and commands.cpp need no #ifdef of
 * their own -- in particular commands.cpp's wifi_off/wifi_on still call
 * wifiSuspend()/wifiResume() unconditionally. */

void wifiStart(void)
{
    Serial.println("wifi:disabled, not compiled in");
}

void wifiSuspend(void) {}
void wifiResume(void) {}

bool wifiSuspended(void)
{
    return false;
}

void serviceWifi(void) {}
void serviceTcp(void) {}

void printNetStatus(Print &out)
{
    out.println("{\"net\":\"disabled\"}");
}

#endif /* NET_ENABLED */
