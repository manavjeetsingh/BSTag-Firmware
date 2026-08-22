#include "session.h"

#include "acquisition.h"
#include "buffered_out.h"
#include "commands.h"
#include "hardware.h"

Session sessions[SESSION_COUNT];

void sessionsInit(void)
{
    for (int i = 0; i < SESSION_COUNT; i++) {
        sessions[i].io = NULL;
        sessionReset(i);
    }
    sessions[0].io = &Serial;
}

void sessionReset(int idx)
{
    sessions[idx].len = 0;
    sessions[idx].discard = false;
}

void sessionClose(int idx)
{
    sessions[idx].io = NULL;
    sessionReset(idx);
    plotterSessionClosed(idx);
}

void pollSession(int idx)
{
    Session &s = sessions[idx];
    if (s.io == NULL) {
        return;
    }

    while (s.io->available() > 0) {
        int c = s.io->read();
        if (c < 0) {
            break;
        }

        if (c == '\n' || c == '\r') {
            if (s.discard) {
                s.discard = false;
                s.len = 0;
                s.io->println("cmd:too long");
                continue;
            }
            if (s.len > 0) {
                s.buf[s.len] = '\0';
                s.len = 0;
                setUserLed(true);
                {
                    BufferedOut out(*s.io);
                    handleCommand(s.buf, out, idx);
                    out.done();
                }
                setUserLed(false);
            }
            continue;
        }

        if (s.discard) {
            continue;
        }
        if (s.len < CMD_BUF_LEN - 1) {
            s.buf[s.len++] = (char)c;
        } else {
            s.discard = true;   /* swallow the rest of the line */
        }
    }
}
