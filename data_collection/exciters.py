"""Which exciter lights a run.

EXCITER in configurations.json names it; make_exciter() returns an object with
set_freq(mhz) and set_pwr(power), plus close() if it holds a link, and nothing
else in the tooling knows the names.

EXC_POWER goes to the exciter untouched: dBm on rf_gen, TX gain in dB on the
bladeRF, which has no calibrated output. So 12.9 is a normal generator level
and nearly nothing as bladeRF gain -- start around 60 there.

Only the bladeRF can sync a wireless run: its sync() keys the ASK preamble's
2 ms chips sample-exactly, which GPIB writes cannot.
"""

import os
import sys

from ribbn_scripts.hardware_api.hardware import (Exciter, BladeRFExciter,
                                                 BLADERF_PORT)

ASK_SYNC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BladeRFCode", "ASK_sync")

OFF_POWER = -30   # parks both: -30 dBm on the generator, mute on the bladeRF
EXCITERS = ("rf_gen", "bladerf")
NO_EXCITER = {"", "none", "null"}


def make_bladerf(bladerf_host=None, bladerf_port=BLADERF_PORT, **_):
    """The bladeRF's flowgraph in this process, or, with BLADERF_HOST, one on
    another machine behind bladerf_exciter_server.py.

    In process needs a python with GNU Radio and osmosdr, which the repo's
    .venv does not have -- on the lab box, C:\\ProgramData\\radioconda\\python.exe.
    """
    if bladerf_host:
        return BladeRFExciter(host=bladerf_host, port=bladerf_port)

    if ASK_SYNC_DIR not in sys.path:
        sys.path.insert(0, ASK_SYNC_DIR)
    try:
        from bladerf_cw import CWExciter
    except ImportError as e:
        raise Exception(
            f"cannot drive the bladeRF from this python ({e}). Run under a GNU "
            f"Radio python (C:\\ProgramData\\radioconda\\python.exe on the lab "
            f"box, plus `pip install pyserial`), or set BLADERF_HOST to a "
            f"machine running bladerf_exciter_server.py.")
    exc = CWExciter()
    exc.start()
    return exc


def normalize(exciter_type):
    """Lowercase EXCITER, folding the spellings of "there isn't one" to None."""
    if exciter_type is None:
        return None
    name = str(exciter_type).strip().lower()
    return None if name in NO_EXCITER else name


def settings_from_config(configurations):
    """Exciter-prefixed keys (BLADERF_HOST, ...), lowercased, as kwargs."""
    prefixes = tuple(f"{name.upper()}_" for name in EXCITERS)
    return {key.lower(): value for key, value in configurations.items()
            if key.upper().startswith(prefixes)}


def make_exciter(exciter_type, **settings):
    """Build the exciter for this run, or None if the run has none."""
    name = normalize(exciter_type)
    if name is None:
        return None
    if name == "rf_gen":
        return Exciter()
    if name == "bladerf":
        return make_bladerf(**settings)
    raise Exception(f"unknown EXCITER={exciter_type!r}; expected one of "
                    f"{EXCITERS} or \"None\" to drive the carrier yourself.")


def shutdown(exc, off_power=OFF_POWER):
    """Park the carrier and let go of the exciter. Never raises: it runs on
    the way out of failed runs too, and must not mask their error."""
    if exc is None:
        return
    try:
        exc.set_pwr(off_power)
    except Exception as e:
        print(f"WARNING could not park the exciter at {off_power}: {e}")
    close = getattr(exc, "close", None)
    if close is not None:
        try:
            close()
        except Exception as e:
            print(f"WARNING could not close the exciter link: {e}")
