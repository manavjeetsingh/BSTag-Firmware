"""Host side of exciter sync for wireless MPP.

Over WiFi the host cannot start the tags within a 3 ms MPP dwell of each other,
so it doesn't try: every tag is staged, armed, and then started by the
bladeRF's ASK preamble, which all of them hear at once (see
BladeRFCode/ASK_sync/README.md). The sequence is _esyncRound() in
measurePhasesMultiThreadedMultiTags.py; this holds its timing and checks.
"""

ESYNC_WIFI_TIMEOUT_MS = 30000   # == the firmware's config.h: it gives up and
                                # brings WiFi back after this long unsynced

# After the last arm ack, before the preamble. The tag needs ESYNC_WIFI_QUIET_MS
# (50 ms) to take its radio down and 26 ms of carrier to fill the correlator;
# a preamble sent before that is simply never heard. The rest is margin.
ARM_SETTLE_S = 1.0

# Re-shoots per MPP round before the run gives up. A missed preamble (a burst
# landing on it) is worth retrying; one that misses every time is the setup.
MAX_ROUND_ATTEMPTS = 10

RECONNECT_DEADLINE_S = ESYNC_WIFI_TIMEOUT_MS / 1000.0 + 30.0


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
