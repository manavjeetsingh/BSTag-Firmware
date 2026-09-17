#!/usr/bin/env python3
"""
bladerf_cw.CWExciter behind a line protocol, for a bladeRF on another machine.

This is NOT how a normal run drives the bladeRF. `data_collection` holds a
CWExciter directly, in the run's own process, which needs nothing started
beforehand and no socket at all -- see exciters.py. This server exists for
the one case that cannot do that: the tags on one machine and the bladeRF
plugged into another. Then the flowgraph runs here, and the run reaches it
through hardware.BladeRFExciter, which speaks the protocol below.

Run it on the machine the bladeRF is plugged into, with a GNU Radio python
(on the lab Windows box, C:\\ProgramData\\radioconda\\python.exe):

    python3 bladerf_exciter_server.py --bind 0.0.0.0 --port 3334

and point the run at it in data_collection/configurations.json:

    "EXCITER": "bladerf",
    "BLADERF_HOST": "<this machine's ip>",
    "BLADERF_PORT": 3334

Leaving BLADERF_HOST empty is what selects the in-process exciter instead.

Protocol
--------
One command per line, "\n" terminated, ASCII, one reply line each, "ok ..."
or "err ...". One client at a time -- a second connection is served only
after the first hangs up, which is what keeps two runs from fighting over
one carrier. A client may reconnect freely; the flowgraph keeps transmitting
across connections, so the carrier does not blink when a run ends.

    ping                 -> ok pong
    freq <MHz>           tune so the emitted tone lands on <MHz>
    power <dB>           set the TX gain (POWER IS GAIN, in bladerf_cw)
    gain <dB>            the same thing, under the name the radio uses
    blank <ms>           fire one sync blank
    on | off             unmute / mute the carrier in baseband
    status               -> ok freq=915.000MHz gain=60dB muted=0 blanks=7
    quit                 close this connection (the transmitter stays up)
"""

import argparse
import socket

from bladerf_cw import CWExciter, DEFAULT_GAIN_DB, FREQ, TONE_OFFSET

DEFAULT_PORT = 3334          # tags are on 3333; this is the exciter


def handle_command(exc, line):
    """One command line in, one reply line out. Never raises."""
    parts = line.split()
    if not parts:
        return "err empty"
    cmd = parts[0].lower()
    args = parts[1:]

    try:
        if cmd == "ping":
            return "ok pong"

        if cmd == "freq":
            return f"ok freq {exc.set_freq(float(args[0])):.6f}MHz"

        if cmd in ("power", "gain"):
            gain, muted = exc.set_pwr(float(args[0]))
            # The gain reported back is the one that landed, which is not
            # the one asked for if it was clamped, or if it was low enough
            # to mean "off" -- in which case the gain is simply left where
            # it was and the mute is what changed.
            return f"ok {cmd} {gain:g}dB muted {int(muted)}"

        if cmd == "blank":
            ms = float(args[0])
            exc.blank(ms / 1e3)
            return f"ok blank {ms:g}ms"

        if cmd in ("on", "off"):
            exc.carrier_off() if cmd == "off" else exc.carrier_on()
            return f"ok {cmd}"

        if cmd == "status":
            return f"ok {exc.describe()}"

        if cmd in ("quit", "exit", "bye"):
            return "ok bye"

        return f"err unknown command {cmd!r}"
    except ValueError as e:
        # A bad number and a blank CWExciter refused both land here, and
        # both are the client's problem to hear about rather than the
        # server's to die of.
        return f"err {e}"
    except IndexError:
        return f"err bad arguments for {cmd!r}: {' '.join(args)!r}"


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


def serve(exc, bind, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, port))
    srv.listen(1)
    print(f"listening on {bind}:{port}")
    try:
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # One at a time, on purpose: two runs sharing one carrier would
            # blank each other's rounds.
            serve_client(exc, conn, addr)
    finally:
        srv.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="bladeRF CW exciter with a network-triggered sync blank.")
    ap.add_argument("--bind", default="0.0.0.0",
                    help="interface to listen on (default: all)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"control port (default: {DEFAULT_PORT})")
    ap.add_argument("--freq", type=float, default=(FREQ + TONE_OFFSET) / 1e6,
                    help="initial emitted frequency, MHz")
    ap.add_argument("--gain", type=float, default=DEFAULT_GAIN_DB,
                    help="initial TX gain, dB")
    ap.add_argument("--muted", action="store_true",
                    help="start with the carrier off (the client turns it on)")
    args = ap.parse_args()

    exc = CWExciter(freq_mhz=args.freq, gain_db=args.gain, muted=args.muted)
    exc.start()

    print(f"TX CW at {exc.emitted_mhz():.3f} MHz, gain {exc.gain:g} dB"
          f"{' (muted)' if args.muted else ''}.")
    print("EXC_POWER is TX gain in dB here, not a level -- see POWER IS GAIN "
          "in bladerf_cw.py.")
    try:
        serve(exc, args.bind, args.port)
    except KeyboardInterrupt:
        pass
    finally:
        exc.close()
        print("\nstopped")
