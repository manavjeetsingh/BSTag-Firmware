from init import get_exising_mapping, get_ports, IGNORE_LIST, get_mac_address
import measurePhasesMultiThreadedMultiTags as mtt
import numpy as np
import time
import pickle
import os
import ast
import pandas as pd

# matches the pwr clipping (dBm, rounded) in phase_cal.cal_theta


def build_s11_cfg(vna_data):
    """Turn the raw all_s11_poly.csv rows into cfg['s11'][mac][channel] = {'freq' (Hz), 'phase' (rad), 'amp'}."""
    s11_cfg = {}
    for _, row in vna_data.iterrows():
        freq = np.array(ast.literal_eval(row['Frequencies']))
        phase = np.deg2rad(np.array(ast.literal_eval(row['Phases'])))
        amp = np.array(ast.literal_eval(row['Amplituds']))

        mac = row['Tag MAC'].replace('_', ':')

        tag_entry = s11_cfg.setdefault(mac, {})
        tag_entry[int(row['Channel'])] = {'freq': freq, 'phase': phase, 'amp': amp}

    return s11_cfg

def build_pv_cfg(pv_data):
    """Turn the all_pv_polynomials.csv rows into cfg['pv'][mac][freq (Hz)] = {'polynomial', 'inverse'}."""
    pv_cfg = {}
    for _, row in pv_data.iterrows():
        mac = row['Tag MAC'].replace('_', ':')

        tag_entry = pv_cfg.setdefault(mac, {})
        tag_entry[float(row['Frequency'])*1e6] = {
            'polynomial': np.array(ast.literal_eval(row['Polynomial'])),
            'inverse': np.array(ast.literal_eval(row['Inverse'])),
        }

    return pv_cfg

def load_hw_config():
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    VNA_data_path = os.path.join(SCRIPT_DIR, "ribbn_scripts/src/ribbn_scripts/calibrations/VNA_data_Sept2026/processed","all_s11_poly.csv")
    PV_data_path = os.path.join(SCRIPT_DIR, "ribbn_scripts/src/ribbn_scripts/calibrations/PV_data_Sept2026/processed","all_pv_polynomials.csv")

    vna_data = pd.read_csv(VNA_data_path)
    pv_data = pd.read_csv(PV_data_path)

    cfg = {}
    cfg['s11'] = build_s11_cfg(vna_data)
    cfg['pv'] = build_pv_cfg(pv_data)

    print(cfg['pv']['98:A3:16:8F:DB:94'].keys())
    exit()

    return cfg


def tag_detection():
    mac_tag_mapping = get_exising_mapping("mac-tag-mapping.json")
    tag_mac_mapping = {v: k for k, v in mac_tag_mapping.items()}
    
    ports = get_ports()
    print(ports)
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
    return tag_port_mapping, tag_mac_mapping, mac_tag_mapping

def main():
    configurations = get_exising_mapping("configurations.json")
    print(configurations)
    
    if configurations["EXCITER"]=="None":
        exciter_type=None
    else:
        exciter_type=configurations["EXCITER"]
    
    tag_port_mapping, tag_mac_mapping, mac_tag_mapping=tag_detection()
    mtt.initialize(tag_port_mapping)
    mtt.test(tag_port_mapping.keys(), exciter_type=configurations["EXCITER"])
    
    if len(tag_port_mapping)<2:
        raise Exception(f"Need at least two tags, got {len(tag_port_mapping)}")

    input("Testing done, press [enter/return] to continue...")
    
    
    hw_config=load_hw_config()
    
    err=mtt.mainMultiWays(
        num_exp_runs=configurations["NUM_EXP_RUNS"],
        hw_config=hw_config,
        save_path=configurations['SAVE_DIR'],
        exp_name=configurations["EXP_NAME"],
        freq_range=np.arange(
            configurations["FREQ_MIN"],
            configurations["FREQ_MAX"]+1,
            configurations["FREQ_JUMP"]), 
        mpp_repetitions=configurations["MPP_REPETITIONS"],
        exciter_type=exciter_type,
        inter_MPP_batch_sleep_time=configurations["INTER_MPP_BATCH_SLEEP_S"],
        channels=configurations["CHANNELS"],
        tag_mac_mapping=tag_mac_mapping)



if __name__=="__main__":
    main()    
