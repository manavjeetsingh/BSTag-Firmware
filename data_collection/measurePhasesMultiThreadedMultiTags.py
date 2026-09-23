import pandas as pd
from ribbn_scripts.hardware_api.hardware import Tag
import numpy as np
import time
import os
import matplotlib.pyplot as plt
import multiprocessing
import queue as queue_mod
from ribbn_scripts.processing.phase_cal import cal_theta_et_al
from ribbn_scripts.processing.mpp_segment import segment_capture, channel_windows
import esync_mpp
import exciters


COMMAND_RESULT_TYPE = {
    "get_mac": "mac",
    "perform_mpp": "mpp_times",
    "stop_reading": "voltage_readings",
    "get_adc_val": "adc_vals",
    "esync_stage_mpp": "esync_staged",
    "esync_stage_capture": "esync_staged",
    "esync_arm": "esync_armed",
    "esync_collect_rx": "esync_result",
    "esync_collect_tx": "esync_result",
    "esync_reset": "esync_reset",
}

WIRED = "wired"
WIRELESS = "wireless"


def connect_tag(endpoint, transport):
    """Open one tag over whichever transport the run is using.

    `endpoint` is a serial port for WIRED and an IP for WIRELESS. Nothing
    past this function knows the difference -- the two transports speak the
    same command protocol, so the only thing that varies is which set of
    methods (foo / foo_wifi) is bound below.
    """
    if transport == WIRELESS:
        return Tag.over_wifi(endpoint)
    if transport == WIRED:
        return Tag(endpoint)
    raise ValueError(f"unknown transport {transport!r}, expected "
                     f"{WIRED!r} or {WIRELESS!r}")


def tag_ops(tag_instance, transport):
    """Bind the logical operations to this transport's methods."""
    if transport == WIRELESS:
        return {
            "get_mac": tag_instance.get_mac_wifi,
            "begin_reading": tag_instance.begin_reading_wifi,
            "perform_mpp": tag_instance.perform_mpp_wifi,
            "stop_reading": tag_instance.stop_reading_wifi,
            "get_adc_val": tag_instance.get_adc_val_wifi,
            "reflect": tag_instance.reflect_wifi,
            "disconnect": tag_instance.disconnect_wifi,
            # esync: staged here, fired by the exciter's preamble, collected
            # over the same session. Wired runs don't need it.
            "stage_mpp": lambda: tag_instance.queue_mpp_wifi(1),
            "stage_capture": tag_instance.queue_capture_wifi,
            "arm_esync": tag_instance.listen_esync_wifi,
            "collect_esync": lambda want_trace: _collect_esync(
                tag_instance, want_trace),
            "reset_esync": lambda: _reset_esync(tag_instance),
        }
    return {
        "get_mac": tag_instance.get_mac,
        "begin_reading": tag_instance.begin_reading,
        "perform_mpp": tag_instance.perform_mpp,
        "stop_reading": tag_instance.stop_reading,
        "get_adc_val": tag_instance.get_adc_val,
        "reflect": tag_instance.reflect,
        "disconnect": tag_instance.disconnect,
    }


def _reset_esync(tag_instance):
    """Disarm and clear the staged command, left over from a run that died
    mid-round -- or the next round fires the previous run's command.
    Disarm first, so a sync can't fire the slot in between."""
    tag_instance.stop_esync_wifi()
    tag_instance.clear_queued_command_wifi()


