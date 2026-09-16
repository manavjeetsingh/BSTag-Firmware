import serial
import pandas as pd
from ribbn_scripts.hardware_api.hardware import Tag,Exciter
import numpy as np
import time
import pickle
import os
import numpy as np
import matplotlib.pyplot as plt
import os
import multiprocessing
from ribbn_scripts.processing.phase_cal import cal_theta


COMMAND_RESULT_TYPE = {
    "get_mac": "mac",
    "perform_mpp": "mpp_times",
    "stop_reading": "voltage_readings",
    "get_adc_val": "adc_vals",
}

def device_worker(com_port, tag_id, command_queue, result_queue):
    """
    A worker function to be run in a separate PROCESS. It instantiates its
    own Tag object to avoid sharing non-serializable objects.
    """
    print(f"Process for Tag {tag_id} on {com_port} started.")
    # Each process creates its own instance of the Tag class
    tag_instance = Tag(com_port)

    while True:
        command = command_queue.get()

        if command == "STOP":
            tag_instance.disconnect()
            print(f"Process for Tag {tag_id} stopping.")
            break

        try:
            if command == "get_mac":
                result = tag_instance.get_mac()
                result_queue.put((tag_id, "mac", result))
            elif command == "begin_reading":
                tag_instance.begin_reading()
            elif command == "perform_mpp":
                result = tag_instance.perform_mpp()
                result_queue.put((tag_id, "mpp_times", result))
            elif command == "stop_reading":
                result = tag_instance.stop_reading()
                result_queue.put((tag_id, "voltage_readings", result))
            elif command == "get_adc_val":
                result = tag_instance.get_adc_val()
                result_queue.put((tag_id, "adc_vals", result))
            elif command[:2]=='ch':
                tag_instance.reflect(int(command[3:]))

        except Exception as e:
            print(f"🛑 ERROR in process for Tag {tag_id} ({com_port}): {e}")
            # Whoever sent `command` is blocked waiting on a matching
            # result_queue entry (see MPPMultiWays) -- without this they'd
            # wait forever. None marks the reading as failed/missing.
            result_type = COMMAND_RESULT_TYPE.get(command)
            if result_type:
                result_queue.put((tag_id, result_type, None))

SLEEPTIME=0.1
READTIME=5

# Default Settings
INBUILT_REPETITIONS=1
# FREQ_RANGE=[915]
FREQ_RANGE=list(range(775,1005,10))

# # Setting up the exciter
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

def initialize(tag_port_mapping):
    global cmd_qs, result_q, processes
    n_tags = len(tag_port_mapping)
    result_q = multiprocessing.Queue()
    
    # Create queues from the multiprocessing module
    for tag in tag_port_mapping.keys():
        local_queue = multiprocessing.Queue()
        cmd_qs[tag]= local_queue
        processes[tag] = multiprocessing.Process(target=device_worker, args=(tag_port_mapping[tag], int(tag[3:]), local_queue, result_q), daemon=True)
    

    # Start the child processes
    for p in processes.values():
        p.start()
    

def MPP(cmdq_rx,cmdq_tx, result_q):
    """
        @args: Tx, Rx: Tag type objects.
    
        - Set Rx to receiving state.
        - Go through all phases of Tx (includes non-reflecting (ch5) and receiving (ch2), for completeness.
        
        @returns a dictionary of "phase":"voltage at Rx" mappings.
    """

    cmdq_rx.put("begin_reading")
    cmdq_tx.put("perform_mpp")
    mpp_done = False
    mpp_start_time=None
    mpp_stop_time=None
    while not mpp_done:
        tag_id, res_type, data = result_q.get()
        if res_type == "mpp_times":
            mpp_start_time, mpp_stop_time = data
            mpp_done = True
    cmdq_rx.put("stop_reading")
    voltage_readings = None
    while voltage_readings is None:
        tag_id, res_type, data = result_q.get()
        if res_type == "voltage_readings":
            voltage_readings = data

    return voltage_readings, mpp_start_time, mpp_stop_time


def MPPMultiWays(rx_tags:list, cmdq_tx, result_q):
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
    for rx_tag in rx_tags:
        cmd_qs[rx_tag].put("stop_reading")
    voltage_readings = {}
    while len(voltage_readings) < len(rx_tags):
        tag_id, res_type, data = result_q.get()
        if res_type == "voltage_readings":
            voltage_readings[tag_id] = data
    # print("MPP DONE WITH TAGS:",len(voltage_readings))
    return voltage_readings, mpp_start_time, mpp_stop_time


def getChannelVoltage(voltage_readings, mpp_stop_time, mpp_start_time, channels,plotting=False):
    channel_medians={}
    channel_voltages={}
    ignore_cnt=70
    voltage_readings=voltage_readings[:-ignore_cnt]
    if plotting:
        plt.figure(figsize=(20,10))

    mpp_time_elapsed=mpp_stop_time-mpp_start_time

    plot_all_time=np.arange(0,mpp_time_elapsed,mpp_time_elapsed/len(voltage_readings))
    plot_end_time=plot_all_time[-1]
    ver_lines=[]
    for i in range(len(channels)+1):
        ver_lines.append(plot_end_time-0.003*(i))

    if plotting:
        for v in ver_lines:
            plt.axvline(x = v, color = 'b', label = 'axvline - full height')

    plot_time=plot_all_time[:len(voltage_readings)]
    plot_voltage=np.asarray(voltage_readings)[:len(plot_time)]

    if plotting:
        plt.plot(plot_time,plot_voltage, '.')

    
    # Median of the readings falling between each adjacent pair of blue lines,
    # drawn on top of the data trace.
    for seg,(left,right) in enumerate(zip(sorted(ver_lines)[:-1],sorted(ver_lines)[1:])):
        if seg==len(channels):
            continue
        in_segment=(plot_time>=left)&(plot_time<right)
        if not np.any(in_segment):
            continue
        channel_median=np.median(plot_voltage[in_segment])
        channel_medians[channels[seg]]=channel_median
        channel_voltages[channels[seg]]=plot_voltage[in_segment]
        if plotting:
            print(f"  segment {seg} [{left:.5f}, {right:.5f}): {np.count_nonzero(in_segment)} readings: {channel_median}")
            plt.hlines(channel_median,left,right,color='r',zorder=3)
    if plotting:
        plt.xlabel("Time [s]")
        plt.ylabel("ADC out [mV]")
        plt.show()
    
    return channel_medians,channel_voltages


