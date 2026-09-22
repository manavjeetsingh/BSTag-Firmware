# ASK sync — explained like you're five

## What problem are we solving?

The old way (`BladeRFCode/null_sync/`), the exciter told the tags "sync up now!"
by doing ONE simple thing: it turns the radio signal almost all the way off
for a short moment, then turns it back on. The tag is constantly checking
"is the signal basically zero right now?" and the moment it sees that, it
says "ah-ha, that's the sync signal" and starts its countdown.

This works great when the signal reaching the tag is nice and strong. But
imagine the tag is far away, or the antenna is pointed a bit wrong, so the
strongest the signal ever gets is only about 10 millivolts — already tiny.
Now ask the tag to tell the difference between:

- "signal is ON, but weak" (10 mV)
- "signal is basically OFF" (the sync moment)
- "random electrical noise" (always a little bit there)

When the ON signal is already almost as small as the noise, "OFF" and
"noisy ON" start to look the same. It's like trying to notice someone
whispered stop in a noisy room — if the whisper is quiet enough, you can't
tell it apart from the background hum. One quick listen isn't good enough
to be sure.

## The idea: stop listening for one moment, listen for a pattern

Instead of one on/off, one blank, this code makes the exciter blink the
signal on and off in a special, known RHYTHM — like a secret knock:

```
knock, knock, knock, knock, knock, (pause), (pause), knock, knock, (pause), knock, (pause), knock
```

That's 13 "beats," each one either ON or OFF, in this exact order:

```
ON ON ON ON ON off off ON ON off ON off ON
```

This isn't a random rhythm — it's a special one called a **Barker code**.
Nobody needs to memorize why it's special, just this: mathematically, it is
about as hard as possible to mix up with random noise or with itself
shifted a little bit in time. It has a very clean, unmistakable "shape."

## Why a rhythm beats a single beep, when things are quiet

If you only get to listen for ONE knock, and the knock is really quiet, you
might miss it, or you might mistake a random bump for it. You basically
have to bet everything on that one moment.

But if you know the FULL secret knock pattern ahead of time — 13 beats,
in that exact ON/OFF order — you don't have to be sure about any single
beat. You listen to the whole 13-beat sequence and check: "does this match
the pattern I was told to expect?" Even if a couple of individual beats are
too quiet or noisy to be sure about on their own, the pattern as a WHOLE
still stands out clearly, because random noise essentially never happens to
draw the exact same 13-beat rhythm by accident.

This trick is basically "don't trust one glance, take one long careful
listen instead" — and it's a big reason radios and radar systems have used
Barker-code-style patterns for decades to find weak signals in noisy
conditions.

## How short can a beat be?

The tag's detector can't jump from ON to OFF instantly. It takes about a
millisecond or two to settle. If a beat is shorter than that, the beats blur
together and the pattern turns to mush. So 2 ms per beat is about as short
as is safe until we've looked at the real signal. To check, run `rdb`, press
Enter on the exciter, then run `rds` and look at the trace: each beat should
have a flat top or a flat bottom. If the beats look like sharp spikes or
smooth hills instead, make them longer.

## Why does this still work when the signal is weak (like 10 mV)?

The old way asks one question about ONE sample: "is it below 2 mV?" At 10 mV
there isn't much room between "on", "off" and noise, so a single noisy
sample can give the wrong answer.

The new way asks: "over these 780 samples, does the wiggle look like my
knock?" Noise is random, so across 780 samples its ups and downs mostly
cancel out. The knock is the same every time, so it adds up. That's why a
10 mV knock can still stand out clearly. The more samples you add up, the
weaker a signal you can find. Making the pattern longer is how you'd buy
more of that later, if you need it.

The tag also doesn't care how big the signal is, only what shape it has.
It subtracts the average level and compares the shape. So a 10 mV knock and
a 500 mV knock look the same to it, and no "2 mV" number needs tuning for
each tag.

The limit: if the knock is much smaller than the noise, even across all 780
samples, nothing will save it. We don't know the tag's real noise level at
10 mV yet. Measuring that with `rdb` is the first thing to do.

## What if something else is making radio noise (interference)?

Depends on what kind:

- **A steady signal that's always on** (another transmitter, or leakage):
  it just lifts the average level. Since the tag subtracts the average, it
  mostly disappears. The catch is that a really strong one can make the
  tag's detector less sensitive, so our knock gets smaller.
- **Bursts that come and go** (RFID readers, LoRa, other 915 MHz stuff):
  this is where the pattern really helps. With the old way, one burst
  that happens to dip, or cover up the blank, gives a false sync or a missed
  one. For a burst to fool the new way, it would have to copy our exact
  13-beat rhythm at our exact beat speed. Random bursts basically never do
  that. What they CAN do is mess up a few beats, which makes the match score
  lower. If the tag requires a good score, a messed-up knock gets ignored
  instead of giving a wrong sync.
