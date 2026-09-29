"""The "multiple" collection run: every direction measured off one trigger.

COLLECTION_TYPE in configurations.json picks between this and the original
per-transmitter run in measurePhasesMultiThreadedMultiTags.mainMultiWays().
Both drive the same worker processes, produce the same CSV, and are fed by the
same detection and calibration code -- what differs is the shape of a round:

    individual   for each tx tag: tx sweeps, everyone else captures.
                 N rounds per frequency.

    multiple     every tag is loaded with a slotted program first, then one
                 trigger runs all of them: slot k is tag k sweeping and
                 everyone else listening. 1 round per frequency.

So a frequency costs one sweep here instead of N, and the pairs come out of a
single round rather than being stitched together across N of them -- every
reading at a frequency sees the same carrier, the same instant, the same
everything.

The geometry, the sizing and the checks are in sched_collect.py; the firmware
is XIAO-ESP32-C6_firmware_wifi/schedule.cpp.
"""

import os
import time

import numpy as np
import pandas as pd

from ribbn_scripts.processing.mpp_segment import segment_capture
from ribbn_scripts.processing.phase_cal import cal_theta_et_al

import esync_mpp
import exciters
import measurePhasesMultiThreadedMultiTags as mtt
import sched_collect


def tag_order(tags):
    """The slot order, fixed for the whole run.

    Sorted by the number in "TagN" rather than by the string, so Tag10 does
    not land between Tag1 and Tag2. Which tag owns which slot is the only
    thing that says who a listener was listening to, so this has to be the
    same list every round -- reordering it between rounds would relabel every
    Rx/Tx pair with nothing to notice it.
    """
    return sorted(tags, key=lambda t: int(t[3:]))


def _gather_named(res_type, tags, timeout):
    """mtt._gather, keyed by tag name instead of by the bare id."""
    got = mtt._gather(mtt.result_q, res_type, tags, timeout=timeout)
    return {f"Tag{tag_id}": data for tag_id, data in got.items()}


def _load_programs(order, programs, slot_us, listen_samples, timeout=60):
    """Load every tag's program. Also what clears the last round's results."""
    for tag in order:
        mtt.cmd_qs[tag].put(("sched_load", {
            "program": programs[tag],
            "slot_us": slot_us,
            "listen_samples": listen_samples,
            # Raw codes, converted host-side. See ADC_REF_MV in hardware.py:
            # it halves what a 20-tag round puts on the wire and saves the tag
            # formatting a float per sample.
            "raw": True,
        }))
    loaded = _gather_named("sched_loaded", order, timeout=timeout)
    failed = sorted(t for t, ok in loaded.items() if not ok)
    if failed:
        raise Exception(f"could not load the schedule on tag(s) {failed}")


def _trigger_wired(order, slot_us):
    """Release every tag's sgo together and wait out the grid.

    The barrier is reset first because a round that failed may have left
    workers part-way through it, and a broken barrier stays broken: without
    this the next attempt would raise in every worker instead of re-shooting.
    """
    duration = sched_collect.round_duration_s(len(order), slot_us)
    worker_timeout = duration * 2 + 10

    if mtt.sync_barrier_obj is not None:
        mtt.sync_barrier_obj.reset()

    for tag in order:
        mtt.cmd_qs[tag].put(("sched_go", {"timeout": worker_timeout}))

    # sgo answers only once the last boundary has passed, so this gather is
    # the wait for the round. Its own deadline sits past the workers' so a
    # tag that gave up reports why, instead of this timing out first and
    # blaming everyone.
    triggers = _gather_named("sched_trigger", order,
                             timeout=worker_timeout + 10)
    failed = sorted(t for t, v in triggers.items() if v is None)
    if failed:
        raise Exception(f"sgo failed on tag(s) {failed}")

    starts = list(triggers.values())
    skew_ms = (max(starts) - min(starts)) * 1e3
    return min(starts), max(starts) + duration, skew_ms