def _collect_esync(tag_instance, want_trace):
    """Pick up what the preamble fired, over the session that stayed up.

    Waiting for the fired reply is the wait for the sync: an Rx tag staged rdb
    and answers the plain line "rdb", the Tx tag staged mpp_1 and answers its
    JSON. The trace then comes over the same socket with rds, shaped exactly
    like a wired capture.

    A window that hears no preamble never answers, so the wait is bounded. On
    a timeout the tag is disarmed and its report fetched anyway -- that report
    is what carries peak_rho into check_fired()'s message -- and the round is
    failed by the "queued": None below.
    """
    ack = "rdb" if want_trace else None
    try:
        queued = tag_instance.await_fired_wifi(
            ack=ack, timeout=esync_mpp.FIRE_DEADLINE_S)
    except Exception:
        _reset_esync(tag_instance)
        return {"queued": None,
                "report": tag_instance.esync_report_wifi(),
                "trace": None}
    report = tag_instance.esync_report_wifi()
    trace = tag_instance.stop_reading_wifi() if want_trace else None
    return {"queued": queued, "report": report, "trace": trace}


def device_worker(endpoint, tag_id, command_queue, result_queue, transport=WIRED):
    """
    A worker function to be run in a separate PROCESS. It instantiates its
    own Tag object to avoid sharing non-serializable objects.

    `endpoint` is a COM/tty port under WIRED and an IP address under
    WIRELESS; a wireless run has no serial cable attached at all.
    """
    print(f"Process for Tag {tag_id} on {endpoint} ({transport}) started.")
    # Each process creates its own instance of the Tag class -- and, over
    # wifi, its own TCP session, so each one takes a session slot of its own
    # (MAX_TCP_CLIENTS in the firmware's config.h).
    tag_instance = connect_tag(endpoint, transport)
    ops = tag_ops(tag_instance, transport)

    while True:
        command = command_queue.get()

        if command == "STOP":
            ops["disconnect"]()
            print(f"Process for Tag {tag_id} stopping.")
            break

        try:
            if command == "get_mac":
                result = ops["get_mac"]()
                result_queue.put((tag_id, "mac", result))
            elif command == "begin_reading":
                ops["begin_reading"]()
            elif command == "perform_mpp":
                result = ops["perform_mpp"]()
                result_queue.put((tag_id, "mpp_times", result))
            elif command == "stop_reading":
                result = ops["stop_reading"]()
                result_queue.put((tag_id, "voltage_readings", result))
            elif command == "get_adc_val":
                result = ops["get_adc_val"]()
                result_queue.put((tag_id, "adc_vals", result))
            elif command == "esync_stage_mpp":
                ops["stage_mpp"]()
                result_queue.put((tag_id, "esync_staged", True))
            elif command == "esync_stage_capture":
                ops["stage_capture"]()
                result_queue.put((tag_id, "esync_staged", True))
            elif command == "esync_arm":
                ops["arm_esync"]()
                result_queue.put((tag_id, "esync_armed", True))
            elif command == "esync_reset":
                # Wireless only; answer either way so the barrier completes.
                reset = ops.get("reset_esync")
                if reset is not None:
                    reset()
                result_queue.put((tag_id, "esync_reset", True))
            elif command == "esync_collect_rx":
                result_queue.put((tag_id, "esync_result", ops["collect_esync"](True)))
            elif command == "esync_collect_tx":
                result_queue.put((tag_id, "esync_result", ops["collect_esync"](False)))
            elif command[:2]=='ch':
                ops["reflect"](int(command[3:]))

        except Exception as e:
            print(f"🛑 ERROR in process for Tag {tag_id} ({endpoint}): {e}")
            # Whoever sent `command` is blocked waiting on a matching
            # result_queue entry (see MPPMultiWays) -- without this they'd
            # wait forever. None marks the reading as failed/missing.
            result_type = COMMAND_RESULT_TYPE.get(command)
            if result_type:
                result_queue.put((tag_id, result_type, None))

SLEEPTIME=0.1
READTIME=5

# CAPTURE_CHANNEL in the firmware's config.h - rdb forces the tag here anyway,
# so parking the receivers on it first means that switch is a no-op and the
# rectifier is already settled (tau ~ 2.5 ms) when sampling starts
CAPTURE_CHANNEL=2
RX_SETTLE_S=0.02
EXC_SETTLE_S=0.1

# Default Settings
INBUILT_REPETITIONS=1
# FREQ_RANGE=[915]
FREQ_RANGE=list(range(775,1005,10))

