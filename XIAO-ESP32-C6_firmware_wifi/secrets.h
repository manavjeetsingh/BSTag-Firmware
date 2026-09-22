#ifndef SECRETS_H
#define SECRETS_H

/* ------------------------------------------------------------------ */
/* Network credentials. This file is gitignored; keep it out of the    */
/* repo. Leave WIFI_SSID empty ("") to build with WiFi off at runtime. */
/* ------------------------------------------------------------------ */
/* WiFi off: an empty SSID makes wifiStart() skip WiFi.begin() entirely,
 * so serviceWifi()/serviceTcp() return immediately and the loop stays
 * tight. Uncomment the line below to put the radio back. */
#define WIFI_SSID       "GL-SFT1200-efa"
// #define WIFI_SSID       ""
#define WIFI_PASS       "goodlife"
#define WIFI_HOSTNAME   "tagv93"

#endif /* SECRETS_H */
