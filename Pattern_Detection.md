# Pattern Detection

How recurring segments are detected in a set of media files, and how their
timestamps are recovered. This document is self-contained — it describes only
the detection algorithm, independent of where the files come from or what is
done with the results.

The problem it solves: given several files that share some **repeated audio**
(an intro, an outro, a recurring sponsor read), find those shared stretches and
report **start/end timestamps** for each file — even though the repeated audio
may sit at a different offset in every file and is never byte-identical between
files.

The implementation lives in `podcache/repetition.py` (detection) and
`podcache/profile.py` (matching a *known* pattern against a new file).

---

## 1. Overview

```
 audio file ──► fingerprint (Chromaprint)  ──►  list of 32-bit "items"
                                                 (~8 items / second)

 for each pair of files:
     seed candidate alignments (offset vote on a masked key)
     verify each alignment item-by-item with Hamming distance
     → coverage bitmap: which items of file A also occur in file B

 a position is "recurring" if it is covered in ≥ N other files
 contiguous runs of recurring positions  ──►  (start, end) in items
 item index × seconds-per-item           ──►  (start, end) in seconds
```

The pipeline has two layers:

1. **A fingerprint** turns audio into a fixed-rate stream of integers where each
   integer summarises a short slice of audio.
2. **A matcher** finds where two such streams agree, tolerant to the small
   bit-level differences that re-encoding introduces.

Everything else (cross-file recurrence, timestamp recovery, applying a saved
pattern) is built on those two pieces. The matcher is **agnostic to what the
fingerprint represents**, which is what makes the same code reusable for video
(see §9).

---

## 2. Fingerprinting

We use **Chromaprint** (the `fpcalc` tool from the AcoustID project) in raw mode:

```
fpcalc -raw -length 100000 <file>
```

It prints:

```
DURATION=3597
FINGERPRINT=391587,391591,395687,...     # comma-separated 32-bit integers
```

Properties that matter:

- The fingerprint is a **sequence of 32-bit integers**, one roughly every
  **0.1238 s** of audio (~8.06 items/second). The rate is fixed by Chromaprint's
  analysis frame/hop, independent of the file.
- Each item is a compact hash of the spectral content of one short window. The
  **same audio produces near-identical items**; different audio produces
  unrelated items.
- It is **robust to re-encoding** by design — but not bit-exact. The same intro
  encoded into two different episodes yields items that differ by **a few bits**
  per 32-bit value. This single fact dictates the whole matcher (see §3).

Seconds-per-item is derived from the reported duration so timestamps are exact:

```python
item_sec = duration / len(items)          # ≈ 0.1238, but measured per file
```

Items are normalised to unsigned 32-bit (`v & 0xFFFFFFFF`) so the bit math below
is consistent.

```python
# podcache/repetition.py
def fingerprint(path):
    out = run(["fpcalc", "-raw", "-length", "100000", path])
    items = [int(x) & 0xFFFFFFFF for x in FINGERPRINT.split(",")]
    item_sec = duration / len(items)
    return items, item_sec
```

> Chromaprint is the **only** audio-specific dependency. Swap it for any
> fixed-rate descriptor extractor and the rest of the algorithm is unchanged.

---

## 3. The core: Hamming-tolerant item matching

The naïve approach — treat the fingerprint as a string and look for exact common
substrings (or hash fixed windows and intersect) — **does not work** on real
files. Because re-encoding flips a few bits per item, two copies of the same
audio almost never produce an identical run of items, so exact matching finds
nothing. (This was a real bug: it only appeared to work against synthetic,
byte-identical fingerprints.)

The fix is to compare items by **Hamming distance** — the number of differing
bits — instead of equality. Two items *match* when:

```python
((a ^ b) & 0xFFFFFFFF).bit_count() <= MAX_BIT_ERR        # default 8 of 32 bits
```

Why a threshold of ~8/32 works as a separator:

| Pair | Expected differing bits |
|---|---|
| Same audio, re-encoded | ~0–6 (a handful) |
| Unrelated audio | ~16 (half of 32, by chance) with small variance |

A threshold of 8 sits cleanly in the gap: it accepts genuine matches and rejects
random ones (a random pair lands within 8 bits only ~0.35 % of the time).
`MAX_BIT_ERR` is the main accuracy dial — raise it if real matches are missed,
lower it if unrelated audio is matched.

---

## 4. Aligning two fingerprints

The repeated audio can sit at **any offset** — an ad might be 3 minutes into one
episode and 8 minutes into another. So before we can compare items position by
position, we must find the **alignment offset** `d` such that `A[i]` corresponds
to `B[i + d]`.

Checking all offsets is wasteful. Instead we **seed** candidate offsets cheaply,
then verify them precisely.

### 4.1 Seeding candidate offsets (vote histogram)

Bucket file B's items by a **masked key** (the high 16 bits, which are stable
enough to survive a few flipped bits often), then for each item of A, every
B-position sharing its key votes for the offset `j - i`. The offsets with the
most votes are the likely alignments. This is the Shazam-style constellation
idea reduced to a 1-D histogram.