- **Something that pulses at the same speed as our beats**: this is the one
  real danger. If it happens, change `CHIP_MS` a little bit to move away
  from it.
- **The signal fading because things move** (people walking, reflections):
  if the fade is much slower than 26 ms, the knock is over before the fade
  matters. That's one more reason to keep the knock short.

One thing is the same as before: this pattern only says WHEN to sync. It
doesn't carry any ID, so a second exciter playing the same knock would
trigger the tags too.

## What's in this folder

- `bladerf_cw.py` is the actual transmitter. The knock pattern and the beat
  length live here, and everything else uses it.
- `manual_ask_sync_exciter.py` plays one knock each time you press Enter.
  Good for testing by hand.
- `bladerf_exciter_server.py` lets a computer on the network ask for a knock,
  for when the bladeRF is plugged into a different computer from the tags.
  Normally `data_collection/` drives the bladeRF itself and doesn't need this.

The manual one:

1. Sends a normal, steady radio signal (like before).
2. When you press Enter, it plays the 13-beat Barker knock pattern
   (`ON ON ON ON ON off off ON ON off ON off ON`), each beat lasting
   `CHIP_MS` milliseconds (2 ms by default, so 26 ms total, which is shorter
   than the old 50 ms blank). The tag takes about 30 samples per ms, so it
   sees about 60 samples per beat.
3. Goes back to a steady signal afterward.
4. Press `q` + Enter to stop.

Run it the same way you'd run `manual_null_exciter.py`:

```
python3 BladeRFCode/ASK_sync/manual_ask_sync_exciter.py
```

(needs a GNU Radio Python with `gnuradio` + `osmosdr`, same as everything
else in `BladeRFCode/` — see the repo's top-level `CLAUDE.md`.)

## How the tag listens for it

The tag listens for the knock when you send it `esync`. It no longer
listens for the old single blank, so `null_sync/` exciters won't sync it.

What the tag does, step by step:

1. It takes its ~30 samples per ms and drops them into little buckets, one
   bucket per 0.1 ms, averaging each bucket. That way it has a neat,
   evenly spaced list, even when its loop runs a bit unevenly.
2. It always looks at the last 26 ms of buckets (one knock's worth) and
   asks "how much does this look like the knock?" The answer is a score
   from 0 (nothing like it) to 1 (a perfect match). It only cares about
   shape, not size.
3. When the score goes above 0.80 and the ON beats are at least 1 mV above
   the OFF beats, it remembers that moment. It waits 1 ms to make sure the
   score really peaked there and isn't about to get even higher.
4. The peak is the moment the knock ended. 5 ms after that, the tag runs
   the command you queued with `q_<cmd>`, same as before.

Ask `esyncr` afterward and you get the score (`rho`), the ON and OFF
levels, and `resid_mv`, which is the leftover noise. `swing_mv` against
`resid_mv` tells you how much room you had. If nothing fired, `esyncr`
shows `peak_rho`, the best score it saw. If that's 0.6 or 0.7, the knock
was heard but was too noisy (or the settings don't match). If it's 0.2,
the tag never heard it at all.

## What the computer test showed

Before flashing anything, we ran the tag's detector code on a laptop with
fake signals (jumpy timing, a detector that smooths edges, and noise):

- 10 mV knock, with noise up to 3 mV per sample: found every time.
- 5 mV knock with 2 mV noise: found most of the time. 3 mV: not found.
- A steady +20 mV interferer: no difference at all.
- Plain carrier, old-style 50 ms blanks, random bursts, square waves:
  never mistaken for the knock.
- Random bursts landing ON the knock: the knock is often missed. But then
  the tag just doesn't fire, which is way better than firing at the wrong
  time.
- Nasty stuff, like 100 random dropouts every second, or someone sending
  random on/off data at our exact beat speed: fooled it a couple of times.
  Raise `ESYNC_MIN_RHO` to 0.85 to stop that, but then 5 mV knocks stop
  working. That's the tradeoff.

When the tag fires, it's always about 0.3 ms late, but every tag is late
by the same amount, so they still line up with each other. Tag-to-tag
wobble was about 20–80 µs. **One catch:** that 0.3 ms depends on how fast the
tag's detector responds. A tag with a slower detector was 0.6 ms late in the
test. So tags built differently won't agree perfectly. Compare `t_us`
across tags to check.

## Things that must match on both sides

- `BARKER_CODE` in `bladerf_cw.py` must equal `ESYNC_CODE` in `config.h`.
- `CHIP_MS` in `bladerf_cw.py` must equal `ESYNC_CHIP_US / 1000`.

If they don't match, the tag never fires (it's never fooled into firing at
the wrong time). So if `esync` never fires, check these first.

This is all simulated. None of it has run on a real tag yet. The first
real test: flash, send `esync`, press Enter on the exciter, then `esyncr`.
