from init import get_exising_mapping, get_ports, IGNORE_LIST, get_mac_address
import measurePhasesMultiThreadedMultiTags as mtt
import numpy as np
import time
import pickle

def tag_detection():
    mac_tag_mapping = get_exising_mapping("mac-tag-mapping.json")
    ports = get_ports()
    tag_port_mapping=dict()
    
    for p in ports:
        if p in IGNORE_LIST:
            continue
        try:
            mac=get_mac_address(p)
        except Exception as e:
            print(f"Unable to connect to {p} got the following exception: {e}")
            continue
        if mac not in mac_tag_mapping:
            raise Exception("run `python init.py`, before running this script.")
            
        else:
            tag_port_mapping[mac_tag_mapping[mac]] = p
            
    print(f"Detected tag(s): {tag_port_mapping}")
    return tag_port_mapping

def main():
    configurations = get_exising_mapping("configurations.json")
    print(configurations)
    
    if configurations["EXCITER"]=="None":
        exciter_type=None
    else:
        exciter_type=configurations["EXCITER"]
    
    tag_port_mapping=tag_detection()
    mtt.initialize(tag_port_mapping)
    mtt.test(tag_port_mapping.keys())
    
    if len(tag_port_mapping)<2:
        raise Exception(f"Need at least two tags, got {len(tag_port_mapping)}")

    input("Testing done, press [enter/return] to continue...")
    
    
    with open(configurations["HW_CONFIG_PATH"],'rb') as f:
        hw_config = pickle.load(f)
    
    err=mtt.mainMultiWays(
        num_exp_runs=configurations["NUM_EXP_RUNS"],
        hw_config=hw_config,
        save_path="/Users/manavjeet/git/BSTag-Firmware/data_collection/out",exp_name=configurations["EXP_NAME"],
        freq_range=np.arange(
            configurations["FREQ_MIN"],
            configurations["FREQ_MAX"]+1,
            configurations["FREQ_JUMP"]), 
        mpp_repetitions=configurations["MPP_REPETITIONS"],
        exciter_type=exciter_type,
        inter_MPP_batch_sleep_time=configurations["INTER_MPP_BATCH_SLEEP_S"],
        channels=configurations["CHANNELS"])



if __name__=="__main__":
    main()    
