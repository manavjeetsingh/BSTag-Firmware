#ifndef BUFFERED_OUT_H
#define BUFFERED_OUT_H

#include <Arduino.h>
#include <string.h>

#include "config.h"

/* ------------------------------------------------------------------ */
/* TX coalescing.                                                      */
/*                                                                     */
/* WiFiClient has no write buffer: every print() becomes its own       */
/* lwIP send(), and with TCP_NODELAY set that is one packet per        */
/* number. Dumping 10k samples that way is ~10k packets. This wraps    */
/* the sink and flushes in OUT_CHUNK_LEN blocks instead.               */
/* ------------------------------------------------------------------ */
class BufferedOut : public Print {
public:
    explicit BufferedOut(Print &sink) : _sink(sink), _n(0) {}
    ~BufferedOut() { done(); }

    size_t write(uint8_t c) override
    {
        if (_n == sizeof(_buf)) {
            flushChunk();
        }
        _buf[_n++] = c;
        return 1;
    }

    size_t write(const uint8_t *data, size_t len) override
    {
        size_t remaining = len;
        while (remaining > 0) {
            if (_n == sizeof(_buf)) {
                flushChunk();
            }
            size_t space = sizeof(_buf) - _n;
            size_t take  = (remaining < space) ? remaining : space;
            memcpy(_buf + _n, data, take);
            _n   += take;
            data += take;
            remaining -= take;
        }
        return len;
    }

    void done() { flushChunk(); }

private:
    void flushChunk()
    {
        if (_n > 0) {
            _sink.write(_buf, _n);
            _n = 0;
        }
    }

    Print  &_sink;
    uint8_t _buf[OUT_CHUNK_LEN];
    size_t  _n;
};

#endif /* BUFFERED_OUT_H */
