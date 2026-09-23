#include "net.h"

#include "config.h"

#if NET_ENABLED

#include <WiFi.h>
#include <string.h>
#if WIFI_FAST_JOIN
#include <Preferences.h>
#endif

#include "session.h"

static WiFiServer tcp_server(TCP_PORT);
static WiFiClient tcp_clients[MAX_TCP_CLIENTS];
static bool       wifi_enabled = false;
static bool       wifi_up = false;
static bool       server_started = false;
static bool       wifi_suspended = false;
static uint32_t   last_attempt_ms = 0;   /* last WiFi.begin(), drives the retry timer */
static uint16_t   retry_count = 0;       /* forced begin()s since boot, reported by "net" */
static bool       ever_up = false;       /* the link has come up at least once this boot */
static bool       fast_pending = false;  /* a hinted association is in flight */

/* Both fast paths -- the resume after a suspend and the join after a boot --
 * want the same cached association and the same "give it a deadline, then
 * fall back" handling in serviceWifi(), so they share one implementation. */
#if WIFI_FAST_RESUME || WIFI_FAST_JOIN
#define WIFI_FAST_PATH 1
#else
#define WIFI_FAST_PATH 0
#endif

#if WIFI_FAST_PATH
/* The link as it stood before the last suspend, or as the last boot left it
 * in NVS, so a join can skip the parts of a cold one that exist only to
 * discover what we already know: the scan across every channel (we have the
 * AP's channel and BSSID) and, on a resume, the DHCP exchange (we still
 * hold the lease). Together those are the bulk of a join, and an esync run
 * pays a join on every single round.
 *
 * Only ever an optimisation: serviceWifi() below gives the fast path a
 * deadline and then falls back to the cold one, so an AP that moved channel
 * or a lease that went elsewhere costs one slow join and a "fast":"fallback"
 * notice on Serial, not a tag that never comes back. */
static bool       fast_valid = false;    /* fast_bssid/fast_channel are usable */
static bool       fast_have_ip = false;  /* ...and the address below with them */
static uint32_t   fast_deadline_ms = 0;  /* how long this attempt gets */
static uint8_t    fast_bssid[6];
static int32_t    fast_channel = 0;
static IPAddress  fast_ip, fast_gw, fast_mask, fast_dns;
static bool       static_ip_set = false; /* WiFi.config() is pinning an address */

static void clearStaticIp(void)
{
    if (!static_ip_set) {
        return;
    }
    /* 0.0.0.0 as the local address is how the ESP32 WiFi class is told to
     * go back to DHCP; without this a failed fast resume would retry the
     * cold path still pinned to a lease that may be the reason it failed. */
    WiFi.config(IPAddress((uint32_t)0), IPAddress((uint32_t)0),
                IPAddress((uint32_t)0));
    static_ip_set = false;
}

/* Associate straight to the cached AP, and reuse the cached address too
 * when we have one. Caller picks the deadline: a resume keeps its lease so
 * it is done once it has associated, a boot join still has DHCP to sit
 * through. */
static void beginFast(uint32_t deadline_ms)
{
    if (fast_have_ip) {
        WiFi.config(fast_ip, fast_gw, fast_mask, fast_dns);
        static_ip_set = true;
    }
    WiFi.begin(WIFI_SSID, WIFI_PASS, fast_channel, fast_bssid);
    fast_pending = true;
    fast_deadline_ms = deadline_ms;
    last_attempt_ms = millis();
}
#else
static void clearStaticIp(void) {}
#endif /* WIFI_FAST_PATH */

#if WIFI_FAST_JOIN
/* The AP the tag last associated with, kept in NVS so the next boot can
 * start on its channel instead of scanning all of 2.4 GHz for it.
 *
 * Written once per AP rather than once per join, which is what keeps it
 * from quietly undoing WiFi.persistent(false) below -- that is there to
 * keep the driver out of NVS on every begin(), and this touches it only
 * when the tag actually moves to a different AP or channel. */
#define JOIN_HINT_NS   "tagnet"
#define JOIN_HINT_KEY  "ap"

typedef struct {
    uint8_t  bssid[6];
    uint8_t  pad[2];        /* named, so every byte is defined for memcmp */
    int32_t  channel;
} join_hint_t;

static join_hint_t hint_nvs;             /* what NVS is believed to hold */
static bool        hint_nvs_valid = false;

static void joinHintLoad(void)
{
    Preferences prefs;
    if (!prefs.begin(JOIN_HINT_NS, true /* read only */)) {
        return;                          /* never written: first boot */
    }

    join_hint_t hint;
    size_t got = prefs.getBytes(JOIN_HINT_KEY, &hint, sizeof(hint));
    prefs.end();

    if (got != sizeof(hint) || hint.channel <= 0) {
        return;
    }

    hint_nvs = hint;
    hint_nvs_valid = true;

    memcpy(fast_bssid, hint.bssid, sizeof(fast_bssid));
    fast_channel = hint.channel;
    fast_have_ip = false;   /* a lease is not ours to assume across a reset */
    fast_valid   = true;
}

