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

- `COLLECTION_TYPE` in `configurations.json` picks the shape of a round,
  independently of `CONNECTION` -- both work over either transport:
  - `"individual"` (the default, and what every earlier run was) - one
    transmitter per round: it sweeps, every other tag captures, repeated once
    per tag per frequency.
  - `"multiple"` - every tag is loaded with a slotted program first, and one
    trigger runs all of them at once. See **One trigger, every direction**
    below.

  Omitting the key leaves a run behaving exactly as before.

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

## A wireless run skips 810-820 MHz

The exciter's 3rd harmonic lands on the tags' 2.4 GHz channel there (3 x 815 =
2445 MHz, WiFi channels 6-9), and a carrier parked on it jams both tags for the
whole round: they go silent with their sockets still open, and every re-shoot
fails the same way. `mainMultiWays` drops those points from a `CONNECTION:
wireless` sweep and says so; the band is `esync_mpp.WIFI_JAMMED_MHZ`.

Wired runs sweep them normally -- there is no link to lose -- so that is where
to measure them. The harmonic is actually in-band across all of 800-828 MHz
(2400-2483.5 / 3), so 805 and 825 are the next suspects if a run dies with the
AP on channel 1-3 or 12-13. A low-pass filter on the exciter output clears the
whole question, and `WIFI_JAMMED_MHZ = (None, None)` then turns the skip off.

## One trigger, every direction (`COLLECTION_TYPE: multiple`)

An `individual` round measures one direction at a time, so a frequency costs N
rounds and the N readings at that frequency are stitched together across N
separate sweeps. A `multiple` round measures all of them at once. Every tag is
loaded ahead of time with an ordered program of equal-length slots -- with
three tags,

```
Tag1: [ mpp    , listen , listen ]
Tag2: [ listen , mpp    , listen ]
Tag3: [ listen , listen , mpp    ]
```

-- so slot k is tag k sweeping while everyone else records it. One trigger
runs the whole grid. A frequency costs one round instead of N, and every
reading at that frequency saw the same carrier at the same instant.

The CSV is the same file with the same columns, one row per (Rx, Tx) pair, plus
a `Collection Type` column saying which shape produced it. `post_processing.py`
needs no changes.

**What keeps the tags in step** is that the slot grid is absolute: boundary k
sits at `SCHED_SLOT_US` * k from the trigger, and a slot that finishes early is
padded out to it rather than running straight into the next one. So the only
error is the disagreement between the triggers themselves -- it never
accumulates across slots.

**The trigger** is the exciter's ASK preamble over the air for a wireless run,
exactly as in `individual` mode: a loaded program takes over the `esync` fire
from the single staged command. A wired run uses `sgo` down each cable instead,
released by a `multiprocessing.Barrier` so the per-tag worker processes write
it together rather than however far apart the OS scheduled them. Segmentation
tolerates about ±4 ms of trigger skew (a 30 ms listen window against a 24 ms
sweep); a barrier release costs well under a millisecond.

**A wireless round waits for the fire rather than predicting it.** Nothing is
sent when a scheduled round finishes -- the fire calls `schedRun()` with no
session to answer on -- so the host has to ask. It must not ask once after a
fixed sleep, because `exc.sync()` does not put the preamble on the air, it
queues it: `SyncGate.trigger()` drops the envelope into the flowgraph and it
reaches the antenna whenever GNU Radio's and libbladeRF's buffers have
drained. An individual round never had to care, since its tags sit in
`await_fired_wifi()` for `FIRE_DEADLINE_S`. A fixed sleep did, and got it
wrong -- every tag came back `listening=1 primed=1 samples=7300` with
`peak_rho` at the noise floor, which is what a tag asked before the preamble
existed looks like. So the host polls `esyncr` every `FIRE_POLL_S` up to
`FIRE_DEADLINE_S`, after a quiet period covering the case where the preamble
goes out at once. The per-round log prints `fired after <n>s`, which is mostly
the exciter's buffering.

That poll doubles as knowing when the round is over: `esyncListening()`
latches the report and only then calls `schedRun()`, which blocks, so a tag
cannot answer `esyncr` with a lock in it until the last slot boundary has
passed.

**Sizing.** A listen slot samples on a fixed `SCHED_SAMPLE_US` (25 µs) grid, so
one 3 ms dwell is ~120 samples and a whole sweep is ~960. That pacing is not
cosmetic: left free-running the slot loop owns the CPU and samples 4-8x faster,
which neither the sample pool nor a 20-tag round can pay for. `sched_collect.py`
works the trace length out from the tag count, the slot length and
`SCHED_POOL_SAMPLES`, and refuses a configuration whose slots cannot hold a
whole sweep instead of collecting traces that cannot be segmented.
`SCHED_SLOT_US` and `SCHED_LISTEN_SAMPLES` in `configurations.json` override it.

At the 20-tag ceiling (`SCHED_MAX_SLOTS`): 19 listen slots of 1200 samples =
22800 of the pool's 48000, a round of 1.0 s, and ~46 k samples per tag to read
back. Traces come back as raw ADC codes (`squ_raw`) and are scaled host-side --
it halves what goes over the wire and saves the tag formatting a float per
sample.

**Every channel change is paid for out of the padding, not the trace.** A tag
reads its own rectified carrier, and its own switch state moves that reading by
far more than the backscatter it is measuring, so a listen slot that opens
straight after a channel change records a settling ramp instead of a level. The
slot that used to do this was the one right after the tag's own transmit slot --
ch8 at the end of the sweep, then straight to the capture channel -- and it
drifted +2.7 to +9.9 mV across its first 8 ms against channel contrasts of 2.4
to 17 mV. `schedRun()` now hops to the next slot's channel during the previous
slot's pad and gives it `SCHED_RX_SETTLE_US` to settle, late enough in the pad
that the other tags have finished sampling and never see the switch.

**When a round fails** the whole round is dropped and re-shot, up to
`sched_collect.MAX_ROUND_ATTEMPTS` (10) -- a wireless tag that never locked, a
slot that ran out of pool, a program that came back as something other than
what was loaded, or a trace that will not segment. Nothing partial reaches the
CSV: in this mode a half-kept round is not a missing reading, it is N-1
readings labelled with the wrong pair. The program is reloaded before every
round, which is also what makes a stale result impossible -- a tag that missed
the trigger answers `sqr` with `"pending":0` rather than handing back the
previous round's traces under this round's frequency.

**Firmware commands** (`schedule.cpp`): `sqc` clears, `sqd_<us>` / `sqn_<n>` /
`squ_raw|mv` configure, `sq_mpp` / `sq_lis` append slots in order, `sq` shows
the loaded program, `sgo` runs it now, `sqr` dumps every slot's result.

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
C:\ProgramData
adioconda\python.exe -m pip install pyserial
C:\ProgramData
adioconda\python.exe -m pip install -e data_collection/ribbn_scripts
cd data_collection && C:\ProgramData
adioconda\python.exe collection.py
```

**`EXC_POWER` is TX gain in dB here, not a level**, clamped to 0..89; 12.9
left over from an `rf_gen` run is nearly nothing. Start around 60. Negative
means off on both exciters.

**If the bladeRF is on a different machine**, run
`BladeRFCode/ASK_sync/bladerf_exciter_server.py` there and set `BLADERF_HOST`
(and `BLADERF_PORT`, default 3334) here.

`test()` and the other commands run unsynced over plain TCP; only MPP needs the
sync. Wired runs are unchanged and do not use esync.
