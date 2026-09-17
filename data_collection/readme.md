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

- `EXCITER` in `configurations.json` picks what illuminates the tags:
  - `"rf_gen"` - the GPIB signal generator, over pyvisa, as before.
  - `"bladerf"` - the bladeRF in this machine's USB port, driven in process
    (see **Running with the bladeRF** below). Optional settings:
    `BLADERF_HOST` / `BLADERF_PORT`, only for a bladeRF on a *different*
    machine.
  - `"None"` - no exciter is driven from here; someone else is running the
    carrier. A `CONNECTION: wireless` run cannot use this, because it needs an
    exciter that can blank on command.

  `exciters.py` is the only place these names mean anything -- `collection.py`
  and `measurePhasesMultiThreadedMultiTags.py` just ask for a frequency and a
  level. Any `<NAME>_*` key in `configurations.json` is passed to that
  exciter's factory as a lowercased keyword argument, so a new exciter brings
  its own settings without either of those files learning them.

  `EXC_POWER` (formerly `EXC_POWER_DBM`) goes to the exciter untouched and
  means whatever that exciter's level control means: dBm on the `rf_gen`, TX
  gain in dB on the `bladerf`. It is not a unit the tooling converts, so it
  belongs to the exciter a run is using -- moving `EXCITER` without moving
  `EXC_POWER` changes the carrier.

## Wireless MPP is synced off the exciter

Over WiFi the host cannot start the tags together closely enough for a 3 ms
MPP dwell — association, lwIP and the AP put tens of milliseconds between the
sockets, so the Rx captures straddle the Tx sweep. A `CONNECTION: wireless`
run therefore does not start the sweep from the host at all. Per MPP round
(`esync_mpp.py`, `MPPMultiWaysEsync`):

1. Stage — Tx tag gets `q_mpp_1`, every Rx tag gets `q_rdb`. This must happen
   *before* arming: `esync` suspends the radio within `ESYNC_WIFI_QUIET_MS` of
   acking, so anything sent to an armed tag never lands.
2. Arm — `esync` on every tag, then wait for all acks and `ARM_SETTLE_S` (1 s)
   more, so every radio is really down and every detector has seen enough
   carrier to seed its baseline. The budget is only ~65 ms; the rest is margin,
   because a blank that lands before a tag is primed is discarded silently and
   comes back looking like an exciter that never fired.
3. Blank — the exciter takes the carrier to the floor for
   `ESYNC_NULL_HOLD_S` and brings it back (the generator by dropping to
   −30 dBm, the bladeRF by gating baseband to zero). Every tag times off that
   one falling edge and fires `ESYNC_FIRE_DELAY_US` later by dead reckoning.
   Host jitter is out of the timing path entirely.
4. Collect — the firmware holds the radio down until the queued capture
   finishes, so reconnecting *is* the readiness check. Then `qr` (the ack),
   `esyncr` (how the tag saw the blank), and `rds` (the 10000-sample trace).
5. Check — `check_fired()` reads every tag's `esyncr`. **Any** failure in
   steps 1–5 drops the round and re-shoots it from step 1, up to
   `MAX_ROUND_ATTEMPTS` (10) times; only a shot every tag survived is kept,
   and `result_q` is drained between attempts so a late answer from an
   abandoned round cannot be read as the next one's.

Retrying wins where the failure is a race — a blind fire (`"returned":0`) is
one, since the tag commits at the falling edge and fires a fixed delay later
regardless of when the carrier actually returns. It cannot help where the
failure is the setup, and the cap is what surfaces those: exhausting it means
the problem repeats identically every shot. Two worth recognising in the
`esyncr` line the final error carries:

- `base_mv` at or under 4.0 mV (`ESYNC_MIN_BASELINE_MV / (1 - ESYNC_REARM_PCT)`)
  — the return threshold has crossed under the floor, so `esyncListening()`
  bails out before the edge test on every sample. That tag is too dimly lit to
  tell "lit" from "blanked". It needs more carrier, not more attempts; the
  `pending:0` error names this case explicitly.
- Every shot `"returned":0` — `ESYNC_NULL_HOLD_S` is simply longer than
  `ESYNC_FIRE_DELAY_US`, so lower it.

Because the Rx tags queue `rdb` rather than `adc_<n>`, the trace comes back at
the full sample rate over the live socket instead of through the 16 KB queued
reply buffer — so it is shaped exactly like a wired capture and the existing
segmentation and phase code is used unchanged.

### Running with the bladeRF

