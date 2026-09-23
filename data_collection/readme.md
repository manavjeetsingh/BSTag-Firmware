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
    carrier.

  A `CONNECTION: wireless` run needs `"bladerf"`: it is synced by the
  bladeRF's ASK preamble, and a GPIB generator cannot key 2 ms chips.

  `exciters.py` is the only place these names mean anything -- `collection.py`
  and `measurePhasesMultiThreadedMultiTags.py` just ask for a frequency and a
  level. `BLADERF_*` keys in `configurations.json` are passed to it as
  lowercased keyword arguments.

  `EXC_POWER` (formerly `EXC_POWER_DBM`) goes to the exciter untouched and
  means whatever that exciter's level control means: dBm on the `rf_gen`, TX
  gain in dB on the `bladerf`. It is not a unit the tooling converts, so it
  belongs to the exciter a run is using -- moving `EXCITER` without moving
  `EXC_POWER` changes the carrier.

## A cut-short reply fails the round instead of hanging on it

Every reply the tag sends is one line, and the read loops in `hardware.py`
accumulate bytes until a `{...}` blob parses. When bytes go missing -- the
closing brace never comes -- there is nothing to wait for, so they no longer
wait: a line that ended without a parseable blob, a session the tag has closed,
or a blob that goes quiet for `PARTIAL_REPLY_IDLE_S` (5 s) all raise
`TruncatedReply` at once rather than sitting out the read's timeout (60 s for a
10000-sample dump).

Nothing can be salvaged at that point -- the sweep is over and the tag's buffer
is gone -- so the round is re-shot from the start: `MAX_ROUND_ATTEMPTS` (10)
wireless, `MAX_WIRED_ROUND_ATTEMPTS` (3) wired. A round is also re-shot when
`perform_mpp` or a capture answers nothing at all, so a partial round never
reaches the CSV. Each re-shoot prints its reason; a round that fails every
attempt raises with the last one.

## Wireless MPP is synced off the exciter

Over WiFi the host cannot start the tags together closely enough for a 3 ms
MPP dwell, so a `CONNECTION: wireless` run does not start the sweep from the
host at all. The bladeRF sends a 26 ms Barker-coded ASK preamble, every tag
correlates against it (`esync` in the firmware; see
`BladeRFCode/ASK_sync/README.md`), and each fires its staged command 5 ms after
the preamble ends. Per MPP round (`MPPMultiWaysEsync`):

1. Stage — Tx tag gets `q_mpp_1`, every Rx tag gets `q_rdb`. Before arming,
   because `esync` takes the radio down 50 ms after acking.
2. Arm — `esync` on every tag, then `ARM_SETTLE_S` (1 s) so every radio is
   down and every correlator has a full preamble's worth of carrier.
3. Sync — `exc.sync()` sends one preamble.
4. Collect — waiting for the fired reply is the wait, on the session that
   stayed up: the plain line `rdb` from an Rx tag, the `mpp` blob from the Tx
   tag, bounded by `FIRE_DEADLINE_S`. Then `esyncr`, and `rds` (the trace).
5. Check — every tag's `esyncr` must show a lock (`rho`). Any failure drops
   the round and re-shoots it from step 1, up to `MAX_ROUND_ATTEMPTS` (10).

A tag that never locked reports `peak_rho`, the best correlation it saw, and
the final error carries it. Near 0.8 means the preamble was heard but too
noisily (more carrier, or move the tag). Near 0 means it never arrived, or
`BARKER_CODE`/`CHIP_MS` in `bladerf_cw.py` do not match `ESYNC_CODE`/
`ESYNC_CHIP_US` in the firmware. Every CSV row carries `Esync Rho` and
`Esync SNR (dB)`.

Because the Rx tags queue `rdb`, the trace comes back at the full sample rate
over the live socket, shaped exactly like a wired capture, so the segmentation
and phase code is unchanged. Each round costs a WiFi reassociation (~1–3 s),
so a wireless sweep is several times slower than a wired one.

### Running with the bladeRF

Set `"EXCITER": "bladerf"`. The run builds the flowgraph itself
(`BladeRFCode/ASK_sync/bladerf_cw.py`, `CWExciter`) in its own process, so it
must run under a Python with GNU Radio and osmosdr, which are not in the repo's
`.venv`. On the lab Windows box that is radioconda:

```
C:\ProgramDataadioconda\python.exe -m pip install pyserial
C:\ProgramDataadioconda\python.exe -m pip install -e data_collection/ribbn_scripts
cd data_collection && C:\ProgramDataadioconda\python.exe collection.py
```

**`EXC_POWER` is TX gain in dB here, not a level**, clamped to 0..89; 12.9
left over from an `rf_gen` run is nearly nothing. Start around 60. Negative
means off on both exciters.

**If the bladeRF is on a different machine**, run
`BladeRFCode/ASK_sync/bladerf_exciter_server.py` there and set `BLADERF_HOST`
(and `BLADERF_PORT`, default 3334) here.

`test()` and the other commands run unsynced over plain TCP; only MPP needs the
sync. Wired runs are unchanged and do not use esync.
