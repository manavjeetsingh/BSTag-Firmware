from init import (get_exising_mapping, get_ports, IGNORE_LIST, get_mac_address,
                  MAC_TAG_FILE, IP_MAC_FILE)
from ribbn_scripts.hardware_api.hardware import Tag, WIFI_PORT
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
    PV_data_path = os.path.join(SCRIPT_DIR, "ribbn_scripts/src/ribbn_scripts/calibrations/PV_data_Sept2026_705_995/processed","all_pv_polynomials.csv")

    vna_data = pd.read_csv(VNA_data_path)
    pv_data = pd.read_csv(PV_data_path)

    cfg = {}
    cfg['s11'] = build_s11_cfg(vna_data)
    cfg['pv'] = build_pv_cfg(pv_data)

    # print(cfg['s11']['98:A3:16:8F:DB:94'].keys())
    # exit()

    return cfg


def tag_detection():
    """Find the tags on the serial ports (CONNECTION: "wired")."""
    mac_tag_mapping = get_exising_mapping(MAC_TAG_FILE)
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


def get_mac_over_wifi(ip, port=WIFI_PORT, connect_timeout=2):
    """Ask the tag at `ip` for its MAC over TCP, and hang up.

    Deliberately short-lived: this is identification, not the session the
    run uses -- each worker process opens its own afterwards. retries=1 so a
    recorded address that no longer has a tag behind it fails here instead
    of retrying forever inside connect_wifi().
    """
    tag = Tag.over_wifi(ip, port, retries=1, timeout=connect_timeout)
    try:
        return tag.get_mac_wifi()
    finally:
        tag.disconnect_wifi()


def tag_detection_wireless():
    """Find the tags on the network (CONNECTION: "wireless").

    No serial anywhere in here. ip-mac-mapping.json (written by
    `python init.py --wifi`, which does need the cable) says which addresses
    to try; the MAC each one answers with is what actually names the tag, so
    a lease that has moved to another tag is caught rather than silently
    mislabelling every reading from it.
    """
    mac_tag_mapping = get_exising_mapping(MAC_TAG_FILE)
    if not mac_tag_mapping:
        raise Exception("run `python init.py --wifi`, before running this script.")
    tag_mac_mapping = {v: k for k, v in mac_tag_mapping.items()}

    ip_mac_mapping = get_exising_mapping(IP_MAC_FILE)
    if not ip_mac_mapping:
        raise Exception(
            f"no {IP_MAC_FILE} -- connect the tags over serial once and run "
            f"`python init.py --wifi` to record their addresses.")

    tag_ip_mapping = dict()
    for ip, recorded_mac in ip_mac_mapping.items():
        try:
            mac = get_mac_over_wifi(ip)
        except Exception as e:
            print(f"Unable to reach {ip} ({recorded_mac}) over wifi, got the following exception: {e}")
            continue

        if mac != recorded_mac:
            print(f"⚠️  {ip} answers as {mac}, not the recorded {recorded_mac} "
                  f"-- the lease moved. Re-run `python init.py --wifi`.")
        if mac not in mac_tag_mapping:
            raise Exception("run `python init.py --wifi`, before running this script.")

        tag_ip_mapping[mac_tag_mapping[mac]] = ip

    print(f"Detected tag(s): {tag_ip_mapping}")
    return tag_ip_mapping, tag_mac_mapping, mac_tag_mapping


def detect_tags(connection):
    if connection == mtt.WIRELESS:
        return tag_detection_wireless()
    if connection == mtt.WIRED:
        return tag_detection()
    raise Exception(
        f"CONNECTION in configurations.json is {connection!r}, expected "
        f"{mtt.WIRED!r} or {mtt.WIRELESS!r}")


def main():
    configurations = get_exising_mapping("configurations.json")
    print(configurations)
    
    if configurations["EXCITER"]=="None":
        exciter_type=None
    else:
        exciter_type=configurations["EXCITER"]

    # Which transport this run uses end to end: how the tags are found here,
    # and which half of the Tag API the workers drive.
    connection = configurations.get("CONNECTION", mtt.WIRED).lower()
    print(f"Connection: {connection}")

    tag_endpoint_mapping, tag_mac_mapping, mac_tag_mapping=detect_tags(connection)
    mtt.initialize(tag_endpoint_mapping, transport=connection)
    mtt.test(tag_endpoint_mapping.keys(), exciter_type=configurations["EXCITER"],
             exc_power=configurations["EXC_POWER_DBM"])
    
    if len(tag_endpoint_mapping)<2:
        raise Exception(f"Need at least two tags, got {len(tag_endpoint_mapping)}")

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
        exc_power=configurations["EXC_POWER_DBM"],
        tag_mac_mapping=tag_mac_mapping,
        transport=connection,
        # Wireless only: how long the exciter holds its null. Ignored by a
        # wired run, which does not sync off a blank at all.
        esync_null_hold_s=configurations.get("ESYNC_NULL_HOLD_S",
                                             mtt.esync_mpp.NULL_HOLD_S))



if __name__=="__main__":
    main()    
