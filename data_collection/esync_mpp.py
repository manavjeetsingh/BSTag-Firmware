"""Host side of exciter sync for wireless MPP.

Over WiFi the host cannot start the tags within a 3 ms MPP dwell of each other,
so it doesn't try: every tag is staged, armed, and then started by the
bladeRF's ASK preamble, which all of them hear at once (see
BladeRFCode/ASK_sync/README.md). The sequence is _esyncRound() in
measurePhasesMultiThreadedMultiTags.py; this holds its timing and checks.
"""

# After the last arm ack, before the preamble. The tag needs 26 ms of carrier
# to fill the correlator; a preamble sent before that is simply never heard.
# The rest is margin. (It used to have to cover the radio going down too.)
ARM_SETTLE_S = 0.3

# Re-shoots per MPP round before the run gives up. A missed preamble (a burst
# landing on it) is worth retrying; one that misses every time is the setup.
MAX_ROUND_ATTEMPTS = 10

# How long a tag waits for the preamble to fire its staged command, which is
# now a wait on the live socket rather than on the radio coming back. The
# exciter keys the preamble within ARM_SETTLE_S of the last arm, so past a few
# seconds it was not heard and the round is better re-shot than waited on.
FIRE_DEADLINE_S = 15.0


# Sweep points to drop from a wireless run: the exciter's 3rd harmonic lands
# here on the tags' 2.4 GHz channel and jams the link for the whole round --
# both tags go silent with their sockets still open, and no amount of retrying
# gets a round through while the carrier is parked there. 810-820 MHz puts the
# harmonic at 2430-2460 MHz, i.e. WiFi channels 3-12.
#
# Inclusive, in MHz. A low-pass filter on the exciter output is the real fix,
# and with one fitted this can go back to (None, None). Note this covers only
# part of the vulnerable window -- the harmonic is in-band for the whole of
# 800-828 MHz (2400-2483.5 / 3) -- so 805 and 825 still go out, and they are
# the ones to look at if a run dies with an AP on channel 1-3 or 12-13.
WIFI_JAMMED_MHZ = (810.0, 820.0)


def wifi_jammed(freq_mhz):
    """True if this sweep point jams the tags' WiFi (see WIFI_JAMMED_MHZ)."""
    lo, hi = WIFI_JAMMED_MHZ
    if lo is None or hi is None:
        return False
    return lo <= freq_mhz <= hi


def check_fired(reports):
    """Raise unless every tag locked on the preamble and fired.

    A tag that never locked answers esyncr with "pending":0 and peak_rho, the
    best correlation it saw: near ESYNC_MIN_RHO (0.8) means it heard the
    preamble but too noisily, near 0 means it never heard it, or the exciter's
    code/chip length does not match the firmware's.
    """
    missed = [f"{name} never locked: peak_rho={r.get('peak_rho')} "
              f"peak_swing_mv={r.get('peak_swing_mv')} "
              f"level_mv={r.get('level_mv')} stalls={r.get('stalls')}"
              for name, r in reports.items() if "rho" not in r]
    if missed:
        raise Exception("; ".join(missed))


def sync_summary(reports):
    """One line of sync diagnostics for the per-round log."""
    rhos = [r["rho"] for r in reports.values() if "rho" in r]
    snrs = [r["snr_db"] for r in reports.values() if "snr_db" in r]
    if not rhos:
        return "no lock"
    return (f"rho {min(rhos):.2f}..{max(rhos):.2f}, "
            f"snr {min(snrs):.1f}..{max(snrs):.1f} dB")
