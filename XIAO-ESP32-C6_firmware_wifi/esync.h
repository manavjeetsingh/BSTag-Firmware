#ifndef ESYNC_H
#define ESYNC_H

#include <Arduino.h>

/* Exciter sync. `esync` arms the listener; loop() then samples the RX
 * channel as fast as it can go and correlates it against the exciter's
 * Barker-coded ASK preamble (see config.h). The listener is one shot: the
 * first correlation peak that clears ESYNC_MIN_RHO is latched as the timing
 * reference, and whatever was staged with q_<cmd> runs ESYNC_FIRE_DELAY_US
 * later, into steady carrier. The listener disarms itself at the dispatch.
 *
 * Nothing is printed from the detector: read the lock with esyncr and the
 * command's reply with qr. */

/* Arm: force the RX channel, clear the detector, start listening. */
void esyncListen(void);

/* Disarm and drop the detector state. */
void esyncStop(void);

bool esyncActive(void);

/* Drop the detector state, keeping the armed flag as is. */
void esyncReset(void);

/* Last lock, latched at the fire, or what the detector is doing if nothing
 * has fired. Emitted on demand so the detector never blocks on a Serial
 * write between the lock and the dispatch. Cleared when esyncListen() arms
 * a new run. */
void esyncReport(Print &out);
void esyncClearReport(void);

/* Take one timestamped sample. Call once per loop() pass. */
void esyncSample(void);

/* Feed the newest sample to the correlator, and fire the queued command
 * once ESYNC_FIRE_DELAY_US has elapsed from the lock. */
void esyncListening(void);

#endif /* ESYNC_H */
