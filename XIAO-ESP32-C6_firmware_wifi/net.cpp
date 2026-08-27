#include "net.h"

#include <WiFi.h>
#include <string.h>

#include "config.h"
#include "session.h"

static WiFiServer tcp_server(TCP_PORT);
static WiFiClient tcp_clients[MAX_TCP_CLIENTS];
static bool       wifi_enabled = false;
static bool       wifi_up = false;
static bool       server_started = false;
static bool       wifi_suspended = false;

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
}

bool wifiSuspended(void)
{
    return wifi_suspended;
}

void serviceWifi(void)
{
    static uint32_t last_attempt_ms = 0;

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

    if (!connected) {
        uint32_t now = millis();
        if (now - last_attempt_ms >= WIFI_RETRY_MS) {
            last_attempt_ms = now;
            WiFi.begin(WIFI_SSID, WIFI_PASS);
        }
    }
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
