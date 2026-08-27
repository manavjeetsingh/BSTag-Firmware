#ifndef COMMANDS_H
#define COMMANDS_H

#include <Arduino.h>

void printHelp(Print &out);

/* Dispatch one line. `command` is trimmed in place. `session_idx` is the
 * transport the line arrived on, so streaming commands know where to reply.
 * `from_queue` is true only for the esync-fired dispatch, letting a command
 * time itself differently when it runs synchronised. */
void handleCommand(char *command, Print &out, int session_idx,
                   bool from_queue = false);

/* ------------------------------------------------------------------ */
/* Deferred command. One slot, staged with q_<cmd> and fired by the    */
/* esync detector on the next rising edge.                             */
/* ------------------------------------------------------------------ */

/* Stage a command. `session_idx` is where its output goes when it runs;
 * a second call overwrites the slot. */
void queueCommand(const char *cmd, int session_idx);

bool        hasQueuedCommand(void);
const char *queuedCommand(void);
void        clearQueuedCommand(void);

/* Clear the slot, then run what was in it. No-op when empty. */
void runQueuedCommand(void);

/* ------------------------------------------------------------------ */
/* Deferred reply.                                                     */
/*                                                                     */
/* The esync window runs with the radio off, so the session that staged */
/* the command is gone by the time the edge fires. Rather than drop the */
/* reply on a dead socket, it is captured in RAM and handed over with   */
/* qr once the host reconnects -- the same pull pattern as rdb/rds.     */
/* Reads are non-destructive; esyncListen() clears it for the next run. */
/* ------------------------------------------------------------------ */
void dumpQueuedReply(Print &out);
void clearQueuedReply(void);

#endif /* COMMANDS_H */
