# `mpp_segment.py` — locating the MPP channel dwells in a capture

This module answers one question: **given a captured ADC trace, which samples belong to which RF channel?**

Everything downstream depends on getting that right. `cal_theta` builds its least-squares fit from one amplitude per channel, so if the windows are misplaced the channel labels are wrong, the amplitudes are attributed to the wrong reflection coefficients, and the phase comes out wrong — without anything looking broken.

---

## 1. What the tag actually does

During `perform_mpp()` the transmitting tag steps through `MPP_CHANNELS` from `acquisition.cpp`, holding `MPP_DWELL_US` (3 ms) on each entry:

```c
static const uint8_t MPP_CHANNELS[] = {1, 1, 1, 1,  3, 4, 6, 7, 8};
```

Nine dwells, 27 ms total. Note ch1 appears **four times**. Meanwhile the receiving tag is parked on `CAPTURE_CHANNEL` and free-running its ADC at ~66.7 kSa/s, so one 3 ms dwell is **200 samples**.

A capture therefore looks like this, with variable lead-in and tail around the sweep:

```
sample 0                                                                    ~1900
|                                                                               |
[--- lead-in ---][ ch1 ][ ch1 ][ ch1 ][ ch1 ][ ch3 ][ ch4 ][ ch6 ][ ch7 ][ ch8 ][- tail -]
                 \_____________  ______________/ \______________  ______________/
                               \/                               \/
                    padding (3 dwells, unmeasured)      the 6 measured windows
                                        ^
                                        |
                              start  =  the 4th ch1 dwell
```

Only the last six dwells are measured — `ch1, 3, 4, 6, 7, 8`, matching the `CHANNELS` list in `configurations.json`. The three leading ch1 dwells are padding, originally there to absorb the rectifier's settling transient at the start of a capture.

That redundancy turns out to be load-bearing for this algorithm. See §4.

---

## 2. Why the previous approach failed

The boundaries used to be derived from the host's wall clock. `perform_mpp()` brackets a blocking serial call:

```python
mpp_start_time = time.time()
self.ser.write(f"mpp_{passes}\0\n".encode())
self._read_until_contains(...)          # waits for the tag's ack
mpp_end_time = time.time()
```

`getChannelVoltage` then assumed the samples spanned exactly `mpp_stop_time - mpp_start_time`, and stepped back 3 ms at a time from the end of that window.

The problem: that measurement includes the USB-serial round trip at **both** ends, plus `readline` poll granularity and OS scheduling. Measured across 40 captures of a sweep the firmware fixes at exactly 27 ms:

| quantity | value |
|---|---|
| firmware sweep duration | 27 ms (fixed) |
| host-measured `elapsed` | 26.7 – 30.3 ms |
| jitter | **3.6 ms ≈ 1.2 dwells** |

Because the grid was anchored to the *end* of that window, any inflation slid every boundary rightward and shifted every channel label by one position.

**Measured failure rate: 18 of 40 captures (45%).** The check uses the ch1 padding as ground truth — the padding dwells and the measured ch1 window are the same channel, so their levels must agree; where they disagreed, the grid had slipped.

The tag's own sampling was never the problem. The dwell measures exactly 200 samples in every capture, with zero spread. All the instability lived in the host's clock.

---

## 3. Reducing the problem to one unknown

The dwell length is not really unknown — it is hardware. 3 ms at the tag's sample-loop rate is 200 samples, confirmed empirically across every capture. So the grid is **rigid**: six blocks of known width, laid end to end. The only thing left to find is *where it starts*.

```
[--- lead-in ---][ch1][ch1][ch1][ch1][ch3][ch4][ch6][ch7][ch8][- tail -]
                            ^prev ^start
                            |     |
                            |     the six blocks we want
                            the padding block just before
```

That single unknown is found by **brute force**: try every possible start index, score how much the resulting layout looks like a real sweep, and keep the best. That is the whole algorithm — the rest of this section is why the grid is rigid, and §4 is the scoring.