```python
# podcache/repetition.py
def candidate_offsets(a, b, key_mask=0xFFFF0000, top=8):
    bpos = defaultdict(list)
    for j, v in enumerate(b):
        bpos[v & key_mask].append(j)
    votes = Counter()
    for i, v in enumerate(a):
        for j in bpos.get(v & key_mask, ()):
            votes[j - i] += 1
    return [off for off, _ in votes.most_common(top)]
```

Masking is only for **seeding** — it tolerates noise well enough to surface the
true offset among the top few, even if many individual items are too noisy to
key cleanly. We keep several candidates (`top = 8`) because one file can share
*multiple* segments with another (e.g. an intro at one offset and an ad at a
different offset), each producing its own vote peak.

### 4.2 Verifying an offset (coverage bitmap)

At a candidate offset, walk every aligned position and apply the real Hamming
test. The result is a bitmap over A marking which items actually match B at this
alignment.

```python
def coverage_at(a, b, offset, max_bit_err):
    cov = bytearray(len(a))
    for i, va in enumerate(a):
        j = i + offset
        if 0 <= j < len(b) and ((va ^ b[j]) & 0xFFFFFFFF).bit_count() <= max_bit_err:
            cov[i] = 1
    return cov
```

`match_cover(a, b)` runs the verification for each candidate offset and unions
the bitmaps, so all shared segments between the pair are captured in one pass.

---

## 5. Cross-file recurrence

A segment is interesting when it repeats across **multiple** files, not just one
pair. For each file `i`, count — per item position — how many *other* files
cover it, then keep positions covered in at least `min_shows - 1` others.

```python
# podcache/repetition.py  (simplified)
for i in range(n):
    a = fingerprints[i]
    match_count = [0] * len(a)
    for j in range(n):
        if j == i:
            continue
        cov = match_cover(a, fingerprints[j])
        for p in range(len(a)):
            match_count[p] += cov[p]

    recurring = [c >= (min_shows - 1) for c in match_count]   # default min_shows = 2
    ...
```

- `min_shows = 2` means "present in at least two files" — catches a sponsor read
  shared by even two episodes. Intros/outros (in all files) trivially clear any
  threshold.
- Comparing all pairs is `O(N²)` in the number of files; fine for the handful of
  files in a typical batch.

---

## 6. From recurring items to timestamped segments

`recurring` is a noisy bitmap: inside a real shared segment most items match but
a few fall just over the bit threshold (gaps), and outside it the odd item
matches by chance (specks). Turning this into clean ranges needs two guards:

1. **Gap tolerance** — bridge short gaps (default ≤ 0.5 s) so one noisy item
   doesn't split a segment.
2. **Density** — a run must be *mostly* covered (default ≥ 50 %). This is the
   key defence against false positives: a long span bridged from a few scattered
   specks has low density and is rejected, whereas a genuine segment is densely
   covered. Without it, generous gap-bridging invents segments out of noise.

A run also must clear a **minimum length** (default 5 s) to count.

```python
def _runs(cover, min_len, max_gap, min_density=0.5):
    runs = []
    p = 0
    while p < len(cover):
        if not cover[p]:
            p += 1; continue
        q = p; last = p; gap = 0; count = 0
        while q < len(cover):
            if cover[q]:
                last = q; gap = 0; count += 1
            else:
                gap += 1
                if gap > max_gap:
                    break
            q += 1
        length = last + 1 - p
        if length >= min_len and count / length >= min_density:
            runs.append((p, last + 1))
        p = q + 1
    return runs
```

Finally, **item indices become timestamps** by multiplying by the file's
measured seconds-per-item, and overlapping ranges are merged:

```python
start_seconds = run_start * item_sec
end_seconds   = run_end   * item_sec
```

The output is, per file, a list of `(start, end)` second-ranges for the segments
that recur across the set.

---

## 7. Matching a *known* pattern against a new file

Once a segment is found it can be stored as a **pattern** — just the slice of
fingerprint items for that segment (`items[round(start/item_sec) :
round(end/item_sec)]`) plus a label. Later, a single new file can be trimmed
without needing peers, by locating the stored pattern inside it. This is the same
alignment-and-verify machinery, asking "where does pattern A occur in file B,
and how well?"

```python
# podcache/repetition.py
def locate(a, b, max_bit_err, min_ratio):           # a = pattern, b = new file
    best_cov, best_off, best_n = None, 0, 0
    for off in candidate_offsets(a, b):
        cov = coverage_at(a, b, off, max_bit_err)
        n = sum(cov)
        if n > best_n:
            best_n, best_cov, best_off = n, cov, off
    if best_cov is None or best_n / len(a) < min_ratio:   # default min_ratio 0.55
        return None
    hits = [i + best_off for i, c in enumerate(best_cov) if c]
    return max(0, min(hits)), min(len(b), max(hits) + 1)  # (start, end) in B
```

`best_ratio()` (the fraction of a pattern that aligns to another fingerprint) is
also used to **de-duplicate** patterns: two stored patterns are "the same" when
their match ratio exceeds ~0.6, so re-detecting an intro across many episodes
increments one pattern's count rather than creating duplicates.

---

## 8. Parameters and tuning