The bladeRF's blank is the one the firmware's detector was written against:
the carrier is gated to zero in baseband, sample by sample, so the blank is
exactly as long as it was asked to be, where the generator's is a pair of GPIB
writes with latency either side. What it needed to be usable for a sweep was a
trigger that is not a keypress — which is now a method call.

Set `"EXCITER": "bladerf"` and run as usual. The run builds the flowgraph
itself (`BladeRFCode/null_sync/bladerf_cw.py`, `CWExciter`) in its own
process: nothing to start beforehand, no socket, and the carrier comes up when
the run starts and goes down when it ends.

The one requirement is the Python it runs under. GNU Radio and osmosdr are
GRC-provided, not pip packages, so they are **not** in the repo's `.venv` —
run `collection.py` with a Python that has them. On the lab Windows box that
is radioconda, which needs the tag side's packages added once:

```
C:\ProgramDataadioconda\python.exe -m pip install pyserial
C:\ProgramDataadioconda\python.exe -m pip install -e data_collection/ribbn_scripts
cd data_collection && C:\ProgramDataadioconda\python.exe collection.py
```

(It already has numpy, pandas, scipy and matplotlib. `pyvisa` is only needed
for `rf_gen` and the VNA, `scikit-learn` only for `processing/localization.py`,
neither of which a bladeRF run touches.)

**If the bladeRF is on a different machine** from the tags, that is what
`BLADERF_HOST` is for: start `bladerf_exciter_server.py` over there with the
same GNU Radio python, and set `BLADERF_HOST` (and `BLADERF_PORT`, default
3334) here. The run then drives the same `CWExciter` through a line protocol
instead of holding it. Leave `BLADERF_HOST` unset and none of that is
involved.

Two things to know before reading a number off it:

- **`EXC_POWER` is TX gain in dB here, not a level.** A bladeRF has no
  calibrated output to ask for one, so the number goes to the radio as given,
  clamped to 0..89 dB; nothing is converted. An `EXC_POWER` left over from an
  `rf_gen` run is therefore not the same carrier — 12.9 is a normal level out
  of the generator and nearly nothing as gain. Start around 60 and set it for
  the exciter you are actually running. (Negative still means off, on both:
  the generator takes it as −30 dBm, the bladeRF as "below any gain there is",
  and mutes.)
- **`ESYNC_NULL_HOLD_S` can sit closer to the fire delay here.** The gate is
  exact, so it does not need the margin the generator's host-timed drop does --
  0.05 s (`= ESYNC_FIRE_DELAY_US`) is what `manual_null_exciter.py` uses. The
  tag still fires ~10 ms later than the gate suggests, because its rectifier
  takes that long to fall to the 2 mV floor the edge is latched at, which is
  the margin that keeps the fire landing after the carrier is back. Anything at
  or under `ESYNC_BLANK_MIN_US` is refused before the run starts; anything past
  `ESYNC_FIRE_DELAY_US` warns.

### Adding another exciter

There are two registries, because lighting the tags and blanking for them are
separate jobs: `exciters.py` for the carrier (`set_freq` / `set_pwr`, what
every run needs) and `esync_mpp.py` for the sync blank (what only a wireless
run needs). An exciter can perfectly well do the first without the second --
that was the bladeRF's position until it got a trigger input.

The carrier half is a factory that returns anything answering to `set_freq(mhz)`
and `set_pwr(power)` (and, optionally, `close()`, taken if it is there).
`set_pwr` gets `EXC_POWER` as written, in whatever unit the instrument wants:

```python
@exciters.register_exciter('my_gen')
def make_my_gen(my_gen_host="127.0.0.1", **_):
    return MyGen(my_gen_host)
```

The blank is the interesting half. The tags do not care how the carrier goes
away — they latch the first sample at or under `ESYNC_MIN_BASELINE_MV` (2 mV)
and dead-reckon from there. So any mechanism that takes the carrier to the floor and brings it back is a valid
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
`run_power`, …) as keyword arguments; take what you need and absorb the
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
generator drops. That default is sized for the generator, whose blank is two
GPIB writes long and therefore approximate; the bladeRF's gate is exact to the
sample and can sit closer to the fire delay, at 0.05 s. Every row of the CSV carries `Esync Low (us)`, `Esync
Returned` and `Esync Long`, and each round logs the spread across tags — a
wide spread means one tag timed off a different edge and its trace does not
line up with the others even though both fired.

Each synced round costs a WiFi reassociation (~1–3 s), so a wireless sweep is
several times slower than the wired one. That is the price of the sync.

`test()` and the other commands still run unsynced over plain TCP; only MPP
needs the edge. Wired runs are unchanged and do not use esync at all.
