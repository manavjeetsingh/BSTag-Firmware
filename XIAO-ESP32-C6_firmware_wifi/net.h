#ifndef NET_H
#define NET_H

#include <Arduino.h>

/* Bring up STA mode, or stay off when WIFI_SSID is empty. */
void wifiStart(void);

/* Track link state and retry; call from loop(). */
void serviceWifi(void);

/* Reap dropped clients and accept new ones; call from loop(). */
void serviceTcp(void);

void printNetStatus(Print &out);

#endif /* NET_H */
