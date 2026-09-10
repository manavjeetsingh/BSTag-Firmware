from ribbn_scripts.hardware_api.hardware import Tag
import sys
import os
import json 

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IGNORE_LIST=["/dev/tty.debug-console", "/dev/tty.wlan-debug", "/dev/tty.Bluetooth-Incoming-Port"]

def get_mac_address(port):
    tag=Tag(port)
    mac=tag.get_mac()
    return mac

def get_platform():
    if sys.platform == "darwin":  # macOS
        print("Running on macOS")
    elif sys.platform == "win32":
        print("Running on Windows")
    elif sys.platform.startswith("linux"):
        print("Running on Linux")
        raise Exception ("Linux not supported yet.")
    return sys.platform

def get_exising_mapping(file_name="mac-tag-mapping.json"):
    try:
        with open(os.path.join(SCRIPT_DIR, file_name)) as f:
            mapping = json.load(f)
    except FileNotFoundError:
        return None
    return mapping

def save_updated_mapping(mapping, file_name="mac-tag-mapping.json"):
        with open(os.path.join(SCRIPT_DIR, file_name), 'w') as f:
            json.dump(mapping, f, indent=2, sort_keys=False)

def get_ports():
    pc_platform = get_platform()
    if pc_platform == "darwin":
        dev_files = os.listdir("/dev")
        ports = [f"/dev/{f}" for f in dev_files if f.startswith("tty.")]
    elif pc_platform == "win32":
        from serial.tools.list_ports import comports
        ports_desc_hwid = comports()
        ports=[]
        for p,d,h in ports_desc_hwid:
            if "USB Serial Device" in d:
                ports.append(p)
    
    return ports

def main():
    mac_tag_mapping = get_exising_mapping()
    if not mac_tag_mapping:
        print("No existing mac-tag mapping found, will create new.")
        mac_tag_mapping = dict()
    tag_no=len(mac_tag_mapping)+1

    ports = get_ports()
    
    for p in ports:
        if p in IGNORE_LIST:
            continue
        try:
            mac=get_mac_address(p)
        except Exception as e:
            print(f"Unable to connect to {p} got the following exception: {e}")
            continue
        if mac not in mac_tag_mapping:
            print(f"Got new mac {mac} on {p}. Will be saved as Tag{tag_no}")
            mac_tag_mapping[mac] = f"Tag{tag_no}"
            tag_no+=1
        else:
            print(f"Found existing mac {mac} on {p}. Belongs to {mac_tag_mapping[mac]}")
                
    
    save_updated_mapping(mac_tag_mapping)

if __name__=="__main__":
    main()