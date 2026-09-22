#!/usr/bin/env python3
"""
bladerf_cw.CWExciter behind a line protocol, for a bladeRF on another machine.

A normal run holds a CWExciter in its own process (data_collection/exciters.py)
and needs none of this. This is only for tags on one machine and the bladeRF on
another: run it there with a GNU Radio python,

    python3 bladerf_exciter_server.py --bind 0.0.0.0 --port 3334

and set "BLADERF_HOST" (and optionally "BLADERF_PORT") in
data_collection/configurations.json; hardware.BladeRFExciter is the client.

One "\\n"-terminated ASCII command per line, one "ok ..." / "err ..." reply
each. One client at a time, so two runs cannot fight over one carrier; the
carrier stays up across connections.

    freq <MHz>    tune so the emitted tone lands on <MHz>
    power <dB>    TX gain; below 0 mutes (POWER IS GAIN, in bladerf_cw)
    sync          send one ASK sync preamble
    status        -> ok freq=915.100000MHz gain=60dB muted=0 syncs=7
    quit          close this connection (the transmitter stays up)
"""

import argparse
import socket

from bladerf_cw import CWExciter, DEFAULT_GAIN_DB, FREQ, TONE_OFFSET

DEFAULT_PORT = 3334          # tags are on 3333


def handle_command(exc, line):
    """One command line in, one reply line out. Never raises."""
    cmd, *args = line.split()
    cmd = cmd.lower()
    try:
        if cmd == "freq":
            return f"ok freq {exc.set_freq(float(args[0])):.6f}MHz"
        if cmd == "power":
            gain, muted = exc.set_pwr(float(args[0]))
            return f"ok power {gain:g}dB muted {int(muted)}"
        if cmd == "sync":
            exc.sync()
            return "ok sync"
        if cmd == "status":
            return f"ok {exc.describe()}"
        if cmd == "quit":
            return "ok bye"
        return f"err unknown command {cmd!r}"
    except (ValueError, IndexError) as e:
        return f"err {cmd}: {e}"


def serve_client(exc, conn, addr):
    print(f"  client {addr[0]}:{addr[1]} connected")
    buf = b""
    with conn:
        while True:
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                reply = handle_command(exc, line)
                print(f"    {line}  ->  {reply}")
                try:
                    conn.sendall((reply + "\n").encode("ascii"))
                except OSError:
                    return
                if reply == "ok bye":
                    return
    print(f"  client {addr[0]}:{addr[1]} disconnected")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="bladeRF CW exciter with a "
                                             "network-triggered ASK sync.")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--freq", type=float, default=(FREQ + TONE_OFFSET) / 1e6,
                    help="initial emitted frequency, MHz")
    ap.add_argument("--gain", type=float, default=DEFAULT_GAIN_DB,
                    help="initial TX gain, dB")
    ap.add_argument("--muted", action="store_true",
                    help="start with the carrier off")
    args = ap.parse_args()

    exc = CWExciter(freq_mhz=args.freq, gain_db=args.gain, muted=args.muted)
    exc.start()
    print(f"TX CW at {exc.emitted_mhz():.3f} MHz, gain {exc.gain:g} dB"
          f"{' (muted)' if args.muted else ''}.")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(1)
    print(f"listening on {args.bind}:{args.port}")
    try:
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            serve_client(exc, conn, addr)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        exc.close()
        print("\nstopped")
