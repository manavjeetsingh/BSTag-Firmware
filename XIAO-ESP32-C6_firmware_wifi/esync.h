#ifndef ESYNC_H
#define ESYNC_H

#include <Arduino.h>

/* Exciter sync. `esync` arms the listener; loop() then samples the RX
 * channel as fast as it can go, looking for the falling edge that starts
 * a blank. The listener is one shot: the first falling edge that survives
 * its checks is latched as the timing reference, and whatever was staged
 * with q_<cmd> runs ESYNC_FIRE_DELAY_US later -- landing where the
 * carrier is due back, without the tag having to see it come back. The
 * listener disarms itself at the dispatch.
 *
 * The edge is the first sample at or under ESYNC_MIN_BASELINE_MV, which
 * is both the timing reference and the proof the blank is real -- a sag
 * that never reaches the floor never latches one.
 *
 * The tag is committed from that edge, so only one thing can still call
 * the fire off: the carrier returning before ESYNC_BLANK_MIN_US, which
 * discards the edge and leaves the listener running. A blank that runs
 * long is fired against and flagged after the fact -- see esyncReport().
 *
 * Nothing is printed from the detector: read the edge with esyncr and the
 * command's reply with qr. */

/* Arm: force the RX channel, clear the window, start listening. */
void esyncListen(void);

/* Disarm and drop the window and detector state. */
void esyncStop(void);

bool esyncActive(void);

/* Drop the window and the detector state, keeping the armed flag as is. */
void esyncReset(void);

/* Last falling edge, latched at detection, plus the blank as it was
 * actually measured. Emitted on demand so the detector never blocks on a
 * Serial write between the edge and the dispatch. Cleared when
 * esyncListen() arms a new run. */
void esyncReport(Print &out);
void esyncClearReport(void);

/* Take one sample into the moving window. Call once per loop() pass. */
void esyncSample(void);

/* Look at the newest sample. Latches a falling edge, then fires the
 * queued command once ESYNC_FIRE_DELAY_US has elapsed from it. */
void esyncListening(void);

#endif /* ESYNC_H */
