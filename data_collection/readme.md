- Install ribbn scripts using:
```python -m pip install ribbn_scripts from this directory.```

- Create fixed tag names supported by the mac address: ```python init.py```

- To also run the tags over WiFi, connect them over serial once and run
  ```python init.py --wifi```
  That writes `ip-mac-mapping.json` alongside `mac-tag-mapping.json`. Serial is
  required for this step only -- it is how each tag's IP is learned.

- `CONNECTION` in `configurations.json` picks the transport for a run:
  - `"wired"` - tags are found on the serial ports, as before.
  - `"wireless"` - no serial at all: the addresses in `ip-mac-mapping.json` are
    probed over TCP, each tag names itself by the MAC it answers with, and the
    workers drive the `*_wifi` half of the `Tag` API.

  A tag whose IP lease has moved stops being reachable -- re-run
  `python init.py --wifi` over serial to refresh it. Wireless runs need firmware
  built with `NET_ENABLED 1` and a populated `secrets.h`.

## Wireless MPP is synced off the exciter

Over WiFi the host cannot start the tags together closely enough for a 3 ms
MPP dwell — association, lwIP and the AP put tens of milliseconds between the
sockets, so the Rx captures straddle the Tx sweep. A `CONNECTION: wireless`
run therefore does not start the sweep from the host at all. Per MPP round
(`esync_mpp.py`, `MPPMultiWaysEsync`):

1. Stage — Tx tag gets `q_mpp_1`, every Rx tag gets `q_rdb`. This must happen
   *before* arming: `esync` suspends the radio within `ESYNC_WIFI_QUIET_MS` of
   acking, so anything sent to an armed tag never lands.
2. Arm — `esync` on every tag, then wait for all acks and `ARM_SETTLE_S` more,
   so every radio is really down and every detector has seen enough carrier to
   seed its baseline.
3. Blank — the generator drops to −30 dBm for `ESYNC_NULL_HOLD_S`, then back.
   Every tag times off that one falling edge and fires `ESYNC_FIRE_DELAY_US`
   later by dead reckoning. Host jitter is out of the timing path entirely.
4. Collect — the firmware holds the radio down until the queued capture
   finishes, so reconnecting *is* the readiness check. Then `qr` (the ack),
   `esyncr` (how the tag saw the blank), and `rds` (the 10000-sample trace).

Because the Rx tags queue `rdb` rather than `adc_<n>`, the trace comes back at
the full sample rate over the live socket instead of through the 16 KB queued
reply buffer — so it is shaped exactly like a wired capture and the existing
segmentation and phase code is used unchanged.

### Adding another exciter

The tags do not care how the carrier goes away — they latch the first sample
at or under `ESYNC_MIN_BASELINE_MV` (2 mV) and dead-reckon from there. So any
mechanism that takes the carrier to the floor and brings it back is a valid
blank. Subclass `esync_mpp.NullExciter`, implement `fire()` and `describe()`,
and register it:

```python
@esync_mpp.register_null_exciter('my_gen')
class MyNullExciter(esync_mpp.NullExciter):
    def __init__(self, hold_s=esync_mpp.NULL_HOLD_S, exc=None, **_):
        super().__init__(hold_s)
        self.exc = exc
    def fire(self):      # -> (t_drop, t_restore)
        ...
    def describe(self):
        ...
```

That decorator is the only place an `EXCITER` value is wired to a blank —
nothing in `collection.py` or `measurePhasesMultiThreadedMultiTags.py` knows
the names. Factories get every resource the caller has (`exc`,
`run_power_dbm`, …) as keyword arguments; take what you need and absorb the
rest with `**_`, so a resource added later does not break implementations
that do not want it.

Two properties are worth checking for any new blank, and neither is about the
mechanism: it must reach the floor **at every tag**, not just the nearest
(`esyncr`'s `min_mv` against `base_mv` is the check — a tag the blank does not
reach never fires at all), and its falling edge should be sharp, since a slow
decay moves `t_fall` later and drags every tag's fire with it.

### Tuning `ESYNC_NULL_HOLD_S`

The tag fires at `t_fall + 50 ms` (`ESYNC_FIRE_DELAY_US`) regardless of how
long the blank actually is, so the hold only has to land in a window:

- **too long** (past 50 ms) — the tag sweeps before the carrier is back, with
  nothing illuminating it. The trace is garbage; `esyncr` says `"returned":0`
  and the run raises.
- **too short** (under `ESYNC_BLANK_MIN_US`, 10 ms measured) — the tag calls it
  a fade and never fires; `esyncr` shows `rej_short` climbing.

The default 0.03 s sits in the middle: it measures back as roughly 20–35 ms of
`low_us`, since the rectifier takes ~10 ms to fall to the 2 mV floor after the
generator drops. Every row of the CSV carries `Esync Low (us)`, `Esync
Returned` and `Esync Long`, and each round logs the spread across tags — a
wide spread means one tag timed off a different edge and its trace does not
line up with the others even though both fired.

Each synced round costs a WiFi reassociation (~1–3 s), so a wireless sweep is
several times slower than the wired one. That is the price of the sync.

`test()` and the other commands still run unsynced over plain TCP; only MPP
needs the edge. Wired runs are unchanged and do not use esync at all.