`DWELL_TOLERANCE` (±3%) widens the search to dwells of 194–206 samples, purely to absorb drift if the firmware's sample loop rate ever shifts. Results are insensitive to it.

Fitting the dwell per-trace as a genuinely free parameter was tried and is strictly worse (§6): it is degenerate, because a slightly-wrong dwell that straddles boundaries still produces large differences between blocks.

---

## 4. The scoring function

For a candidate start `s` and dwell `D`, lay down six consecutive blocks `B0…B5` covering the measured windows, plus the padding block `prev = [s-D, s)` immediately before. Take the **mean voltage of each of those seven blocks**:

```
 prev    B0     B1     B2     B3     B4     B5
  |      |      |      |      |      |      |
        [s]
```

Then:

```
score(s, D) =   |B1-B0| + |B2-B1| + |B3-B2| + |B4-B3| + |B5-B4|      (A)  reward big steps inside
              - |B0 - prev|                                          (B)  punish a step at the left edge
```

Pick the `(s, D)` that **maximises** it. One idea per line:

**(A) — the five internal boundaries are real channel switches, so the voltage should jump at each one.** A correct alignment collects all five jumps. This guards against the grid sliding one dwell **too far left**: if it does, `B0` and `B1` both land on ch1, that boundary contributes nothing, and only 4 real steps are captured instead of 5.

**(B) — `prev` and `B0` are both ch1, so that one boundary must be flat.** This guards against sliding one dwell **too far right**: if it does, `B0` lands on ch3 while `prev` is still ch1, producing a large step exactly where there should be none.

Term (A) alone cannot tell you that the grid slid right — you would still see five large jumps, just the wrong five. Term (B) is what pins the phase, and it is only valid because of the four-fold ch1 repeat: the padding dwell is *guaranteed* to be the same channel as the first measured window, making that the one boundary in the whole sweep that must be flat. **The padding that looks like a settling hack is what anchors the grid's phase.**

### Worked example

Say the true block levels are ch1 = 10, ch3 = 14, ch4 = 9, ch6 = 12, ch7 = 7, ch8 = 11 mV, with the tail sitting at 11.

| alignment | the six blocks | `prev` | (A) rewards | (B) penalty | **score** |
|---|---|---|---|---|---|
| **correct** | 10, 14, 9, 12, 7, 11 | 10 (ch1) | 4+5+3+5+4 = 21 | \|10−10\| = 0 | **21** |
| one dwell too far left | 10, 10, 14, 9, 12, 7 | 10 (ch1) | 0+4+5+3+5 = 17 | \|10−10\| = 0 | **17** |
| one dwell too far right | 14, 9, 12, 7, 11, 11 | 10 (ch1) | 5+3+5+4+0 = 17 | \|14−10\| = 4 | **13** |

Sliding left wastes a boundary on ch1→ch1, which contributes 0. Sliding right both wastes the ch8→tail boundary *and* eats the penalty. The correct alignment wins on both counts, and the right-shift — the failure mode the old wall-clock method actually had — loses by the larger margin.

---

## 5. Why it survives low SNR

The algorithm compares **block averages, never adjacent samples**.

This is the difference between working and not working on weak captures. A naive "find the largest single-sample jump" edge detector is fine when the channel steps are 10–20 mV, but collapses when the whole trace spans 2 mV and the largest step (0.9 mV) is comparable to the sample-to-sample noise (~0.5 mV).

Each block holds ~184 usable samples after trimming, so noise on the block mean falls by √184 ≈ 13.6×. A 0.5 mV step that is invisible in a differenced signal becomes a confident detection once integrated. This is the single reason the module works at all on the 2 mV set.

Three implementation details:

