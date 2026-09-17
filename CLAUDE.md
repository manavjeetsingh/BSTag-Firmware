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

**Tag firmware** (`XIAO-ESP32-C6_firmware_wifi/XIAO-ESP32-C6_firmware_wifi.ino`): built and flashed via the Arduino IDE/CLI targeting the "XIAO_ESP32C6" board. `XIAO-ESP32-C6_firmware_wifi/secrets.h` is gitignored and has no checked-in template — create it locally with `#define WIFI_SSID` and `#define WIFI_PASS` before compiling, or the sketch won't build (unless `NET_ENABLED` is 0, see below). Leaving `WIFI_SSID` empty is a runtime switch: WiFi/TCP is still compiled in, but `wifiStart()` never brings the radio up and the tag runs Serial-only. `NET_ENABLED` in `config.h` is the compile-time switch — set it to 0 to strip net.cpp's WiFi.h use and the TCP session slots out of the build entirely (smaller flash/RAM, and `secrets.h` is not needed to compile); the `mac` command still works, reading efuse directly instead of going through the WiFi driver.
- `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32C6 XIAO-ESP32-C6_firmware_wifi`
- `arduino-cli upload -p <port> --fqbn esp32:esp32:XIAO_ESP32C6 XIAO-ESP32-C6_firmware_wifi`

