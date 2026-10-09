"""Check every tag in ip-mac-mapping.json over WiFi.

For each recorded IP, open a TCP session, ask for `mac`, and compare it with
the MAC recorded for that IP. Reports one of:

  OK        -- reachable and still the recorded tag
  MOVED     -- reachable, but the lease now belongs to a different tag
  UNREACH   -- no answer (tag off, out of range, or the lease went away)

No serial needed. If anything MOVED, re-run `python init.py --wifi` with the
tags on their cables to refresh ip-mac-mapping.json.

    python check_tags_wifi.py [--timeout 2]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

from init import get_exising_mapping, MAC_TAG_FILE, IP_MAC_FILE
from ribbn_scripts.hardware_api.hardware import Tag, WIFI_PORT


def query_mac(ip, timeout):
    tag = Tag.over_wifi(ip, WIFI_PORT, retries=1, timeout=timeout)
    try:
        return tag.get_mac_wifi()
    finally:
        tag.disconnect_wifi()


def check(ip, recorded_mac, mac_tag_mapping, timeout):
    recorded_tag = mac_tag_mapping.get(recorded_mac, "?")
    try:
        mac = query_mac(ip, timeout)
    except Exception as e:
        return "UNREACH", f"{ip:<16} {recorded_tag:<6} ({recorded_mac})  -- {e}"

    if mac == recorded_mac:
        return "OK", f"{ip:<16} {recorded_tag:<6} ({mac})"

    actual_tag = mac_tag_mapping.get(mac, "unknown tag")
    return "MOVED", (f"{ip:<16} expected {recorded_tag} ({recorded_mac}), "
                     f"now answers as {actual_tag} ({mac})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=2,
                        help="TCP connect timeout per tag, seconds")
    args = parser.parse_args()

    ip_mac_mapping = get_exising_mapping(IP_MAC_FILE)
    mac_tag_mapping = get_exising_mapping(MAC_TAG_FILE) or {}
    if not ip_mac_mapping:
        raise SystemExit(f"no {IP_MAC_FILE} -- run `python init.py --wifi` first.")

    # One thread per tag: each check is just a connect and one line, and
    # an unreachable tag would otherwise cost its full timeout serially.
    with ThreadPoolExecutor(max_workers=len(ip_mac_mapping)) as pool:
        results = list(pool.map(
            lambda item: check(*item, mac_tag_mapping, args.timeout),
            ip_mac_mapping.items()))

    counts = {"OK": 0, "MOVED": 0, "UNREACH": 0}
    for status in ("OK", "MOVED", "UNREACH"):
        for s, line in results:
            if s == status:
                counts[s] += 1
                print(f"{s:<8} {line}")

    print(f"\n{counts['OK']} ok, {counts['MOVED']} moved, "
          f"{counts['UNREACH']} unreachable, of {len(results)}")
    if counts["MOVED"]:
        print("Leases moved -- re-run `python init.py --wifi` to refresh "
              f"{IP_MAC_FILE}.")


if __name__ == "__main__":
    main()