def _trigger_wireless(order, slot_us, exc):
    """Arm every tag and let the exciter's preamble start the grid.

    Returns (start, stop, reports, waited_s).
    """
    for tag in order:
        mtt.cmd_qs[tag].put("esync_arm")
    armed = _gather_named("esync_armed", order, timeout=30)
    failed = sorted(t for t, ok in armed.items() if not ok)
    if failed:
        raise Exception(f"could not arm esync on tag(s) {failed}")

    # Correlators primed with carrier before the preamble; see
    # sched_collect.ARM_SETTLE_S.
    time.sleep(sched_collect.ARM_SETTLE_S)

    start, stop = exc.sync()
    reports, waited = _await_fires(order, slot_us)
    return start, stop, reports, waited


def _await_fires(order, slot_us):
    """Wait until every tag reports that the preamble fired, or give up.

    Nothing is sent when a scheduled round finishes -- the fire calls
    schedRun() with no session to answer on -- so the host has to ask. What it
    must not do is ask once, after a fixed sleep: exc.sync() queues the
    preamble rather than emitting it, and the delay before it reaches the air
    is set by buffering nobody here can see (see sched_collect.FIRE_POLL_S).

    Asking repeatedly also happens to be exact about when the round is over.
    esyncListening() latches the report and only then calls schedRun(), and
    schedRun() blocks, so a tag cannot answer esyncr with a lock in it until
    the last slot boundary has passed. An answer carrying "rho" therefore says
    both "the preamble fired" and "the traces are in the pool" -- which is why
    nothing sleeps for the round's length afterwards.

    On the deadline the last report from each tag that never fired is kept and
    handed back, because that is what carries peak_rho into check_fired()'s
    message and says which of the ways this fails happened.
    """
    # A tag inside schedRun() is not reading its socket, so every ask has to be
    # allowed to outlast a whole round.
    gather_timeout = sched_collect.round_duration_s(len(order), slot_us) + 30

    # Quiet while the round would be running if the preamble went out at once.
    # Only a slower path than that pays for any polling at all.
    time.sleep(sched_collect.result_deadline_s(len(order), slot_us))

    fired, latest = {}, {}
    started = time.time()
    deadline = started + esync_mpp.FIRE_DEADLINE_S
    while True:
        pending = [t for t in order if t not in fired]
        if not pending:
            return fired, time.time() - started

        for tag in pending:
            mtt.cmd_qs[tag].put("esync_report")
        got = _gather_named("esync_report", pending, timeout=gather_timeout)
        for name, report in got.items():
            report = report or {}
            latest[name] = report
            if "rho" in report:
                fired[name] = report

        if len(fired) == len(order):
            return fired, time.time() - started
        if time.time() >= deadline:
            out = dict(latest)
            out.update(fired)
            return out, time.time() - started
        time.sleep(sched_collect.FIRE_POLL_S)


def _collect_reports(order, timeout):
    """Every tag's sqr, parsed."""
    for tag in order:
        mtt.cmd_qs[tag].put("sched_report")
    reports = _gather_named("sched_result", order, timeout=timeout)
    failed = sorted(t for t, r in reports.items() if r is None)
    if failed:
        # The usual cause over the air is a tag that never heard the preamble:
        # its program is loaded but has never run, so sqr answers "pending":0
        # and the worker raises. check_fired() below says which.
        raise Exception(f"sqr failed on tag(s) {failed}")
    return reports


def _segment_round(pairs, channels):
    """Segment every pair's trace, or fail the whole round.

    Part of the shot for the same reason it is in the individual path (see
    mtt._segmentRound): a trace that arrives intact but holds no recoverable
    sweep is exactly the kind of thing a re-shoot fixes, and failing it here
    makes it another failed shot rather than the end of the run.

    The dwell is taken from the slot that recorded the trace rather than from
    mpp_segment's transport table -- a scheduled listen slot samples at its
    own paced rate, not at the rate a loop()-driven capture manages. See
    sched_collect.dwell_samples_from_slot().
    """
    segments = {}
    for (rx, tx), slot in pairs.items():
        try:
            medians, per_channel, _ = segment_capture(
                slot["trace"], channels,
                dwell_samples=sched_collect.dwell_samples_from_slot(slot))
        except Exception as e:
            raise Exception(f"could not segment Tx {tx} -> Rx {rx} "
                            f"(slot {slot['i']}, {slot['count']} samples): {e}")
        segments[(rx, tx)] = (medians, per_channel)
    return segments


