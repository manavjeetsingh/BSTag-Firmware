"""Find out why the tags are not locking on the exciter's preamble.

    C:\\ProgramData\\radioconda\\python.exe diagnose_esync.py

Runs four checks over WiFi against every tag in ip-mac-mapping.json, and each
one rules something out:

  1. carrier       -- do the tags see the exciter at all, and at what level?
  2. keying (raw)  -- capture raw ADC across a preamble and measure the
                      on/off swing directly, without the correlator in the
                      way. This is the check that separates "the preamble is
                      not on the air" from "the tag cannot detect it": if the
                      swing is there in the trace but esync still does not
                      lock, the problem is the detector or its timing; if the
                      swing is not there, nothing downstream can help.
  3. esync bare    -- arm and fire with NO schedule loaded, i.e. exactly what
                      COLLECTION_TYPE=individual does.
  4. esync + sched -- the same with a schedule loaded, i.e. what
                      COLLECTION_TYPE=multiple does.

3 against 4 is the one that matters for a run that works individually and not
multiply: the firmware arms identically either way (esyncListen() does not
look at the schedule; only the fire does), so if 3 locks and 4 does not, that
assumption is wrong and the difference is worth chasing. If neither locks, the
run is not the problem -- check the exciter with check 2.
"""

import sys
import time

import numpy as np

from init import get_exising_mapping, IP_MAC_FILE
from ribbn_scripts.hardware_api.hardware import Tag, SCHED_LISTEN, SCHED_MPP

import exciters
import sched_collect


CAPTURE_CHANNEL = 2


def connect_all():
    ip_mac = get_exising_mapping(IP_MAC_FILE)
    if not ip_mac:
        raise SystemExit(f"no {IP_MAC_FILE} -- run `python init.py --wifi` first.")
    tags = {}
    for ip, mac in ip_mac.items():
        try:
            t = Tag.over_wifi(ip, retries=1, timeout=5)
            tags[t.get_mac_wifi()] = t
        except Exception as e:
            print(f"  cannot reach {ip} ({mac}): {e}")
    if not tags:
        raise SystemExit("no tags reachable over wifi.")
    return tags


def check_carrier(tags):
    print("\n[1] carrier -- median ADC level with the carrier up, per tag")
    for mac, t in tags.items():
        t.reflect_wifi(CAPTURE_CHANNEL)
    time.sleep(0.05)
    for mac, t in tags.items():
        v = t.get_adc_val_wifi(count=30)
        print(f"    {mac}  {np.median(v):8.1f} mV")


def check_keying(tags, exc):
    """Capture raw ADC across a preamble and measure the swing directly."""
    print("\n[2] keying -- raw capture across one preamble (correlator not involved)")
    print("    The preamble is 13 chips of 2 ms, so a keyed carrier should show")
    print("    a swing comparable to the level in [1], not a fraction of a mV.")
    for mac, t in tags.items():
        t.begin_reading_wifi()
    time.sleep(0.02)
    exc.sync()
    time.sleep(0.15)
    for mac, t in tags.items():
        v = t.stop_reading_wifi()
        v = np.asarray(v, dtype=float)
        if len(v) < 20:
            print(f"    {mac}  short trace ({len(v)} samples)")
            continue
        lo, hi = np.percentile(v, 10), np.percentile(v, 90)
        print(f"    {mac}  {len(v):5d} samples  p10 {lo:8.1f}  p90 {hi:8.1f}  "
              f"swing {hi - lo:7.1f} mV   {'KEYED' if hi - lo > 5 else '<-- FLAT'}")


def arm_fire_report(tags, exc, label, stage):
    print(f"\n{label}")
    for mac, t in tags.items():
        stage(t)
        t.listen_esync_wifi()
    time.sleep(sched_collect.ARM_SETTLE_S)
    exc.sync()
    time.sleep(0.6)
    locked = 0
    for mac, t in tags.items():
        r = t.esync_report_wifi()
        if "rho" in r:
            locked += 1
            print(f"    {mac}  LOCKED  rho={r['rho']:.3f} swing={r.get('swing_mv')} "
                  f"snr={r.get('snr_db')} dB")
        else:
            print(f"    {mac}  no lock  peak_rho={r.get('peak_rho')} "
                  f"peak_swing_mv={r.get('peak_swing_mv')} "
                  f"level_mv={r.get('level_mv')} listening={r.get('listening')} "
                  f"primed={r.get('primed')} samples={r.get('samples')} "
                  f"stalls={r.get('stalls')}")
    print(f"    -> {locked}/{len(tags)} locked")
    return locked


def main():
    cfg = get_exising_mapping("configurations.json")
    exciter_type = exciters.normalize(cfg.get("EXCITER"))
    exc = exciters.make_exciter(exciter_type,
                                **exciters.settings_from_config(cfg))
    if exc is None or not hasattr(exc, "sync"):
        raise SystemExit(f"EXCITER={exciter_type!r} cannot key a preamble; "
                         f"this needs \"bladerf\".")
    freq = cfg.get("FREQ_MIN", 915)
    exc.set_freq(freq)
    exc.set_pwr(cfg["EXC_POWER"])
    time.sleep(0.2)
    print(f"exciter: {exciter_type} at {freq} MHz, EXC_POWER={cfg['EXC_POWER']}")
    if hasattr(exc, "describe"):
        print(f"         {exc.describe()}")

    tags = connect_all()
    print(f"tags: {list(tags)}")

    try:
        check_carrier(tags)
        check_keying(tags, exc)

        bare = arm_fire_report(
            tags, exc, "[3] esync with NO schedule loaded (individual mode)",
            lambda t: t.sched_clear_wifi())

        n = len(tags)
        program = [SCHED_LISTEN] * n
        program[0] = SCHED_MPP
        withsched = arm_fire_report(
            tags, exc, "[4] esync WITH a schedule loaded (multiple mode)",
            lambda t: t.sched_load_wifi(
                program, slot_us=sched_collect.DEFAULT_SLOT_US,
                listen_samples=sched_collect.listen_samples_for(n), raw=True))

        print("\n--- verdict ---")
        if bare == 0 and withsched == 0:
            print("Neither locks, so the schedule is not what breaks it. Check [2]:")
            print("  swing FLAT   -> the preamble is not reaching the tags. The")
            print("                  exciter is emitting CW but not keying it, or")
            print("                  something else is providing the carrier. Check")
            print("                  BARKER_CODE/CHIP_MS against ESYNC_CODE/")
            print("                  ESYNC_CHIP_US, and that no other exciter")
            print("                  process still holds the bladeRF.")
            print("  swing KEYED  -> the carrier is keyed but the detector misses")
            print("                  it. Compare the swing against")
            print("                  ESYNC_MIN_SWING_MV and peak_rho against")
            print("                  ESYNC_MIN_RHO in config.h.")
        elif bare > 0 and withsched == 0:
            print("Locks without a schedule and not with one -- so loading a")
            print("schedule does break arming, which the firmware should not do")
            print("(esyncListen() ignores the schedule; only the fire looks at")
            print("it). Worth reporting with this output.")
        elif withsched > 0:
            print("Locks with a schedule loaded, so esync and the exciter are")
            print("fine right now. If a run still fails, the difference is in the")
            print("run's sequencing rather than the mechanism.")
    finally:
        for t in tags.values():
            try:
                t.stop_esync_wifi()
                t.sched_clear_wifi()
                t.disconnect_wifi()
            except Exception:
                pass
        exciters.shutdown(exc)


if __name__ == "__main__":
    main()
