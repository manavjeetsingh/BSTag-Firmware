"""Locate the MPP channel dwells inside a captured ADC trace.

The tag sweeps MPP_CHANNELS = {1,1,1,1,3,4,6,7,8}, holding MPP_DWELL_US (3ms)
on each entry, so ch1 is held for four dwells and the rest for one each. Only
the last six dwells are measured; the three leading ch1 dwells are padding.

The boundaries used to come from the host's wall-clock measurement of
perform_mpp() (``mpp_stop_time - mpp_start_time``). That includes the USB-serial
round trip at both ends, which jitters by ~3.6ms against a sweep the firmware
fixes at 27ms -- more than a whole dwell -- so roughly half of all captures had
every channel label shifted by one. The grid is recovered from the trace itself
instead, and the wall-clock timing is not used at all.
"""

import numpy as np

# Samples per MPP_DWELL_US (3ms) dwell, keyed by the run's CONNECTION in
# configurations.json. The two differ because the sample loop does: a wireless
# run keeps the radio up through the capture (see the esync comment in the
# .ino), and the WiFi and lwIP tasks outrank loopTask, so fewer samples land
# inside the same 3ms the firmware holds each channel for. A wired run has the
# radio off and the loop runs clean.
#
# Both are measured, and nothing checks them against the firmware -- a change
# to MPP_DWELL_US, or to anything else loop() has to service, shows up as
# silently misaligned windows rather than an error. Re-measure by reading back
# the dwell fit_sweep_grid() actually settled on.
DWELL_SAMPLES = {"wireless": 45, "wired": 90}
DWELL_TOLERANCE = 0.03   # absorbs small drift in that loop rate
N_PAD = 4                # leading ch1 dwells in MPP_CHANNELS
EDGE_TRIM = 8            # samples either side of a switch, which catch the transition


def _block_means(cumsum, starts, lo_off, hi_off):
    a = starts + lo_off + EDGE_TRIM
    b = starts + hi_off - EDGE_TRIM
    return (cumsum[b] - cumsum[a]) / (b - a)


def fit_sweep_grid(voltages, n_measured=6, *, transport):
    """Find (start, dwell) of the measured dwells; start is the ch1 window.

    `transport` picks which DWELL_SAMPLES the search is centred on; it only
    sets where to look, since the dwell that comes back is the one measured
    from the trace.

    It is keyword-only and has no default on purpose. The search window is
    +/-DWELL_TOLERANCE (3%) around the nominal, so the two modes' ranges do
    not overlap at all -- 43-47 samples against 194-206. Pointed at the wrong
    one it cannot find the real grid, and instead of failing it returns
    whichever 45-sample window happened to score best somewhere inside a
    200-sample dwell. A default is how that happens silently.

    Scores a candidate grid by the contrast across the channel switches it
    implies, minus any step at the ch1 entry -- the padding dwell before the
    measured ch1 is the same channel, so that one boundary must be flat. That
    asymmetry is what pins the phase of the grid. Minimising within-block
    variance instead does not work: it is happiest parking every block inside
    the flat lead-in, ignoring the sweep entirely.
    """
    v = np.asarray(voltages, dtype=float)
    cs = np.concatenate([[0.0], np.cumsum(v)])

    try:
        nominal = DWELL_SAMPLES[transport]
    except KeyError:
        raise ValueError(f"unknown transport {transport!r}, expected one of "
                         f"{sorted(DWELL_SAMPLES)}") from None

    lo = int(nominal * (1 - DWELL_TOLERANCE))
    hi = int(nominal * (1 + DWELL_TOLERANCE)) + 1

    best = (-np.inf, None, None)
    for dwell in range(lo, hi):
        # one padding dwell must sit before the window, and the window must fit
        starts = np.arange(dwell, len(v) - n_measured * dwell + 1)
        if len(starts) == 0:
            continue
        means = [_block_means(cs, starts, i * dwell, (i + 1) * dwell) for i in range(n_measured)]
        prev = _block_means(cs, starts, -dwell, 0)
        score = sum(np.abs(means[i + 1] - means[i]) for i in range(n_measured - 1))
        score = score - np.abs(means[0] - prev)
        j = int(np.argmax(score))
        if score[j] > best[0]:
            best = (float(score[j]), int(starts[j]), dwell)
    if best[1] is None:
        raise ValueError(f"trace too short to contain a sweep ({len(v)} samples)")
    return best[1], best[2], best[0]


def channel_windows(start, dwell, channels):
    return {ch: (start + i * dwell, start + (i + 1) * dwell) for i, ch in enumerate(channels)}


def segment_capture(voltages, channels, *, transport):
    """Return (medians, per-channel samples, (start, dwell, score)).

    `transport` is the run's CONNECTION -- the dwell is a different length in
    samples on each, see DWELL_SAMPLES -- and is keyword-only with no default
    for the reason fit_sweep_grid() gives.
    """
    v = np.asarray(voltages, dtype=float)
    start, dwell, score = fit_sweep_grid(v, n_measured=len(channels),
                                         transport=transport)

    medians, per_channel = {}, {}
    for ch, (lo, hi) in channel_windows(start, dwell, channels).items():
        seg = v[lo + EDGE_TRIM:hi - EDGE_TRIM]
        medians[ch] = float(np.median(seg))
        per_channel[ch] = seg
    return medians, per_channel, (start, dwell, score)