**bladeRF exciter scripts** (`BladeRFCode/*/`): run directly with a Python that has `gnuradio`, `osmosdr`, and `numpy` available (these are GNU Radio/GRC-provided, not pip packages — *not* in `.venv`, so these scripts can't be run or imported from the repo venv). `null_sync/manual_null_exciter.py` is the one the current firmware syncs off — it sends one blank per keypress: `python3 BladeRFCode/null_sync/manual_null_exciter.py`. `null_sync/bladerf_cw.py` is that same transmitter and gate with no trigger attached (`CWExciter`: `set_freq`/`set_pwr`/`blank`), which is how a sweep in `data_collection/` drives the bladeRF — `exciters.make_bladerf` builds one in the run's own process, so `collection.py` must then be run under a GNU Radio python (on the lab box, `C:\ProgramDataadioconda\python.exe`) rather than `.venv`. `null_sync/bladerf_exciter_server.py` wraps that same object in a line protocol on TCP 3334 for the one case that cannot work in process — the tags on one machine and the bladeRF on another — and is reached by `hardware.BladeRFExciter` when `BLADERF_HOST` is set. The other two in `null_sync/` are not sync exciters: `plain_exciter.py` is a bare CW carrier, and `periodic_null_exciter.py` free-runs a 2 s blank — do not run it against an armed tag, since the fall-triggered detector will sync to it and fire 1.95 s early (see below).

**Python host tooling** (`ribbn_scripts/`): installable package, `pip install -e ribbn_scripts` (or use the repo's `.venv`, which already has everything). Import as `ribbn_scripts.hardware_api.hardware`, `ribbn_scripts.processing.*`, `ribbn_scripts.ref_functions.*`. Note `pyproject.toml` declares **no** dependencies, so installing it into a bare environment gives you a package that `ImportError`s on first use — the code needs `pyserial`, `numpy`, `pandas`, `matplotlib`, `scipy`, `scikit-learn`, and (only for `Exciter`/`VNA`) `pyvisa`, all installed separately. There is no test runner configured — `ribbn_scripts/src/ribbn_scripts/test/check_tag_conn.py` is a manual, ad hoc script (and still imports from a stale `scripts.*` path), not an automated test to run in CI.

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

Two size limits shape what the protocol can carry, and both fail loudly rather than truncating. A whole line must fit `CMD_BUF_LEN` (32 B incl. terminator) or `session.cpp` answers `cmd:too long` and drops it — the staged command in `q_<cmd>` spends the same budget, so a long deferred command is the case that hits this. And a deferred reply must fit `QUEUED_REPLY_BUF_LEN` (16 KB, sized for `MAX_ADC_SAMPLES` printed as mV) or `qr` answers `{"info":"qr","pending":1,"err":"overflow"}` instead of the trace — raising `MAX_ADC_SAMPLES` means raising that too.

**Multi-transport sessions** (`session.h`/`session.cpp`): one `Session` slot per transport (slot 0 is always Serial, slots 1..`MAX_TCP_CLIENTS` are TCP clients), each with its own line-assembly buffer. `handleCommand()` is transport-agnostic — it takes whatever `Print &out` its session gives it, plus `session_idx` so streaming commands (`spl`) know where to keep writing.

**Exciter sync (esync)** is the mechanism that makes multiple physically separate tags act in lockstep with the exciter, without a shared clock. `esync` arms a one-shot blank detector, and WiFi is suspended for the listening window (`net.cpp`'s `wifiSuspend()`/`wifiResume()`) because the WiFi/lwIP tasks outrank the sample loop and jitter it by milliseconds.

The exciter sends one blank of a known length (`BladeRFCode/null_sync/manual_null_exciter.py`, mirroring the `ESYNC_*` constants in `config.h`). The tag times off the **falling edge that starts the blank** and fires `ESYNC_FIRE_DELAY_US` later by dead reckoning — landing where the carrier is due back, without needing to see it come back. That edge is simply the first sample at or under `ESYNC_MIN_BASELINE_MV`; since the exciter drives the carrier to the floor every time (measured: ~0.8 mV against a 2 mV threshold), the one absolute test is both the timing reference and the proof the blank is real. `ESYNC_REARM_PCT` is used only for the *return*, which is hysteresis rather than timing.

**The blank length is duplicated, exactly, in two places that cannot check each other**: `ESYNC_FIRE_DELAY_US` in `config.h` and `DROP_MS` in `manual_null_exciter.py` (each scaled by `ESYNC_TIME_SCALE` / `N`, which must also match, with the tag reflashed). This coupling is tighter than it looks: because the tag dead-reckons, a mismatch does not stop it firing, it just fires at the wrong moment — early if the tag's number is low, out past the end of the blank if it's high. Nothing on the air detects this. `esyncr`'s `low_us` is the only check: it reports the blank as actually measured. Read it as an approximation, not an equality — it spans the floor going down to `ESYNC_REARM_PCT` coming back up, two different thresholds, so it under-reads `DROP_MS` by a millisecond or two. What matters is that it is stable and that tags agree; a shared drift of many ms means the two hardcoded lengths have come apart.

Timing off the fall means committing before the blank's length is known, which costs the upper length gate. Only one thing resolves inside the delay window and can still call the fire off: a blank that ends before `ESYNC_BLANK_MIN_US` (`rej_short`). A shallow sag needs no separate check — it never reaches the floor, so it never latches an edge. A blank that runs *long* — an outage, or a scale mismatch — is fired against regardless and only flagged afterwards, as `"long":1` with `"returned":0` on the `esyncr` report. `ESYNC_BLANK_MAX_US` is no longer a gate, only the threshold for that flag. This makes `periodic_null_exciter.py`'s 2 s blank an active hazard rather than merely unsupported: it trips the falling edge and fires 1.95 s before the carrier returns.

When nothing fires, `esyncr` reports `rej_short` plus `min_mv` against `base_mv`, and `armed` (a falling edge latched, fire pending). With the fall detected at the floor these are the only two cases: either the carrier came back too early, or `min_mv` never approached `ESYNC_MIN_BASELINE_MV` and the blank isn't reaching the floor at that tag at all.

Keep the exciter's edges sharp. Because the tag latches at the floor, a ramp moves `t_fall` later by roughly half the ramp — a fixed offset every tag shares rather than jitter, but one that walks the fire off the end of the blank (simulated: a 2 ms ramp fires 1000 µs late; `RAMP_MS = 0` fires exactly on time). `RAMP_MS` is deliberately not scaled with `N` for the same reason (measured: -7.4 ms at N=100 with a scaled ramp, +2 µs with a fixed one).

Because the radio is down when it fires, the reply is captured to RAM instead of a (possibly dead) socket and pulled later with `qr`; on the host side this is `hardware.py`'s `reconnect_wifi()` + `fetch_queued_wifi()` pattern. If nothing arrives within `ESYNC_WIFI_TIMEOUT_MS`, the firmware brings WiFi back up on its own (with a `"sync":"degraded"` notice) so a host isn't locked out over TCP with no path back except Serial.

## Firmware module layout (`XIAO-ESP32-C6_firmware_wifi/`)

The header comment at the top of the `.ino` is the map; module boundaries matter — keep new code in the module it belongs to rather than adding to the `.ino`:

- `config.h` — pin map and every tuning constant (buffer sizes, timeouts, dwell times), plus `NET_ENABLED` (compile WiFi/TCP in at all). Change behavior here first before hardcoding a new constant elsewhere.
- `secrets.h` — WiFi credentials, gitignored, must be created locally unless `NET_ENABLED` is 0
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