def _round(order, programs, slot_us, listen_samples, exc, transport, channels):
    """One shot: load, trigger, collect, segment."""
    _load_programs(order, programs, slot_us, listen_samples)

    esync_reports = {}
    fire_wait = None
    if transport == mtt.WIRELESS:
        start, stop, esync_reports, fire_wait = _trigger_wireless(
            order, slot_us, exc)
        skew_ms = None
        # Goes before the traces are fetched: a tag that never locked explains
        # itself with peak_rho, which is a far better message than whatever
        # sqr is about to fail with.
        esync_mpp.check_fired(esync_reports)
    else:
        start, stop, skew_ms = _trigger_wired(order, slot_us)

    reports = _collect_reports(order, timeout=sched_collect.REPORT_GATHER_S)
    sched_collect.check_reports(reports, len(order), order)

    pairs = sched_collect.pair_traces(reports, order)
    segments = _segment_round(pairs, channels)
    return (reports, esync_reports, pairs, segments, start, stop, skew_ms,
            fire_wait)


def run_round(order, programs, slot_us, listen_samples, exc, transport,
              channels):
    """One round, re-shot if any part of it is lost.

    Nothing from a failed shot is kept, and leftover answers are drained first
    or the next _gather() reads them as its own -- the same discipline as
    mtt.MPPMultiWaysEsync(), for the same reason: in this mode a half-kept
    round is not a missing reading, it is N-1 readings labelled with the wrong
    frequency.
    """
    last = None
    for attempt in range(1, sched_collect.MAX_ROUND_ATTEMPTS + 1):
        if attempt > 1:
            mtt._drainResults(mtt.result_q)
        try:
            return _round(order, programs, slot_us, listen_samples, exc,
                          transport, channels)
        except Exception as e:
            last = e
            print(f"  round: attempt {attempt}/"
                  f"{sched_collect.MAX_ROUND_ATTEMPTS} failed, re-shooting in "
                  f"{sched_collect.RETRY_BACKOFF_S:g}s: {e}")
            if attempt < sched_collect.MAX_ROUND_ATTEMPTS:
                time.sleep(sched_collect.RETRY_BACKOFF_S)

    raise Exception(
        f"scheduled round failed on all {sched_collect.MAX_ROUND_ATTEMPTS} "
        f"attempts. A failure this consistent is the setup, not the shot. "
        f"Last: {last}")