- **A cumulative sum makes each candidate O(1).** `cs = np.cumsum(v)` is built once, after which any block's mean is `(cs[b] - cs[a]) / (b - a)` rather than a fresh 200-sample average. `starts` is also an array, so every candidate position at a given dwell is scored in one vectorised pass. Together these are why the whole search runs 40 traces in well under a second despite being exhaustive.
- **Means for the search, medians for the result.** Noise here is symmetric, so means are unbiased and cheap. The values actually reported are medians, which resist the occasional outlier sample.
- **`EDGE_TRIM = 8`** drops 8 samples either side of every boundary, since those catch the switch transition rather than a settled level.

---

## 6. Validation

Measured as run-to-run repeatability of the channel pattern: for a fixed link and frequency, the six channel medians should repeat across runs. Common-mode DC drift is removed first, so this measures *alignment* rather than the tag's slow drift. Higher is better.

| dataset | trace span | wall-clock | this module |
|---|---|---|---|
| high SNR | ~90 mV | 2.14 | **211.9** |
| low SNR | ~2 mV | 2.71 | **9.22** |

On the high-SNR set, where large steps give independent ground truth, the fit agrees with directly-detected edges in **39/40** captures.

The low-SNR figure is limited by genuine measurement noise, not by alignment — the channel pattern itself is only ~1.5 mV there. More MPP repetitions would average it down.

### Approaches that were tried and rejected

Recorded so they are not re-attempted:

- **Largest single-sample steps (edge detection).** Works at high SNR (100% on the 90 mV set) but fails on 2 mV captures where step size ≈ noise.
- **Fitting the dwell per-trace as a free parameter.** Degenerate — wandered across 185–216 samples and scored *worse*, because a misaligned dwell straddling boundaries still yields large block differences.
- **Minimising within-block variance (ordinary least-squares piecewise-constant fit).** The textbook choice, and actively wrong here: it is happiest parking all six blocks inside the flat lead-in, where variance is lowest and there is no sweep at all. Scored 2.13 — no better than the original bug. The objective must *seek* the steps, not smooth them away.

---

## 7. Assumptions, and when this breaks

| assumption | consequence if violated |
|---|---|
| At least some adjacent channels differ measurably | If two adjacent channels reflect near-identically, that boundary contributes nothing to term (A) and the fit leans on the remaining four. The wall-clock method fails such cases too. |
| `MPP_CHANNELS` begins with repeated ch1 | Term (B) becomes invalid. If the firmware's channel list changes, this module must change with it. |
| Dwell ≈ `DWELL_SAMPLES` ±3% | A larger shift in the ADC loop rate needs `DWELL_SAMPLES` or `DWELL_TOLERANCE` updated. |
| The trace contains ≥ 7 dwells | Raises `ValueError`; shorter captures cannot be segmented. |

`DWELL_SAMPLES` is coupled to two firmware facts — `MPP_DWELL_US` in `config.h` and the ADC loop rate. Nothing checks that coupling automatically, so if either changes, the symptom is silently misaligned windows.

---

## 8. API

```python
from ribbn_scripts.processing.mpp_segment import segment_capture

medians, per_channel, (start, dwell, score) = segment_capture(voltages, channels)
```

- `medians` — `{channel: median_mV}`, the values fed to `cal_theta`
- `per_channel` — `{channel: np.ndarray}` of the trimmed samples in each window
- `(start, dwell, score)` — the fitted grid; `score` is the objective value, useful as a confidence signal

Lower-level entry points: `fit_sweep_grid(voltages, n_measured)` returns `(start, dwell, score)` alone, and `channel_windows(start, dwell, channels)` maps those to `{channel: (lo, hi)}` sample indices.

### Callers

Both the live pipeline and the offline plots go through this module, deliberately — so a trace plot can never disagree with what the measurement pipeline actually used:

- `measurePhasesMultiThreadedMultiTags.getChannelVoltage()` — live collection. It still accepts `mpp_start_time`/`mpp_stop_time` for the caller's bookkeeping, but they no longer influence the boundaries.
- `post_processing.segment_trace()` — the voltage-trace PDF. Wall-clock timing there only scales the x-axis for display.
