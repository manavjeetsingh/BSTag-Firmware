#ifndef ESYNC_H
#define ESYNC_H

#include <Arduino.h>

/* Exciter sync. `esync` arms the listener; loop() then samples the RX
 * channel as fast as it can go. The listener is one shot: the first
 * rising edge is reported on Serial, the listener disarms itself, and
 * whatever was staged with q_<cmd> runs at that instant. */

/* Arm: force the RX channel, clear the window, start listening. */
void esyncListen(void);

/* Disarm and drop the window and detector state. */
void esyncStop(void);

bool esyncActive(void);

/* Drop the window and the detector state, keeping the armed flag as is. */
void esyncReset(void);

/* Take one sample into the moving window. Call once per loop() pass. */
void esyncSample(void);

/* Look at the newest sample. On a rising edge: report it, disarm, and
 * run the queued command. */
void esyncListening(void);

#endif /* ESYNC_H */