# # Setting up the exciter
# Default only -- callers normally pass exc_power down from configurations.json
# (EXC_POWER there). The unit is the exciter's own: dBm on the rf_gen, TX gain
# in dB on the bladeRF, which has no calibrated output level to ask for.
EXC_POWER=12.9


# # Connecting to Tags
# # TAG1_COM="/dev/tty.usbserial-2130"
# TAG1_COM="COM5" # v32-4
# # TAG2_COM="/dev/tty.usbserial-2120"
# TAG2_COM="COM3" #v32-5
# TAG3_COM="COM4" #v31-2
# TAG4_COM="COM7" #v31-1 
# TAG5_COM="COM6" #v32-3


# FOLDER_PATH="C:/git/T2TExperiments/DistExperiments/EstimatingDistNCS330Feb2_1"
FOLDER_PATH=os.getcwd()

cmd_qs = {}
result_q = None 
processes = {}

def initialize(tag_endpoint_mapping, transport=WIRED):
    """Spawn one worker process per tag.

    `tag_endpoint_mapping` is {"TagN": endpoint}: serial ports under WIRED,
    IP addresses under WIRELESS.
    """
    global cmd_qs, result_q, processes
    n_tags = len(tag_endpoint_mapping)
    result_q = multiprocessing.Queue()
    
    # Create queues from the multiprocessing module
    for tag in tag_endpoint_mapping.keys():
        local_queue = multiprocessing.Queue()
        cmd_qs[tag]= local_queue
        processes[tag] = multiprocessing.Process(target=device_worker, args=(tag_endpoint_mapping[tag], int(tag[3:]), local_queue, result_q, transport), daemon=True)
    

    # Start the child processes
    for p in processes.values():
        p.start()
    

# Re-shoots per wired MPP round before the run gives up. A capture that comes
# back cut short (hardware.TruncatedReply) is worth another shot; one that fails
# every time is the cable or the tag, and more tries won't tell us anything new.
MAX_WIRED_ROUND_ATTEMPTS = 3


def MPPMultiWays(rx_tags:list, cmdq_tx, result_q):
    """One MPP round over serial, re-shot if any part of it is lost.

    A capture cut short -- hardware.py fails those fast rather than sitting out
    its 60 s timeout -- or any other worker error answers None, and that capture
    cannot be asked for again: the sweep is over and the buffer is gone. So the
    whole round is re-shot, the way the wireless path does it
    (MPPMultiWaysEsync), rather than carrying a hole into the results.
    """
    last = None
    for attempt in range(1, MAX_WIRED_ROUND_ATTEMPTS + 1):
        if attempt > 1:
            # Answers from the failed shot would otherwise be read as this
            # one's (same reasoning as MPPMultiWaysEsync).
            _drainResults(result_q)
        try:
            return _mppRound(rx_tags, cmdq_tx, result_q)
        except Exception as e:
            last = e
            print(f"  mpp: attempt {attempt}/{MAX_WIRED_ROUND_ATTEMPTS} "
                  f"failed, re-shooting: {e}")

    raise Exception(f"MPP round failed on all {MAX_WIRED_ROUND_ATTEMPTS} "
                    f"attempts. Last: {last}")


def _mppRound(rx_tags, cmdq_tx, result_q):
    """One shot: start the receivers capturing, sweep, collect."""
    global cmd_qs

    for rx_tag in rx_tags:
        cmd_qs[rx_tag].put("begin_reading")
    cmdq_tx.put("perform_mpp")
    mpp_done = False
    mpp_start_time=None
    mpp_stop_time=None
    while not mpp_done:
        tag_id, res_type, data = result_q.get()
        if res_type == "mpp_times":
            if data is not None:
                mpp_start_time, mpp_stop_time = data
            mpp_done = True
    if mpp_start_time is None:
        # The tx tag never swept, so whatever the receivers captured is of
        # nothing. Fail the round instead of segmenting a flat trace.
        raise Exception("perform_mpp failed on the tx tag")
    for rx_tag in rx_tags:
        cmd_qs[rx_tag].put("stop_reading")
    voltage_readings = {}
    while len(voltage_readings) < len(rx_tags):
        tag_id, res_type, data = result_q.get()
        if res_type == "voltage_readings":
            voltage_readings[tag_id] = data
    failed = [t for t, v in voltage_readings.items() if v is None]
    if failed:
        raise Exception(f"capture failed on tag(s) {sorted(failed)}")
    # print("MPP DONE WITH TAGS:",len(voltage_readings))
    return voltage_readings, mpp_start_time, mpp_stop_time


