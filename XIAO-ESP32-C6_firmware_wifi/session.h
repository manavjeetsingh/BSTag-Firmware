#ifndef SESSION_H
#define SESSION_H

#include <Arduino.h>

#include "config.h"

/* One command-line assembler per transport. Slot 0 is always Serial. */
typedef struct {
    Stream *io;                 /* NULL when the slot is idle */
    char    buf[CMD_BUF_LEN];
    size_t  len;
    bool    discard;            /* set when a line exceeds CMD_BUF_LEN */
} Session;

extern Session sessions[SESSION_COUNT];

/* Clear every slot, then bind slot 0 to Serial. */
void sessionsInit(void);

void sessionReset(int idx);
void sessionClose(int idx);

/* Drain one transport without blocking; dispatch on each complete line. */
void pollSession(int idx);

#endif /* SESSION_H */
