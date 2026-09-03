# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RIBBN (backscatter RF tag) hardware-in-the-loop system, codenamed "BSTag" / "TagV93". Three parts that only make sense together:

1. **Exciter** (`BladeRFCode/`) — a bladeRF, driven via GNU Radio + `osmosdr`, transmits a CW carrier at 915 MHz that powers/illuminates the tags and modulates a data signal.
2. **Tag firmware** (`XIAO-ESP32-C6_firmware_wifi/`, `XIAO-ESP32-C6_firmware/`) — Arduino sketches for a Seeed XIAO ESP32-C6 on each physical tag. The tag switches its RF reflection state across 8 channels and samples an ADC over SPI, exposing everything through a line-based text/JSON command protocol delivered over Serial and/or WiFi TCP.
3. **Host tooling** (`ribbn_scripts/`, `Testing/`) — Python that drives one or many tags (and optionally the bladeRF/VNA) over that command protocol to run experiments, capture ADC traces, and post-process them (offset/phase estimation, localization).

`reference_code/tagV93_esp32c6.cpp` is the earlier monolithic (Serial-only) firmware that `XIAO-ESP32-C6_firmware_wifi/` was decomposed and extended from — useful as a reference for "how did the old version do X," not for editing directly.

Active development is in `XIAO-ESP32-C6_firmware_wifi/` (see `git status` — this is the directory that changes). `XIAO-ESP32-C6_firmware/` is the older Serial-only sketch and generally trails it.

## Build / run commands

There is no unified build system — each part has its own toolchain and there is no CI in this repo.

**Tag firmware** (`XIAO-ESP32-C6_firmware_wifi/XIAO-ESP32-C6_firmware_wifi.ino`): built and flashed via the Arduino IDE/CLI targeting the "XIAO_ESP32C6" board. Copy `secrets.h.example`-style credentials into a gitignored `XIAO-ESP32-C6_firmware_wifi/secrets.h` (defines `WIFI_SSID`/`WIFI_PASS`) before compiling — the sketch won't build without it. Leaving `WIFI_SSID` empty disables WiFi at compile time and the tag runs Serial-only.
- `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32C6 XIAO-ESP32-C6_firmware_wifi`
- `arduino-cli upload -p <port> --fqbn esp32:esp32:XIAO_ESP32C6 XIAO-ESP32-C6_firmware_wifi`