static void joinHintSave(void)
{
    const uint8_t *bssid = WiFi.BSSID();
    int32_t        channel = WiFi.channel();

    if (bssid == NULL || channel <= 0) {
        return;
    }

    join_hint_t hint;
    memset(&hint, 0, sizeof(hint));
    memcpy(hint.bssid, bssid, sizeof(hint.bssid));
    hint.channel = channel;

    if (hint_nvs_valid && memcmp(&hint, &hint_nvs, sizeof(hint)) == 0) {
        return;                          /* same AP as last time, no write */
    }

    Preferences prefs;
    if (!prefs.begin(JOIN_HINT_NS, false)) {
        return;
    }
    prefs.putBytes(JOIN_HINT_KEY, &hint, sizeof(hint));
    prefs.end();

    hint_nvs = hint;
    hint_nvs_valid = true;
}
#endif /* WIFI_FAST_JOIN */

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

#if WIFI_FAST_JOIN
    joinHintLoad();
    if (fast_valid) {
        /* Straight to the channel the tag last associated on, skipping the
         * scan that otherwise stands between power-on and the TCP server.
         * Still a real DHCP exchange, so this gets the longer deadline. */
        beginFast(WIFI_FAST_JOIN_MS);
        Serial.println("wifi:connecting, hinted");
        return;
    }
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

#if WIFI_FAST_RESUME
    /* Snapshot before the teardown, while these still read back. Gated on
     * wifi_up: a suspend entered while the link was already down would
     * otherwise cache a half-formed association and send the next resume
     * chasing it. */
    if (wifi_up) {
        const uint8_t *bssid = WiFi.BSSID();
        if (bssid != NULL) {
            memcpy(fast_bssid, bssid, sizeof(fast_bssid));
            fast_channel = WiFi.channel();
            fast_ip      = WiFi.localIP();
            fast_gw      = WiFi.gatewayIP();
            fast_mask    = WiFi.subnetMask();
            fast_dns     = WiFi.dnsIP();
            fast_valid   = (fast_channel > 0);
            fast_have_ip = (fast_valid &&
                            !(fast_ip == IPAddress((uint32_t)0)));
        }
    }
#endif

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

#if WIFI_FAST_PATH
    if (fast_valid) {
        /* Straight back to the same AP on the same channel -- and with the
         * same address when the suspend captured one, which drops DHCP too.
         * serviceWifi() restarts the server as usual, and watches the clock
         * in case this does not take. A cache left by the boot hint has no
         * address in it, so that case still waits on DHCP and gets the
         * longer deadline. */
        beginFast(fast_have_ip ? WIFI_FAST_RESUME_MS : WIFI_FAST_JOIN_MS);
        return;
    }
    fast_pending = false;
#endif

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

#if WIFI_FAST_PATH
    /* A hinted attempt that has not come up by now is chasing something that
     * changed under us -- the AP moved channel or band, or the lease went to
     * someone else. Drop the cache and join the long way; the next success
     * re-learns it. */
    if (fast_pending) {
        if (connected) {
            fast_pending = false;
        } else if (millis() - last_attempt_ms >= fast_deadline_ms) {
            fast_pending = false;
            fast_valid = false;
            fast_have_ip = false;
            WiFi.disconnect(false);
            clearStaticIp();
            WiFi.begin(WIFI_SSID, WIFI_PASS);
            last_attempt_ms = millis();
            Serial.println("{\"info\":\"wifi\",\"fast\":\"fallback\","
                           "\"reason\":\"hinted join did not associate\"}");
            return;
        }
    }
#endif

    if (connected && !wifi_up) {
        wifi_up = true;
        ever_up = true;
#if WIFI_FAST_JOIN
        joinHintSave();   /* remember the channel for the next boot */
#endif
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

    /* Retry. WiFi.setAutoReconnect(true) (wifiStart()) does retry internally
     * on disconnect, but only for the reasons in the core's
     * _is_staReconnectableReason(): an AUTH_FAIL after the first attempt, or
     * an ASSOC_LEAVE, leaves the STA down with nothing left to bring it back,
     * and nothing else in this firmware calls begin() again. This is the
     * floor under that, and under a begin() that never got far enough to
     * raise a disconnect event at all.
     *
     * The disconnect() first is what makes it safe, and is why an
     * unconditional begin() here did not work before: esp_wifi_set_config()
     * is refused while the STA is mid-association ("sta is connecting, cannot
     * set config"). It also settles the race with the core's own retry --
     * ASSOC_LEAVE is the one reason its handler deliberately will not chase,
     * so there is no in-flight attempt of its own left to collide with. */
    if (connected) {
        last_attempt_ms = millis();   /* the retry timer only runs while down */
    } else if (!fast_pending && millis() - last_attempt_ms >= WIFI_RETRY_MS) {
        WiFi.disconnect(false);
        clearStaticIp();
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        last_attempt_ms = millis();
        retry_count++;

        if (retry_count == 1 && !ever_up) {
            /* One line, once, and only when the link has never been up this
             * boot: enough to tell "slow to join" from "not joining" on the
             * serial console, without dropping notices into the middle of a
             * session's replies later in a run. The running count is on the
             * "net" command for anyone who wants it. */
            Serial.println("{\"info\":\"wifi\",\"retry\":1,"
                           "\"reason\":\"no link since boot\"}");
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
        out.printf("{\"net\":\"down\", \"retries\":%u}\n", (unsigned)retry_count);
        return;
    }
    out.printf("{\"net\":\"up\", \"ip\":\"%s\", \"port\":%u, \"rssi\":%d, "
               "\"retries\":%u}\n",
               WiFi.localIP().toString().c_str(),
               (unsigned)TCP_PORT,
               (int)WiFi.RSSI(),
               (unsigned)retry_count);
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
