#ifndef NET_H
#define NET_H

#include <Arduino.h>

/* Bring up STA mode, or stay off when WIFI_SSID is empty. A no-op stub
 * when NET_ENABLED (config.h) is 0 -- WiFi/TCP is not compiled in at all. */
void wifiStart(void);

/* Track link state and retry; call from loop(). */
void serviceWifi(void);

/* Reap dropped clients and accept new ones; call from loop(). */
void serviceTcp(void);

void printNetStatus(Print &out);

/* ------------------------------------------------------------------ */
/* Suspend/resume. The WiFi driver and lwIP tasks outrank loopTask, so */
/* a beacon, a retry or a stray ACK can stall the esync sample loop by */
/* milliseconds. Stopping the radio for the listening window removes   */
/* that entirely; measured sync goes from milliseconds to clean.       */
/*                                                                     */
/* Anything written to a TCP session while suspended is discarded --   */
/* the socket is gone. Serial is unaffected.                           */
/* ------------------------------------------------------------------ */
void wifiSuspend(void);      /* drop clients, stop the radio */
void wifiResume(void);       /* re-associate; serviceWifi() finishes the job */
bool wifiSuspended(void);

#endif /* NET_H */