**bladeRF exciter scripts** (`BladeRFCode/*/`): run directly with a Python that has `gnuradio`, `osmosdr`, and `numpy` available (these are GNU Radio/GRC-provided, not pip packages — *not* in `.venv`, so these scripts can't be run or imported from the repo venv). `null_sync/` holds the older bare-drop exciters; `manchester_sync/manual_sync.py` sends the framed sync packet the current firmware expects. e.g. `python3 BladeRFCode/manchester_sync/manual_sync.py`.

**Python host tooling** (`ribbn_scripts/`): installable package, `pip install -e ribbn_scripts` (or use the repo's `.venv`). Import as `ribbn_scripts.hardware_api.hardware`, `ribbn_scripts.processing.*`, `ribbn_scripts.ref_functions.*`. There is no test runner configured — `ribbn_scripts/src/ribbn_scripts/test/check_tag_conn.py` is a manual, ad hoc script (and currently has a stale import path), not an automated test to run in CI.

There are no lint configs in the repo; match the surrounding code's style rather than introducing a formatter.

## Tag command protocol

This is the interface everything else is built on — most host-side and firmware changes are really changes to this protocol, so read `commands.cpp`'s `printHelp()` and `handleCommand()` before adding a command. Lines are sent terminated with `\r\n` (or `\0\n` from some older Python call sites) over Serial and/or a TCP socket on port 3333 (`TCP_PORT` in `config.h`), and replies are either plain text (`ch: 2, ok`) or a single-line JSON blob (`{"info":"adc",...}`). Key commands:

- `ch_<1-8>` — switch RF/tag channel
- `adc`, `adc_<n>`, `adcraw`, `adcraw_<n>` — one-shot/burst ADC reads (scaled mV or raw codes)
- `rdb` / `rds` — start/stop+dump a buffered capture (10000 samples, forced to `CAPTURE_CHANNEL`)
- `spl` / `epl` — start/stop a live plotter stream (`mV,d0_level` lines, for Arduino Serial Plotter)
- `mpp` / `mpp_<n>` — run a max-power-point channel sweep
- `esync` / `esyncs` / `esyncr` — arm/disarm/report the exciter-sync edge detector (see below)
- `q_<cmd>` / `q` / `qc` / `qr` — stage a command to fire on the next esync edge, and pull its deferred reply
- `net`, `mac`, `wifi_off`, `wifi_on`, `d0`, `help`

**Multi-transport sessions** (`session.h`/`session.cpp`): one `Session` slot per transport (slot 0 is always Serial, slots 1..`MAX_TCP_CLIENTS` are TCP clients), each with its own line-assembly buffer. `handleCommand()` is transport-agnostic — it takes whatever `Print &out` its session gives it, plus `session_idx` so streaming commands (`spl`) know where to keep writing.

**Exciter sync (esync)** is the mechanism that makes multiple physically separate tags act in lockstep with the exciter, without a shared clock. `esync` arms a one-shot packet detector, and WiFi is suspended for the listening window (`net.cpp`'s `wifiSuspend()`/`wifiResume()`) because the WiFi/lwIP tasks outrank the sample loop and jitter it by milliseconds.

The exciter sends a framed packet, not a bare drop: a 40 ms SFD blank, a guard, then 4 Manchester-encoded bits carrying an id (`BladeRFCode/manchester_sync/manual_sync.py`, mirroring the `ESYNC_*` constants in `config.h` — the packet shape must be edited in both places together). Detection and timing are deliberately split:

- The SFD is a **Manchester code violation** — no run inside the payload can exceed one bit period — so it marks the frame start unambiguously however late a tag started listening. Its rising edge (`t_sfd`) is the single timing reference.
- The payload is **validation only**. Both halves of every bit must differ, which a fade or stray dropout won't produce. The id lets a run confirm every tag locked onto the *same* packet by comparing what each reports via `esyncr`.
- The queued command fires at `ESYNC_FIRE_OFFSET_US` past `t_sfd`, **not** on a payload edge, so all tags fire at the same instant regardless of when their decode finished.

Every rejection path returns to idle with the listener still armed — a tag that misfires on a half-seen packet would act at a time no other tag shares, which is worse than one that never fired. `esyncr` reports per-cause rejection tallies (`rej_sfd`/`rej_chip`/`rej_id`/`rej_late`) to diagnose a run that never triggers.

Because the radio is down when it fires, the reply is captured to RAM instead of a (possibly dead) socket and pulled later with `qr`; on the host side this is `hardware.py`'s `reconnect_wifi()` + `fetch_queued_wifi()` pattern. If nothing arrives within `ESYNC_WIFI_TIMEOUT_MS`, the firmware brings WiFi back up on its own (with a `"sync":"degraded"` notice) so a host isn't locked out over TCP with no path back except Serial.

## Firmware module layout (`XIAO-ESP32-C6_firmware_wifi/`)

The header comment at the top of the `.ino` is the map; module boundaries matter — keep new code in the module it belongs to rather than adding to the `.ino`:

- `config.h` — pin map and every tuning constant (buffer sizes, timeouts, dwell times). Change behavior here first before hardcoding a new constant elsewhere.
- `secrets.h` — WiFi credentials, gitignored, must be created locally
- `hardware.*` — power rail, LEDs, RF switch, ADC-over-SPI
- `acquisition.*` — buffered capture, plotter stream, MPP sweep; owns `pathIsBusy()`, the guard against overlapping RF-path users
- `esync.*` — exciter sync listener and edge detector
- `commands.*` — the text/JSON protocol, transport-agnostic (takes `Print &`)
- `session.*` — per-transport line assembly and dispatch, drives `commands.cpp`
- `net.*` — WiFi link management and the TCP command server, including suspend/resume for esync
- `buffered_out.h` — TX-coalescing `Print` wrapper used when writing larger replies

`loop()` in the `.ino` is the scheduler: service WiFi/TCP, poll every session for a complete line, then step whichever of capture/plotter/esync is currently active. The esync suspend/resume dance around `wifiSuspend()`/`wifiResume()` has real ordering constraints explained inline — read the comments there before touching it.

## Host tooling (`ribbn_scripts/`)

`hardware_api/hardware.py` has the `Tag` class (talks the command protocol above — every Serial method `foo()` has a `foo_wifi()` counterpart using the raw TCP socket instead of `pyserial`), plus `Exciter` (GPIB signal generator control) and `VNA` (GPIB network analyzer control) — both of the latter `import pyvisa` lazily in `__init__` since they're only needed on machines with that lab hardware attached. `processing/` (offset/phase estimation, localization) and `ref_functions/` are pure-Python analysis run on captured ADC traces, independent of live hardware.

`Testing/*.ipynb` and `Testing/testing_wifi.py` are experiment notebooks/scripts against real tag hardware — treat them as usage examples of the `Tag` API, not as library code to import from.
