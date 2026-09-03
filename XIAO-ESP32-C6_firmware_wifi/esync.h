#ifndef ESYNC_H
#define ESYNC_H

#include <Arduino.h>

/* Exciter sync. `esync` arms the listener; loop() then samples the RX
 * channel as fast as it can go, looking for the framed packet described
 * in config.h -- a long SFD blank, then four Manchester bits carrying an
 * id. The listener is one shot: the first well-formed packet latches its
 * timing, the listener disarms itself, and whatever was staged with
 * q_<cmd> runs at a fixed offset from the SFD edge. A packet that fails
 * any check is dropped and listening continues. Nothing is printed from
 * the detector: read the packet with esyncr and the command's reply with
 * qr. */

/* Arm: force the RX channel, clear the window, start listening. */
void esyncListen(void);

/* Disarm and drop the window and detector state. */
void esyncStop(void);

bool esyncActive(void);

/* Drop the window and the detector state, keeping the armed flag as is. */
void esyncReset(void);

/* Last rising edge, latched at detection. Emitted on demand so the
 * detector never blocks on a Serial write between the edge and the
 * dispatch. Cleared when esyncListen() arms a new run. */
void esyncReport(Print &out);
void esyncClearReport(void);

/* Take one sample into the moving window. Call once per loop() pass. */
void esyncSample(void);

/* Look at the newest sample. On a rising edge: report it, disarm, and
 * run the queued command. */
void esyncListening(void);

#endif /* ESYNC_H */
