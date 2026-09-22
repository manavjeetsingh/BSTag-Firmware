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

**bladeRF exciter scripts** (`BladeRFCode/*/`): run directly with a Python that has `gnuradio`, `osmosdr`, and `numpy` available (these are GNU Radio/GRC-provided, not pip packages — *not* in `.venv`, so these scripts can't be run or imported from the repo venv). `ASK_sync/manual_ask_sync_exciter.py` is the one the current firmware syncs off — it sends one Barker-coded preamble per keypress: `python3 BladeRFCode/ASK_sync/manual_ask_sync_exciter.py`. Both it and a sweep in `data_collection/` are built on `ASK_sync/bladerf_cw.py` (`CWExciter`: `set_freq`/`set_pwr`/`sync`), which owns `BARKER_CODE`/`CHIP_MS` — `exciters.make_bladerf` builds one in the run's own process, so `collection.py` must then be run under a GNU Radio python (on the lab box, `C:\ProgramDataadioconda\python.exe`) rather than `.venv`. `ASK_sync/bladerf_exciter_server.py` wraps that same object in a line protocol on TCP 3334 for the one case that cannot work in process — the tags on one machine and the bladeRF on another — and is reached by `hardware.BladeRFExciter` when `BLADERF_HOST` is set. Everything in `null_sync/` sends the old single blank, which the firmware no longer listens for: `manual_null_exciter.py`, `plain_exciter.py` (a bare CW carrier), and `periodic_null_exciter.py`. A GPIB `rf_gen` can light the tags but cannot key the preamble, so wireless runs need `EXCITER: bladerf`.

**Python host tooling** (`ribbn_scripts/`): installable package, `pip install -e ribbn_scripts` (or use the repo's `.venv`, which already has everything). Import as `ribbn_scripts.hardware_api.hardware`, `ribbn_scripts.processing.*`, `ribbn_scripts.ref_functions.*`. Note `pyproject.toml` declares **no** dependencies, so installing it into a bare environment gives you a package that `ImportError`s on first use — the code needs `pyserial`, `numpy`, `pandas`, `matplotlib`, `scipy`, `scikit-learn`, and (only for `Exciter`/`VNA`) `pyvisa`, all installed separately. There is no test runner configured — `ribbn_scripts/src/ribbn_scripts/test/check_tag_conn.py` is a manual, ad hoc script (and still imports from a stale `scripts.*` path), not an automated test to run in CI.

There are no lint configs in the repo; match the surrounding code's style rather than introducing a formatter.

## Tag command protocol

This is the interface everything else is built on — most host-side and firmware changes are really changes to this protocol, so read `commands.cpp`'s `printHelp()` and `handleCommand()` before adding a command. Lines are sent terminated with `\r\n` (or `\0\n` from some older Python call sites) over Serial and/or a TCP socket on port 3333 (`TCP_PORT` in `config.h`), and replies are either plain text (`ch: 2, ok`) or a single-line JSON blob (`{"info":"adc",...}`). Key commands:

- `ch_<1-8>` — switch RF/tag channel
- `adc`, `adc_<n>`, `adcraw`, `adcraw_<n>` — one-shot/burst ADC reads (scaled mV or raw codes)
- `rdb` / `rds` — start/stop+dump a buffered capture (10000 samples, forced to `CAPTURE_CHANNEL`)
- `spl` / `epl` — start/stop a live plotter stream (`mV,d0_level` lines, for Arduino Serial Plotter)
- `mpp` / `mpp_<n>` — run a max-power-point channel sweep
- `esync` / `esyncs` / `esyncr` — arm/disarm/report the exciter-sync preamble detector (see below)
- `q_<cmd>` / `q` / `qc` / `qr` — stage a command to fire on the next esync lock, and pull its deferred reply
- `net`, `mac`, `wifi_off`, `wifi_on`, `d0`, `help`

Two size limits shape what the protocol can carry, and both fail loudly rather than truncating. A whole line must fit `CMD_BUF_LEN` (32 B incl. terminator) or `session.cpp` answers `cmd:too long` and drops it — the staged command in `q_<cmd>` spends the same budget, so a long deferred command is the case that hits this. And a deferred reply must fit `QUEUED_REPLY_BUF_LEN` (16 KB, sized for `MAX_ADC_SAMPLES` printed as mV) or `qr` answers `{"info":"qr","pending":1,"err":"overflow"}` instead of the trace — raising `MAX_ADC_SAMPLES` means raising that too.

**Multi-transport sessions** (`session.h`/`session.cpp`): one `Session` slot per transport (slot 0 is always Serial, slots 1..`MAX_TCP_CLIENTS` are TCP clients), each with its own line-assembly buffer. `handleCommand()` is transport-agnostic — it takes whatever `Print &out` its session gives it, plus `session_idx` so streaming commands (`spl`) know where to keep writing.

**Exciter sync (esync)** is the mechanism that makes multiple physically separate tags act in lockstep with the exciter, without a shared clock. `esync` arms a one-shot preamble detector, and WiFi is suspended for the listening window (`net.cpp`'s `wifiSuspend()`/`wifiResume()`) because the WiFi/lwIP tasks outrank the sample loop and jitter it by milliseconds.

The exciter (`BladeRFCode/ASK_sync/bladerf_cw.py`) keys the carrier on/off through a Barker-13 code, 2 ms per chip, 26 ms in all. The tag averages its samples into `ESYNC_BIN_US` time bins, so it works on a uniform grid however unevenly `loop()` runs. It then Pearson-correlates the last preamble's worth of bins against the mean-removed code. It locks on the correlation peak, interpolated between bins, which marks the preamble's end. The lock needs ρ ≥ `ESYNC_MIN_RHO`, on-minus-off ≥ `ESYNC_MIN_SWING_MV`, and the peak must stay the best for `ESYNC_CONFIRM_US`. The queued command then fires `ESYNC_FIRE_DELAY_US` later, into steady carrier. The score depends on shape, not level, so there is no per-tag mV floor to tune, and a steady offset or interferer cancels out. See `BladeRFCode/ASK_sync/README.md` for the plain-language version and the simulation results the defaults came from.

**The code and chip length are duplicated in two places**: `ESYNC_CODE`/`ESYNC_CHIP_US` in `config.h` and `BARKER_CODE`/`CHIP_MS` in `bladerf_cw.py`. A mismatch fails safe, since the tag never correlates, so it never fires. But it looks exactly like a tag that cannot hear the exciter. Check these first when `esyncr`'s `peak_rho` stays low.

`ESYNC_MIN_RHO` is the one real tradeoff. At 0.80 (default), simulation locked 10 mV preambles every time with up to 3 mV/sample noise, but random on/off traffic at the chip rate occasionally fooled it. At 0.85 that stops, and ~5 mV preambles stop locking too. Bursts that land on the preamble cost the lock, but they produce a miss, never a wrong-time fire.

The fire lands a fixed few hundred µs after the true preamble end. That offset tracks the tag's detector time constant (simulated: +330 µs at τ = 0.5 ms, +625 µs at 1 ms), not the signal level. So tags with the same front end agree, and tags with different ones disagree by the difference. Compare `t_us` across tags.

`esyncr` after a fire reports `t_us` (the lock), `fire_us`, `rho`, `on_mv`/`off_mv`/`swing_mv`, `resid_mv` (per-bin noise the code does not explain) and `snr_db`. When nothing has fired, it reports `primed`, `armed` (locked, fire pending), `stalls` (loop gaps longer than a preamble, which restart the window) and `peak_rho`, the best score seen. A `peak_rho` near the threshold means the preamble was heard but too noisy. Near zero means it never arrived, or the code or chip length does not match.

Because the radio is down when it fires, the reply is captured to RAM instead of a (possibly dead) socket and pulled later with `qr`; on the host side this is `hardware.py`'s `reconnect_wifi()` + `fetch_queued_wifi()` pattern. If nothing arrives within `ESYNC_WIFI_TIMEOUT_MS`, the firmware brings WiFi back up on its own (with a `"sync":"degraded"` notice) so a host isn't locked out over TCP with no path back except Serial.

## Firmware module layout (`XIAO-ESP32-C6_firmware_wifi/`)

The header comment at the top of the `.ino` is the map; module boundaries matter — keep new code in the module it belongs to rather than adding to the `.ino`:

- `config.h` — pin map and every tuning constant (buffer sizes, timeouts, dwell times), plus `NET_ENABLED` (compile WiFi/TCP in at all). Change behavior here first before hardcoding a new constant elsewhere.
- `secrets.h` — WiFi credentials, gitignored, must be created locally unless `NET_ENABLED` is 0
- `hardware.*` — power rail, LEDs, RF switch, ADC-over-SPI
- `acquisition.*` — buffered capture, plotter stream, MPP sweep; owns `pathIsBusy()`, the guard against overlapping RF-path users
- `esync.*` — exciter sync listener and ASK preamble correlator
- `commands.*` — the text/JSON protocol, transport-agnostic (takes `Print &`)
- `session.*` — per-transport line assembly and dispatch, drives `commands.cpp`
- `net.*` — WiFi link management and the TCP command server, including suspend/resume for esync
- `buffered_out.h` — TX-coalescing `Print` wrapper used when writing larger replies

`loop()` in the `.ino` is the scheduler: service WiFi/TCP, poll every session for a complete line, then step whichever of capture/plotter/esync is currently active. The esync suspend/resume dance around `wifiSuspend()`/`wifiResume()` has real ordering constraints explained inline — read the comments there before touching it.

## Host tooling (`ribbn_scripts/`)

`hardware_api/hardware.py` has the `Tag` class (talks the command protocol above — every Serial method `foo()` has a `foo_wifi()` counterpart using the raw TCP socket instead of `pyserial`), plus `Exciter` (GPIB signal generator control) and `VNA` (GPIB network analyzer control) — both of the latter `import pyvisa` lazily in `__init__` since they're only needed on machines with that lab hardware attached. `processing/` (offset/phase estimation, localization) and `ref_functions/` are pure-Python analysis run on captured ADC traces, independent of live hardware.

`Testing/*.ipynb` and `Testing/testing_wifi.py` are experiment notebooks/scripts against real tag hardware — treat them as usage examples of the `Tag` API, not as library code to import from.