def mainMultiWays(num_exp_runs, exp_name, save_path, 
                  hw_config,
                  tag_mac_mapping,
                  freq_range=FREQ_RANGE,     
                  exciter_type=None, mpp_repetitions=1, 
                  inter_MPP_batch_sleep_time=0.1,
                  channels=[1,3,4,6,7,8]):
    global cmd_qs, result_q, processes
    
    if exciter_type=='rf_gen':
        exc = Exciter()
        exc.set_freq(915)
        exc.set_pwr(EXC_POWER)

    csv_path=f"{save_path}/{exp_name}.csv"
    if os.path.exists(csv_path):
        answer = input(f"{csv_path} already exists. Overwrite? (Y/n): ").strip().lower()
        if answer in ("", "y"):
            open(csv_path, "w").close()
            print(f"Cleared existing file: {csv_path}")

    t_start=time.time()
    for run_exp_num in range(num_exp_runs):
        print(f"MPP Batch {run_exp_num}/{num_exp_runs}")
    
        if exciter_type=='rf_gen':
            exc.set_pwr(EXC_POWER)

        
        premature_stop=0
        premature_stop_error=""
        FREQS_DONE=[]
        columns=["Rx","Tx", "MPP Start Time (s)",
                                "MPP Stop Time (s)","Voltages (mV)",
                                    "Frequency (MHz)", "Run Exp Num", "MPP Repetition", "Unidirectional Phase (deg)"
                                    "Time Taken (s)"]
        for ch in channels:
            columns.append(f"Channel_{ch}_voltages")
            columns.append(f"Channel_{ch}_median")
        DF=pd.DataFrame(columns=columns)
        DF_SNAPSHOP=DF

        try:
            for freq in freq_range:
                if exciter_type=='rf_gen':
                    exc.set_freq(freq)
                # print("FREQ:",freq)
                
                for tx_tag in cmd_qs:
                    tx_queue = cmd_qs[tx_tag]
                    rx_tags = []
                    for prob_rx_tag in cmd_qs:
                        if prob_rx_tag==tx_tag:
                            continue
                        rx_tags.append(prob_rx_tag)
                        
                    
                    # print([tx_tag, rx_tags])
                    for rep in range(mpp_repetitions):
                        # print(f"Rep: {rep}")
                        rep_start=time.time()
                        voltage_readings_1, mpp_start_time_1, mpp_stop_time_1=MPPMultiWays(rx_tags=rx_tags, cmdq_tx=tx_queue, result_q=result_q)
                        rep_end=time.time()
                        for rx_tag in rx_tags:
                            _voltages=voltage_readings_1[int(rx_tag[3:])]
                            
                            # MPP processing and phase calculation
                            channel_median, channels_voltages=getChannelVoltage(_voltages, mpp_stop_time_1, mpp_start_time_1, channels=channels, plotting=False)
                            phase_theta=cal_theta(channel_median, rxName=tag_mac_mapping[rx_tag], txName=tag_mac_mapping[rx_tag],
                            cfg=hw_config,
                            freq=freq*1e6, #in hz
                            )
                            
                            print(phase_theta)
                            
                            entry={
                                "Rx":rx_tag, 
                                "Tx":tx_tag, 
                                "MPP Start Time (s)":mpp_start_time_1, 
                                "MPP Stop Time (s)":mpp_stop_time_1, 
                                "Voltages (mV)":_voltages,
                                "Frequency (MHz)":freq, 
                                "Run Exp Num":run_exp_num,
                                "MPP Repetition": rep+1,
                                "Time Taken(s)":rep_end-rep_start,
                                "Unidirectional Phase (deg)":phase_theta
                                
                            }
                            for ch in channels:
                                entry[f"Channel_{ch}_voltages"]=channels_voltages[ch]
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
            raise e

        write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        DF_SNAPSHOP.to_csv(csv_path, mode="a", header=write_header, index=False)
        print(f"Appended run {run_exp_num} to {csv_path}")

        time.sleep(inter_MPP_batch_sleep_time)


    time_taken=time.time()-t_start

    print(f"Time taken: {time_taken} for {num_exp_runs} runs over {len(freq_range)} freqs")
    
    if exciter_type=="rf_gen":
        exc.set_pwr(-30)
        
    return premature_stop




def test(tags, exciter_type):
    global cmd_qs, processes, result_q
    
    if exciter_type=='rf_gen':
        exc = Exciter()
        exc.set_pwr(EXC_POWER)
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

    if exciter_type=='rf_gen':
        exc.set_pwr(-30)
    
    return {"Test": "done"}

# if __name__=="__main__":
#     initialize()
#     test()
#     main(1)


def netInitialize():
    try:
        initialize()
    except:
        return {"status": "initializationErrror"}    
    return {"status": "Initialized"}

def ping():
    return {"status": "good"}