def mainMultiple(num_exp_runs, exp_name, save_path,
                 hw_config,
                 tag_mac_mapping,
                 freq_range,
                 exciter_type=None, mpp_repetitions=1,
                 inter_MPP_batch_sleep_time=0.1,
                 channels=(1, 3, 4, 6, 7, 8),
                 exc_power=mtt.EXC_POWER,
                 transport=mtt.WIRED,
                 exciter_settings=None,
                 slot_us=None,
                 listen_samples=None):
    """The "multiple" counterpart to mtt.mainMultiWays().

    Same arguments, same CSV, same failure behaviour -- a run that dies part
    way still writes what it had and shuts the exciter down. `slot_us` and
    `listen_samples` are the two knobs this mode adds; both default to what
    sched_collect works out from the tag count.
    """
    channels = list(channels)
    order = tag_order(mtt.cmd_qs)
    n_slots = len(order)
    if n_slots < 2:
        raise Exception(f"Need at least two tags, got {n_slots}")

    programs = sched_collect.build_programs(order)
    if slot_us is None:
        slot_us = sched_collect.DEFAULT_SLOT_US
    if listen_samples is None:
        listen_samples = sched_collect.listen_samples_for(n_slots, slot_us)

    print(f"Collection: multiple -- {n_slots} slots of {slot_us} us, "
          f"{listen_samples} samples per listen slot, "
          f"{sched_collect.round_duration_s(n_slots, slot_us) * 1e3:.0f} ms "
          f"per round")
    print(f"Slot order: {' '.join(order)}")

    if transport == mtt.WIRELESS:
        # Same exclusion as the individual path: these points jam the tags'
        # own WiFi over the air, so a wireless run cannot reach them at all.
        jammed = [f for f in freq_range if esync_mpp.wifi_jammed(f)]
        if jammed:
            freq_range = [f for f in freq_range
                          if not esync_mpp.wifi_jammed(f)]
            lo, hi = esync_mpp.WIFI_JAMMED_MHZ
            print(f"skipping {jammed} MHz: {lo:g}-{hi:g} MHz jams the tags' "
                  f"WiFi over the air, so a wireless run cannot reach them. "
                  f"Run them with CONNECTION: wired.")
        if not freq_range:
            raise Exception(
                f"every frequency asked for is inside "
                f"esync_mpp.WIFI_JAMMED_MHZ {esync_mpp.WIFI_JAMMED_MHZ}, so a "
                f"wireless run has nothing left to sweep. Use CONNECTION: "
                f"wired for these, or fit a low-pass filter and clear the "
                f"band.")

    exciter_type = exciters.normalize(exciter_type)
    exc = exciters.make_exciter(exciter_type, **(exciter_settings or {}))
    if transport == mtt.WIRELESS and not hasattr(exc, "sync"):
        exciters.shutdown(exc)
        raise Exception(
            f"a wireless run is synced by the exciter's ASK preamble, which "
            f"EXCITER={exciter_type!r} cannot send. Set EXCITER to \"bladerf\" "
            f"(and EXC_POWER to a gain, ~60), or run CONNECTION: wired.")
    if exc is not None:
        exc.set_freq(915)
        exc.set_pwr(exc_power)
        time.sleep(0.1)

    csv_path = (f"{save_path}/{exp_name}"
                f"_{freq_range[0]:g}-{freq_range[-1]:g}MHz"
                f"_{num_exp_runs}runs"
                f"_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    print(f"Writing results to: {csv_path}")

    columns = ["Rx", "Tx", "MPP Start Time (s)", "MPP Stop Time (s)",
               "Voltages (mV)", "Frequency (MHz)", "Run Exp Num",
               "MPP Repetition", "Unidirectional Phase (deg)",
               "Unidirectional V", "Unidirectional beta", "Time Taken (s)",
               "Connection",
               # Which shape of round produced this row. The traces are not
               # interchangeable between the two -- a scheduled listen slot
               # samples at its own rate -- so re-segmenting later needs it,
               # the same way "Connection" is needed.
               "Collection Type",
               # And the rate itself, because "Collection Type" alone is not
               # enough to re-segment by. mpp_segment's DWELL_SAMPLES describes
               # the loop()-driven rdb capture an individual round uses (90
               # wired, 45 wireless); a scheduled listen slot is a paced loop
               # that owns the CPU and runs at a different rate entirely, so a
               # reader that reached for the transport table would search for a
               # 90-sample dwell in a trace whose dwell is 120 and place every
               # window short. These two carry what the fit actually needs, as
               # the firmware measured it (count over dur_us in sqr), so
               # post_processing.py never has to infer it.
               "Sample Rate (Hz)",
               "Dwell (samples)",
               "Esync Rho", "Esync SNR (dB)"]
    for ch in channels:
        columns.append(f"Channel_{ch}_voltages")
        columns.append(f"Channel_{ch}_median")

    t_start = time.time()
    premature_stop = 0
    for run_exp_num in range(num_exp_runs):
        print(f"MPP Batch {run_exp_num}/{num_exp_runs}")

        if exc is not None:
            exc.set_pwr(exc_power)

        DF = pd.DataFrame(columns=columns)
        DF_SNAPSHOP = DF

        try:
            for freq in freq_range:
                if exc is not None:
                    exc.set_freq(freq)
                    time.sleep(mtt.EXC_SETTLE_S)

                for rep in range(mpp_repetitions):
                    rep_start = time.time()
                    (reports, esync_reports, pairs, segments,
                     mpp_start_time, mpp_stop_time, skew_ms,
                     fire_wait) = run_round(
                        order, programs, slot_us, listen_samples, exc,
                        transport, channels)
                    rep_end = time.time()

                    summary = sched_collect.slot_summary(reports)
                    if transport == mtt.WIRELESS:
                        summary += f" | {esync_mpp.sync_summary(esync_reports)}"
                        if fire_wait is not None:
                            # How long after exc.sync() every tag had fired.
                            # Mostly the exciter's buffering, so it is worth
                            # seeing rather than being absorbed by a sleep.
                            summary += f" | fired after {fire_wait:.2f}s"
                    elif skew_ms is not None:
                        summary += f" | trigger skew {skew_ms:.1f} ms"
                    print(f"  round: {summary}")
                    for warning in sched_collect.check_rate(reports):
                        print(f"  ⚠️  {warning}")

                    for (rx_tag, tx_tag), slot in sorted(pairs.items()):
                        channel_median, channels_voltages = segments[(rx_tag, tx_tag)]
                        phase_theta, V, beta = cal_theta_et_al(
                            channel_median,
                            rxName=tag_mac_mapping[rx_tag],
                            txName=tag_mac_mapping[tx_tag],
                            cfg=hw_config,
                            freq=freq * 1e6,  # in hz
                        )

                        print(f"Tx {tx_tag} -> Rx {rx_tag} | run "
                              f"{run_exp_num}/{num_exp_runs} rep "
                              f"{rep + 1}/{mpp_repetitions} | {freq} MHz | "
                              f"Phase Deg {phase_theta}")

                        # The slot's own place on the grid, which is what
                        # "when did this Tx sweep" means here -- there is no
                        # per-pair host timing in this mode, because there is
                        # no per-pair command.
                        slot_start = mpp_start_time + slot["t_us"] / 1e6
                        # The span of the TRACE, not of the sweep inside it.
                        # post_processing scales its time axis by
                        # (stop - start) / len(trace), so handing it the sweep
                        # length would draw a 30 ms trace as if it were 24 ms.
                        rate = slot.get("rate_hz") or (1e6 / sched_collect.SAMPLE_US)
                        trace_s = len(np.asarray(slot["trace"])) / rate
                        entry = {
                            "Rx": rx_tag,
                            "Tx": tx_tag,
                            "MPP Start Time (s)": slot_start,
                            "MPP Stop Time (s)": slot_start + trace_s,
                            # .tolist() so the CSV gets every sample: str() of
                            # a >1000-element ndarray writes numpy's summarised
                            # "[1.0 2.0 ... 9.0]" repr and loses the trace
                            "Voltages (mV)": np.asarray(slot["trace"]).tolist(),
                            "Frequency (MHz)": freq,
                            "Run Exp Num": run_exp_num,
                            "MPP Repetition": rep + 1,
                            "Time Taken (s)": rep_end - rep_start,
                            "Unidirectional Phase (deg)": phase_theta,
                            "Unidirectional V": V,
                            "Unidirectional beta": beta,
                            "Connection": transport,
                            "Collection Type": mtt.MULTIPLE,
                            "Sample Rate (Hz)": slot.get("rate_hz"),
                            "Dwell (samples)":
                                sched_collect.dwell_samples_from_slot(slot),
                        }
                        rx_report = esync_reports.get(rx_tag, {}) or {}
                        entry["Esync Rho"] = rx_report.get("rho")
                        entry["Esync SNR (dB)"] = rx_report.get("snr_db")
                        for ch in channels:
                            entry[f"Channel_{ch}_voltages"] = channels_voltages[ch].tolist()
                            entry[f"Channel_{ch}_median"] = channel_median[ch]

                        DF = pd.concat([DF, pd.DataFrame([entry])],
                                       ignore_index=True)

                DF_SNAPSHOP = DF

        except Exception as e:
            print(DF_SNAPSHOP)
            print("Had to stop script prematurely. Had the following exception: ", e)
            premature_stop = 1
            # Same reasoning as mainMultiWays(): some failures leave the
            # carrier up and, for a networked exciter, a control link the next
            # run cannot take.
            exciters.shutdown(exc)
            raise e

        write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        DF_SNAPSHOP.to_csv(csv_path, mode="a", header=write_header, index=False)
        print(f"Appended run {run_exp_num} to {csv_path}")
        time.sleep(inter_MPP_batch_sleep_time)

    time_taken = time.time() - t_start
    print(f"Time taken: {time_taken} for {num_exp_runs} runs over "
          f"{len(freq_range)} freqs")

    exciters.shutdown(exc)

    return premature_stop
