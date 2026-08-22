#ifndef COMMANDS_H
#define COMMANDS_H

#include <Arduino.h>

void printHelp(Print &out);

/* Dispatch one line. `command` is trimmed in place. `session_idx` is the
 * transport the line arrived on, so streaming commands know where to reply. */
void handleCommand(char *command, Print &out, int session_idx);

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

#endif /* COMMANDS_H */
