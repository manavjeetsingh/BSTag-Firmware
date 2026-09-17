from ribbn_scripts.hardware_api.hardware import Tag
import argparse
import sys
import os
import json
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IGNORE_LIST=["/dev/tty.debug-console", "/dev/tty.wlan-debug", "/dev/tty.Bluetooth-Incoming-Port"]

MAC_TAG_FILE = "mac-tag-mapping.json"
IP_MAC_FILE = "ip-mac-mapping.json"

# How long to keep asking a freshly-plugged tag where it is on the network.
# `net` answers "down" until the association and the DHCP lease are both
# done, which is a second or two after the serial port is usable.
NET_WAIT_S = 20
NET_POLL_S = 1

def get_mac_address(port):
    tag=Tag(port)
    mac=tag.get_mac()
    return mac

def get_mac_and_ip(port, wait_s=NET_WAIT_S):
    """Read a tag's MAC and, if its radio comes up, its IP -- over serial.

    This is the one place that legitimately needs the cable: it is what
    turns "the tag on this port" into an address the rest of the tooling can
    reach it at with no cable at all. Returns (mac, ip), ip None if the tag
    never associates.
    """
    tag = Tag(port)
    try:
        mac = tag.get_mac()

        deadline = time.time() + wait_s
        resumed = False
        while True:
            status = tag.get_ip_port()
            state = status.get("net")

            if state == "up":
                return mac, status["ip"]
            if state == "disabled":
                # Built with NET_ENABLED 0, or an empty WIFI_SSID in
                # secrets.h -- waiting will not change that.
                print(f"  {mac}: wifi disabled in firmware, no ip")
                return mac, None
            if state == "suspended" and not resumed:
                # Left over from an esync window; ask for the radio back
                # once, then fall through to the normal wait.
                print(f"  {mac}: radio suspended, sending wifi_on")
                tag.wifi_on()
                resumed = True

            if time.time() >= deadline:
                print(f"  {mac}: wifi still '{state}' after {wait_s}s, no ip")
                return mac, None
            time.sleep(NET_POLL_S)
    finally:
        tag.disconnect()

def get_platform():
    if sys.platform == "darwin":  # macOS
        print("Running on macOS")
    elif sys.platform == "win32":
        print("Running on Windows")
    elif sys.platform.startswith("linux"):
        print("Running on Linux")
        raise Exception ("Linux not supported yet.")
    return sys.platform

def get_exising_mapping(file_name=MAC_TAG_FILE):
    try:
        with open(os.path.join(SCRIPT_DIR, file_name)) as f:
            mapping = json.load(f)
    except FileNotFoundError:
        return None
    return mapping

def save_updated_mapping(mapping, file_name=MAC_TAG_FILE):
        with open(os.path.join(SCRIPT_DIR, file_name), 'w') as f:
            json.dump(mapping, f, indent=2, sort_keys=False)

def record_ip(ip_mac_mapping, ip, mac):
    """Record ip -> mac, dropping whatever that tag was last seen at.

    Keyed by IP to match mac-tag-mapping.json's key-is-the-looked-up-thing
    shape, but unlike a MAC an IP is a lease: the same tag comes back on a
    different one, and an address it used can be handed to something else
    entirely. So the tag's old entry is removed rather than left to be
    probed later as a tag that no longer answers there.
    """
    stale = [old_ip for old_ip, old_mac in ip_mac_mapping.items()
             if old_mac == mac and old_ip != ip]
    for old_ip in stale:
        print(f"  {mac} moved {old_ip} -> {ip}, dropping old entry")
        del ip_mac_mapping[old_ip]

    if ip in ip_mac_mapping and ip_mac_mapping[ip] != mac:
        print(f"  {ip} was {ip_mac_mapping[ip]}, now {mac}")

    ip_mac_mapping[ip] = mac

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
    parser = argparse.ArgumentParser(
        description="Assign stable TagN names to the tags on the serial ports, "
                    "and optionally record where each one is on the network.")
    parser.add_argument(
        "--wifi", action="store_true",
        help="also record an ip -> mac mapping, so the tags can later be run "
             "with no serial cable (CONNECTION=wireless in configurations.json). "
             "Serial is still required here -- this is how the ip is learned.")
    args = parser.parse_args()

    mac_tag_mapping = get_exising_mapping(MAC_TAG_FILE)
    if not mac_tag_mapping:
        print("No existing mac-tag mapping found, will create new.")
        mac_tag_mapping = dict()
    tag_no=len(mac_tag_mapping)+1

    ip_mac_mapping = dict()
    if args.wifi:
        ip_mac_mapping = get_exising_mapping(IP_MAC_FILE)
        if not ip_mac_mapping:
            print("No existing ip-mac mapping found, will create new.")
            ip_mac_mapping = dict()

    ports = get_ports()

    for p in ports:
        if p in IGNORE_LIST:
            continue
        try:
            if args.wifi:
                mac, ip = get_mac_and_ip(p)
            else:
                mac, ip = get_mac_address(p), None
        except Exception as e:
            print(f"Unable to connect to {p} got the following exception: {e}")
            continue
        if mac not in mac_tag_mapping:
            print(f"Got new mac {mac} on {p}. Will be saved as Tag{tag_no}")
            mac_tag_mapping[mac] = f"Tag{tag_no}"
            tag_no+=1
        else:
            # An already-named tag still gets its ip refreshed below: the
            # name is permanent, the lease is not.
            print(f"Found existing mac {mac} on {p}. Belongs to {mac_tag_mapping[mac]}")

        if args.wifi and ip is not None:
            print(f"  {mac_tag_mapping[mac]} ({mac}) reachable at {ip}")
            record_ip(ip_mac_mapping, ip, mac)

    save_updated_mapping(mac_tag_mapping, MAC_TAG_FILE)
    if args.wifi:
        save_updated_mapping(ip_mac_mapping, IP_MAC_FILE)
        print(f"Saved {len(ip_mac_mapping)} ip -> mac entries to {IP_MAC_FILE}")

if __name__=="__main__":
    main()
