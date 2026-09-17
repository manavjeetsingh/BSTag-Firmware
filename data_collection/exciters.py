"""Which exciter illuminates a run, and how EXCITER names it.

The carrier side of the same idea esync_mpp.py applies to the sync blank:
one registry maps the EXCITER value in configurations.json to something
that can be tuned and levelled, so nothing in collection.py or
measurePhasesMultiThreadedMultiTags.py knows the names. Those two used to
test `exciter_type == 'rf_gen'` in eight places, which meant a second
exciter was eight edits and any one of them missed was a run that measured
at whatever frequency the last one left behind.

The contract is the two calls the GPIB generator already had:

    set_freq(mhz)   tune the carrier, in MHz
    set_pwr(power)  set the output level, in that exciter's own unit

plus, optionally, close() -- taken if it is there, so an implementation
holding a socket can hang up without every caller knowing it holds one.

EXC_POWER deliberately has no unit attached to it, which is why the config
key is not EXC_POWER_DBM any more: it is passed to the exciter untouched and
means whatever that exciter's level control means. On the rf_gen that is
dBm, because a signal generator has a calibrated output. On the bladeRF it
is TX gain in dB, because a bladeRF does not -- converting one to the other
would need a calibration nobody here has, and inventing one would make a
made-up number look measured. So EXC_POWER belongs to the exciter a run is
using: 12.9 is a normal carrier on the generator and nearly nothing on the
bladeRF, where 60 is a reasonable starting point.

A wireless run needs one more thing of its exciter (a blank it can fire),
but that is esync_mpp's registry, not this one: they are separate because
an exciter can perfectly well light the tags without being able to blank on
command, which is exactly the case for the manual bladeRF script and was
the case for every wired run ever done here.
"""

import os
import sys

from ribbn_scripts.hardware_api.hardware import (Exciter, BladeRFExciter,
                                                 BLADERF_PORT)

# The bladeRF's flowgraph lives with the other bladeRF code, not in here.
NULL_SYNC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BladeRFCode", "null_sync")

# Where the tooling parks the carrier when it is done with it. Not an "off"
# switch the instruments necessarily have -- for the generator it is a real
# -30 dBm; for the bladeRF it is below any gain that exists, which is read as
# "mute". Negative works for both for that reason.
OFF_POWER = -30

# EXCITER values that mean "no exciter" -- someone else is running the
# carrier, or there isn't one. "None" (the string) is what configurations.json
# has always carried for this.
NO_EXCITER = {"", "none", "null"}

_EXCITERS = {}


def register_exciter(*exciter_types):
    """Register a factory for EXCITER values.

    Factories are called with every setting the run has as keyword
    arguments; take what you need and absorb the rest with **_, so a
    setting added later does not break the implementations that do not
    want it.
    """
    def register(factory):
        for name in exciter_types:
            _EXCITERS[name] = factory
        return factory
    return register


def registered_exciters():
    return sorted(_EXCITERS)


@register_exciter('rf_gen')
def make_rf_gen(**_):
    """The GPIB signal generator. Takes no settings -- its address is fixed
    in the Exciter class."""
    return Exciter()


@register_exciter('bladerf')
def make_bladerf(bladerf_host=None, bladerf_port=BLADERF_PORT, **_):
    """The bladeRF: its flowgraph in this process, or one on another machine.

    Normally the bladeRF is in this machine's USB port, so the run builds the
    flowgraph itself (BladeRFCode/null_sync/bladerf_cw.py) and drives it
    directly -- nothing to start first, no socket, and the gate trigger is a
    method call.

    That means the run has to be running on a python that has GNU Radio and
    osmosdr, which the repo's .venv does not (they are GRC-provided, not pip
    packages). On the lab Windows box that is
    C:\ProgramData\radioconda\python.exe.

    BLADERF_HOST is for the case that cannot be satisfied that way -- the
    tags on one machine and the bladeRF on another. Then
    bladerf_exciter_server.py runs the flowgraph over there and this end
    talks to it over TCP.
    """
    if bladerf_host:
        return BladeRFExciter(host=bladerf_host, port=bladerf_port)

    if NULL_SYNC_DIR not in sys.path:
        sys.path.insert(0, NULL_SYNC_DIR)
    try:
        from bladerf_cw import CWExciter
    except ImportError as e:
        raise Exception(
            f"cannot drive the bladeRF from this python: {e}. GNU Radio and "
            f"osmosdr are not pip packages and are not in the repo's .venv, "
            f"so run this under a python that has them (on the lab Windows "
            f"box, C:\ProgramData\radioconda\python.exe, which needs "
            f"`pip install pyserial` for the tag side). If the bladeRF is on "
            f"a different machine, start bladerf_exciter_server.py there and "
            f"set BLADERF_HOST to its address instead.")

    exc = CWExciter()
    exc.start()
    return exc


def normalize(exciter_type):
    """The EXCITER value as the registries use it: lowercased, with the
    several spellings of "there isn't one" folded to None."""
    if exciter_type is None:
        return None
    name = str(exciter_type).strip().lower()
    return None if name in NO_EXCITER else name


def settings_from_config(configurations):
    """Pull the exciter's settings out of configurations.json.

    Anything prefixed with an exciter's name (BLADERF_HOST, ...) is handed
    to every factory, lowercased, as a keyword argument. Keeping the rule
    here rather than in collection.py is what lets a new exciter add its own
    settings without collection.py learning them.
    """
    prefixes = tuple(f"{name.upper()}_" for name in registered_exciters())
    return {key.lower(): value for key, value in configurations.items()
            if key.upper().startswith(prefixes)}


def make_exciter(exciter_type, **settings):
    """Build the exciter for this run, or None if the run has none."""
    name = normalize(exciter_type)
    if name is None:
        return None

    factory = _EXCITERS.get(name)
    if factory is None:
        known = ", ".join(repr(n) for n in registered_exciters())
        raise Exception(
            f"no exciter registered for EXCITER={exciter_type!r}. Known: "
            f"{known}, or \"None\" to drive the carrier yourself. Add one "
            f"with @exciters.register_exciter({name!r}).")

    return factory(**settings)


def shutdown(exc, off_power=OFF_POWER):
    """Park the carrier and let go of the exciter.

    Called on the way out of a run, including a run that is on its way out
    because something failed, so it never raises: an exciter left lit is
    worth a warning and is not worth losing the run's error over.
    """
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