def _gather(result_q, res_type, tags, timeout):
    """Block until every tag in `tags` has answered with `res_type`.

    Answers of other types are stale ones from an abandoned round and are
    dropped. A worker that raised answers None (see device_worker).
    """
    want = {int(t[3:]) for t in tags}
    got = {}
    deadline = time.time() + timeout
    while len(got) < len(want):
        remaining = deadline - time.time()
        if remaining <= 0:
            missing = sorted(want - set(got))
            raise Exception(
                f"timed out after {timeout}s waiting for {res_type} from "
                f"tag(s) {missing}")
        try:
            tag_id, rt, data = result_q.get(timeout=remaining)
        except queue_mod.Empty:
            continue
        if rt == res_type and tag_id in want:
            got[tag_id] = data
    return got


def MPPMultiWaysEsync(rx_tags, tx_tag, exc, result_q):
    """One MPP round started by the exciter's preamble instead of the host.

    Any failure drops the whole round and re-shoots it, up to
    MAX_ROUND_ATTEMPTS; nothing from a failed shot is kept. Leftover answers
    from the failed shot are drained first, or the next _gather() would read
    them as its own.
    """
    last = None
    for attempt in range(1, esync_mpp.MAX_ROUND_ATTEMPTS + 1):
        if attempt > 1:
            _drainResults(result_q)
        try:
            return _esyncRound(rx_tags, tx_tag, exc, result_q)
        except Exception as e:
            last = e
            print(f"  sync: attempt {attempt}/{esync_mpp.MAX_ROUND_ATTEMPTS} "
                  f"failed, re-shooting: {e}")

    raise Exception(
        f"esync round failed on all {esync_mpp.MAX_ROUND_ATTEMPTS} attempts. "
        f"A failure this consistent is the setup, not the shot -- see peak_rho "
        f"in esync_mpp.check_fired(). Last: {last}")


def _drainResults(result_q):
    while True:
        try:
            result_q.get_nowait()
        except Exception:
            return


def _esyncRound(rx_tags, tx_tag, exc, result_q):
    """One shot: stage, arm, sync, collect.

    Everything is staged before anything is armed: an armed tag can lock on
    the next preamble it hears, and a lock with an empty slot fires nothing.
    """
    all_tags = [tx_tag] + list(rx_tags)

    cmd_qs[tx_tag].put("esync_stage_mpp")
    for rx_tag in rx_tags:
        cmd_qs[rx_tag].put("esync_stage_capture")
    _gather(result_q, "esync_staged", all_tags, timeout=30)

    for tag in all_tags:
        cmd_qs[tag].put("esync_arm")
    _gather(result_q, "esync_armed", all_tags, timeout=30)

    # Correlators primed with carrier before the preamble, and the collects
    # posted before it too, so every tag is already reading its socket when
    # its fired reply lands there.
    time.sleep(esync_mpp.ARM_SETTLE_S)

    cmd_qs[tx_tag].put("esync_collect_tx")
    for rx_tag in rx_tags:
        cmd_qs[rx_tag].put("esync_collect_rx")

    mpp_start_time, mpp_stop_time = exc.sync()

    results = _gather(result_q, "esync_result", all_tags,
                      timeout=esync_mpp.FIRE_DEADLINE_S + 60)
    failed = [t for t, r in results.items() if r is None]
    if failed:
        raise Exception(f"esync collect failed on tag(s) {sorted(failed)}")

    reports = {f"Tag{tag_id}": r["report"] for tag_id, r in results.items()}
    esync_mpp.check_fired(reports)

    # check_fired() goes first: a tag that never locked explains itself with
    # peak_rho. Reaching here means it locked but its reply never arrived.
    no_reply = [t for t, r in results.items() if r["queued"] is None]
    if no_reply:
        raise Exception(f"locked but no fired reply from tag(s) "
                        f"{sorted(no_reply)}")

    voltage_readings = {tag_id: r["trace"]
                        for tag_id, r in results.items() if r["trace"] is not None}
    return voltage_readings, mpp_start_time, mpp_stop_time, reports