| Parameter | Default | Meaning | Raise to… / Lower to… |
|---|---|---|---|
| `max_bit_err` | 8 / 32 | Bits two items may differ and still "match" | Raise → catch more (looser); lower → fewer false matches |
| `min_seconds` | 5 s | Minimum length of a reported segment | Raise → ignore short repeats; lower → catch brief stings |
| `min_shows` | 2 | Files a segment must appear in | Raise → only very common boilerplate |
| `max_gap` | 0.5 s | Gap bridged inside a segment | Raise → join through noisier passages |
| `min_density` | 0.5 | Fraction of a run that must be covered | Raise → stricter, fewer false positives |
| `key_mask` | top 16 bits | Bits used to seed candidate offsets | Fewer bits → more seed recall, more compute |
| `top_offsets` | 8 | Candidate alignments verified per pair | Raise → catch more co-occurring segments |

The two you actually touch are `max_bit_err` (sensitivity) and `min_seconds`
(what counts as a segment).

### Complexity

- Per file pair: `O(|A| + |B|)` to seed + `O(top · |A|)` to verify ≈ linear.
- Cross-file detection: `O(N² · L)` for `N` files of length `L` items. A 1-hour
  file is ~29 000 items, so even 10 files is tens of millions of cheap integer
  ops — sub-second to a few seconds, done off the critical path.

### Known failure modes

- **Dynamically inserted / personalised ads** that are *different audio* each
  time don't repeat, so they can't be found this way (they need a database of
  ad fingerprints, or transcript-based detection).
- **A segment present in only one file** has nothing to recur against (use a
  stored pattern from a prior run instead).
- **Re-encoding far harsher than typical** can push item differences past
  `max_bit_err` — raise the threshold.
- Boundaries are accurate to roughly the item rate (~0.12 s) plus a little slack
  where a segment fades into adjacent content.

---

## 9. Does this work for video?

**Yes — the matcher is fingerprint-agnostic.** Sections 3–8 never assume the
items are audio; they assume only a **fixed-rate sequence of fixed-width
integers where the same content yields near-identical integers and different
content yields unrelated ones**. Produce such a stream from video and the exact
same alignment, recurrence, run-extraction, and timestamp code applies. There
are three practical ways to get that stream.

### Option A — reuse the audio track (easiest, often best)

Most recurring video segments (channel intros, ad breaks, outros) carry
**consistent audio**. Demux the audio (`ffmpeg -i video -vn audio.wav`),
fingerprint it with Chromaprint exactly as above, and run the unchanged
algorithm. Timestamps map straight back onto the video because the audio and
video share a timeline. This needs **zero new matching code** — only an audio
extraction step.

### Option B — perceptual frame hashing (visual)

When you must match on the picture (e.g. silent bumpers, or audio that varies),
replace the fingerprinter with a **perceptual-hash stream**:

1. Sample frames at a fixed rate — e.g. 2–5 fps (not every frame; that's the
   "item rate", analogous to Chromaprint's 8/s).
2. Normalise each frame: grayscale, downscale (e.g. 32×32), optionally crop
   letterboxing, so encoding/resolution differences wash out.
3. Compute a **perceptual hash** per sampled frame — pHash or dHash → a 64-bit
   integer. Perceptual hashes are designed so visually-similar frames have small
   Hamming distance, which is precisely what the matcher consumes.

The result is a sequence of 64-bit items, one per sampled frame. Feed it to the
**same** `candidate_offsets` / `coverage_at` / `recurring_segments` / `locate`
functions, with two adjustments:

- Use 64-bit math (`& 0xFFFFFFFFFFFFFFFF`, `bit_count()` already handles it).
- Scale `max_bit_err` to the new width — pHash matches commonly use ~10 of 64
  bits; tune as in §8.
- `item_sec = 1 / sample_fps` (e.g. 0.25 s at 4 fps) for the timestamp mapping.

Everything else — offset voting to handle ads at different positions, density to
reject coincidental frame matches, gap tolerance, run→timestamp conversion — is
identical.

### Option C — both, combined

Run A and B independently and intersect (a segment is boilerplate if audio
*and* video repeat) for higher precision, or union them for higher recall.
Audio alone is usually enough and far cheaper.

### Video-specific considerations

- **Volume of data**: subsample frames; full-frame-rate hashing is unnecessary
  and slow. The whole design already assumes a modest fixed item rate.
- **Normalisation matters more** than for audio: crop bars, fix aspect ratio,
  grayscale before hashing, or re-encodes/overlays will inflate Hamming
  distance.
- **Hard cuts vs. fades**: perceptual hashing handles fades fine; the density
  guard tolerates a few transition frames.
- **Limits are the same in spirit**: personalised/region-varying video ads don't
  repeat and so can't be found by recurrence; they'd need a known-ad fingerprint
  library — which Option B also enables (store a clip's pHash stream and
  `locate()` it in new videos, exactly like §7).

In short: the detection engine is a generic "find the repeated sub-sequences in a
set of noisy fixed-rate fingerprint streams, and report their timestamps." Audio
via Chromaprint is one instantiation; perceptual-hash frame streams make it work
on video with no change to the matching logic.