def getChannelVoltage(voltage_readings, mpp_stop_time, mpp_start_time, channels,plotting=False):
    """Segment a capture into its per-channel dwells.

    mpp_stop_time/mpp_start_time are kept for the caller's bookkeeping but are
    deliberately not used to place the boundaries -- see mpp_segment for why
    host wall-clock timing mislabels ~half of all captures.
    """
    plot_voltage=np.asarray(voltage_readings)
    channel_medians,channel_voltages,(start,dwell,score)=segment_capture(plot_voltage, channels)

    if plotting:
        plt.figure(figsize=(20,10))
        plt.plot(plot_voltage, '.')
        for i in range(len(channels)+1):
            plt.axvline(x=start+i*dwell, color='b')
        for ch,(left,right) in channel_windows(start,dwell,channels).items():
            print(f"  ch{ch} [{left}, {right}): {len(channel_voltages[ch])} readings: {channel_medians[ch]}")
            plt.hlines(channel_medians[ch],left,right,color='r',zorder=3)
        plt.xlabel("Sample")
        plt.ylabel("ADC out [mV]")
        plt.show()

    return channel_medians,channel_voltages


def mainMultiWays(num_exp_runs, exp_name, save_path, 
                  hw_config,
                  tag_mac_mapping,
                  freq_range=FREQ_RANGE,     
                  exciter_type=None, mpp_repetitions=1, 
                  inter_MPP_batch_sleep_time=0.1,
                  channels=[1,3,4,6,7,8],
                  exc_power=EXC_POWER,
                  transport=WIRED,
                  exciter_settings=None):
    global cmd_qs, result_q, processes

    exciter_type = exciters.normalize(exciter_type)
    exc = exciters.make_exciter(exciter_type, **(exciter_settings or {}))
    if transport == WIRELESS and not hasattr(exc, "sync"):
        # Over WiFi the host can't start the tags together within a 3 ms
        # dwell; the exciter's ASK preamble does, and only the bladeRF can
        # key one.
        exciters.shutdown(exc)
        raise Exception(
            f"a wireless run is synced by the exciter's ASK preamble, which "
            f"EXCITER={exciter_type!r} cannot send. Set EXCITER to \"bladerf\" "
            f"(and EXC_POWER to a gain, ~60), or run CONNECTION: wired.")
    if exc is not None:
        exc.set_freq(915)
        exc.set_pwr(exc_power)
        time.sleep(0.1)

    csv_path=(f"{save_path}/{exp_name}"
              f"_{freq_range[0]:g}-{freq_range[-1]:g}MHz"
              f"_{num_exp_runs}runs"
              f"_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    print(f"Writing results to: {csv_path}")

    t_start=time.time()
    for run_exp_num in range(num_exp_runs):
        print(f"MPP Batch {run_exp_num}/{num_exp_runs}")
    
        if exc is not None:
            exc.set_pwr(exc_power)

        
        premature_stop=0
        premature_stop_error=""
        FREQS_DONE=[]
        columns=["Rx","Tx", "MPP Start Time (s)",
                                "MPP Stop Time (s)","Voltages (mV)",
                                    "Frequency (MHz)", "Run Exp Num", "MPP Repetition", "Unidirectional Phase (deg)",
                                    "Unidirectional V", "Unidirectional beta",
                                    "Time Taken (s)"]
        # Wireless only: how well each tag locked on the preamble.
        columns += ["Esync Rho", "Esync SNR (dB)"]
        for ch in channels:
            columns.append(f"Channel_{ch}_voltages")
            columns.append(f"Channel_{ch}_median")
        DF=pd.DataFrame(columns=columns)
        DF_SNAPSHOP=DF

        try:
            for freq in freq_range:
                if exc is not None:
                    exc.set_freq(freq)
                    time.sleep(EXC_SETTLE_S)

                # print("FREQ:",freq)
                
                for tx_tag in cmd_qs:
                    tx_queue = cmd_qs[tx_tag]
                    rx_tags = []
                    for prob_rx_tag in cmd_qs:
                        if prob_rx_tag==tx_tag:
                            continue
                        rx_tags.append(prob_rx_tag)
                        
                    
                    # print([tx_tag, rx_tags])
                    for rx_tag in rx_tags:
                        cmd_qs[rx_tag].put(f"ch_{CAPTURE_CHANNEL}")
                    time.sleep(RX_SETTLE_S)

                    for rep in range(mpp_repetitions):
                        # print(f"Rep: {rep}")
                        rep_start=time.time()
                        esync_reports={}
                        if transport == WIRELESS:
                            (voltage_readings_1, mpp_start_time_1,
                             mpp_stop_time_1, esync_reports)=MPPMultiWaysEsync(
                                rx_tags=rx_tags, tx_tag=tx_tag,
                                exc=exc, result_q=result_q)
                            print(f"  sync: {esync_mpp.sync_summary(esync_reports)}")
                        else:
                            voltage_readings_1, mpp_start_time_1, mpp_stop_time_1=MPPMultiWays(rx_tags=rx_tags, cmdq_tx=tx_queue, result_q=result_q)
                        rep_end=time.time()
                        for rx_tag in rx_tags:
                            _voltages=voltage_readings_1[int(rx_tag[3:])]
                            
                            # MPP processing and phase calculation
                            channel_median, channels_voltages=getChannelVoltage(_voltages, mpp_stop_time_1, mpp_start_time_1, channels=channels, plotting=False)
                            phase_theta,V,beta=cal_theta_et_al(channel_median, rxName=tag_mac_mapping[rx_tag], txName=tag_mac_mapping[tx_tag],
                            cfg=hw_config,
                            freq=freq*1e6, #in hz
                            )
                            
                            print(f"Tx {tx_tag} -> Rx {rx_tag} | run {run_exp_num}/{num_exp_runs} rep {rep+1}/{mpp_repetitions} | {freq} MHz | Phase Deg {phase_theta}")
                            
                            entry={
                                "Rx":rx_tag, 
                                "Tx":tx_tag, 
                                "MPP Start Time (s)":mpp_start_time_1, 
                                "MPP Stop Time (s)":mpp_stop_time_1, 
                                # .tolist() so the CSV gets every sample: str() of a
                                # >1000-element ndarray writes numpy's summarised
                                # "[1.0 2.0 ... 9.0]" repr and loses the trace
                                "Voltages (mV)":_voltages.tolist(),
                                "Frequency (MHz)":freq, 
                                "Run Exp Num":run_exp_num,
                                "MPP Repetition": rep+1,
                                "Time Taken(s)":rep_end-rep_start,
                                "Unidirectional Phase (deg)":phase_theta,
                                "Unidirectional V":V,
                                "Unidirectional beta": beta,
                                
                            }
                            rx_report=esync_reports.get(rx_tag, {})
                            entry["Esync Rho"]=rx_report.get("rho")
                            entry["Esync SNR (dB)"]=rx_report.get("snr_db")
                            for ch in channels:
                                entry[f"Channel_{ch}_voltages"]=channels_voltages[ch].tolist()
                                entry[f"Channel_{ch}_median"]=channel_median[ch]
                                
                            DF=pd.concat([DF,pd.DataFrame([entry])],ignore_index=True)
                            
                        
                        

                FREQS_DONE.append(freq)
                DF_SNAPSHOP=DF
                
        
        except Exception as e:
            # Even if there is some error while running the experiments, 
            print(DF_SNAPSHOP)
            print("Had to stop script prematurely. Had the following exception: ",e)
            premature_stop=1
            premature_stop_error=e
            # Every failure inside a run comes through here, and some of
            # them (a tag that never fired) leave the carrier up and, for a
            # networked exciter, a control link the next run cannot take.
            exciters.shutdown(exc)
            raise e

        write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        DF_SNAPSHOP.to_csv(csv_path, mode="a", header=write_header, index=False)
        print(f"Appended run {run_exp_num} to {csv_path}")
        time.sleep(inter_MPP_batch_sleep_time)



    time_taken=time.time()-t_start

    print(f"Time taken: {time_taken} for {num_exp_runs} runs over {len(freq_range)} freqs")
    
    exciters.shutdown(exc)
        
    return premature_stop




def test(tags, exciter_type, exc_power=EXC_POWER, exciter_settings=None):
    global cmd_qs, processes, result_q
    
    # Unsynced: just checks the tags answer and the carrier reaches them.
    exc = exciters.make_exciter(exciter_type, **(exciter_settings or {}))
    if exc is not None:
        exc.set_pwr(exc_power)
        exc.set_freq(915)
    
    # Pre-testing tags
    print("TESTING")
    
    # --- Get MAC addresses ---
    # Just a redundant check
    print("\nRequesting MAC addresses from tags...")
    for t in tags:
        cmd_qs[t].put("get_mac")
    
    mac_results = {}
    while len(mac_results) < len(tags):
        tag_id, res_type, data = result_q.get()
        if res_type == 'mac':
            print(f"✅ Main process received: MAC for Tag {tag_id} is {data}")
            mac_results[tag_id] = data

    time.sleep(SLEEPTIME)
    
    print("Changing phase to 1.")
    for t in tags:
        cmd_qs[t].put("ch_1")

    time.sleep(SLEEPTIME)
    
    for t in tags:
        cmd_qs[t].put("get_adc_val")


    adc_results = {}
    while len(adc_results) < len(tags):
        tag_id, res_type, data = result_q.get()
        if res_type == 'adc_vals':
            print(f"✅ ADC val received for tag {tag_id} is {np.median(data)}")
            adc_results[tag_id] = data

    time.sleep(SLEEPTIME)

    print("Changing phase to 2.")
    for t in tags:
        cmd_qs[t].put("ch_2")

    time.sleep(SLEEPTIME)
    
    for t in tags:
        cmd_qs[t].put("get_adc_val")


    adc_results = {}
    while len(adc_results) < len(tags):
        tag_id, res_type, data = result_q.get()
        if res_type == 'adc_vals':
            print(f"✅ ADC val received for tag {tag_id} is {np.median(data)}")
            adc_results[tag_id] = data

    exciters.shutdown(exc)

    # Clear whatever a previous, crashed run left armed or staged.
    resetEsyncState(tags)

    return {"Test": "done"}


def resetEsyncState(tags, timeout=30):
    """Disarm and clear the staged command on every tag. Failures are only
    reported: a tag that can't be reset fails the staging barrier anyway."""
    targets = [t for t in tags if t in cmd_qs]
    for tag in targets:
        cmd_qs[tag].put("esync_reset")
    try:
        got = _gather(result_q, "esync_reset", targets, timeout=timeout)
    except Exception as e:
        print(f"⚠️  could not clear esync state: {e}")
        return
    failed = sorted(t for t, ok in got.items() if not ok)
    if failed:
        print(f"⚠️  could not clear esync state on tag(s) {failed}")
