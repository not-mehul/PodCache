"""Tier 1 ad removal — cross-episode repetition detection.

Intros, outros, and baked-in host-read ads are the audio that repeats verbatim
across a show's episodes. Given several episodes, we fingerprint each with
Chromaprint (`fpcalc`) and find the segments whose fingerprints recur across
episodes — those are the boilerplate to cut. No transcription, no model; just an
audio fingerprint (cheap) and set intersection.

`fpcalc` (Chromaprint) and `ffmpeg` are optional: with either missing, this
whole tier is skipped and downloads are unaffected.
"""

from __future__ import annotations

import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

# Detection tuning.
_WINDOW = 10        # fingerprint items per shingle (~1.2 s of audio)
_MIN_SHOWS = 2      # a segment must recur in at least this many episodes
_MIN_SECONDS = 2.5  # ignore recurring blips shorter than this
_DEFAULT_ITEM_SEC = 0.1238  # Chromaprint's ~seconds-per-item, if duration is unknown


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def fingerprint(path: Path) -> tuple[list[int], float] | None:
    """Return (raw fingerprint items, seconds-per-item) for a file, or None.

    Uses `fpcalc -raw` over the whole file. The per-item time is derived from the
    duration fpcalc reports, so positions can be converted back to timestamps.
    """
    if not fpcalc_available():
        return None
    try:
        out = subprocess.run(
            ["fpcalc", "-raw", "-length", "100000", str(path)],
            capture_output=True, text=True, check=True, timeout=600,
        )
    except Exception:
        return None

    items: list[int] | None = None
    duration: float | None = None
    for line in out.stdout.splitlines():
        if line.startswith("FINGERPRINT="):
            raw = line[len("FINGERPRINT="):].strip()
            try:
                items = [int(x) for x in raw.split(",") if x != ""]
            except ValueError:
                items = None
        elif line.startswith("DURATION="):
            try:
                duration = float(line[len("DURATION="):].strip())
            except ValueError:
                pass
    if not items:
        return None
    item_sec = (duration / len(items)) if duration else _DEFAULT_ITEM_SEC
    return (items, item_sec)


def _merge(segments: list[tuple[float, float]], gap: float = 1.0) -> list[tuple[float, float]]:
    if not segments:
        return []
    segments = sorted(segments)
    merged = [segments[0]]
    for s, e in segments[1:]:
        ls, le = merged[-1]
        if s <= le + gap:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def recurring_segments(
    fingerprints: list[list[int]],
    item_secs: list[float],
    *,
    min_shows: int = _MIN_SHOWS,
    window: int = _WINDOW,
    min_seconds: float = _MIN_SECONDS,
    mask: int = 0xFFFFFFFF,
) -> list[list[tuple[float, float]]]:
    """For each episode, return the (start, end) ranges that recur across others.

    A "shingle" is `window` consecutive fingerprint items. A shingle present in
    at least `min_shows` distinct episodes marks recurring audio. Runs of
    recurring positions become cut ranges (filtered to `min_seconds`). `mask`
    can drop noisy low bits; the default keeps full precision (fewest false
    positives), which is right for baked-in segments that are byte-identical.
    """
    n = len(fingerprints)
    if n < min_shows:
        return [[] for _ in range(n)]

    # shingle hash -> set of episode indices that contain it
    shingle_shows: dict[int, set[int]] = defaultdict(set)
    per_file_hashes: list[list[int]] = []
    for fi, fp in enumerate(fingerprints):
        masked = [v & mask for v in fp]
        hashes = [hash(tuple(masked[p : p + window])) for p in range(len(masked) - window + 1)]
        per_file_hashes.append(hashes)
        for h in set(hashes):
            shingle_shows[h].add(fi)

    results: list[list[tuple[float, float]]] = []
    for fi, hashes in enumerate(per_file_hashes):
        length = len(fingerprints[fi])
        covered = bytearray(length)
        for p, h in enumerate(hashes):
            if len(shingle_shows[h]) >= min_shows:
                for j in range(p, min(p + window, length)):
                    covered[j] = 1
        isec = item_secs[fi] or _DEFAULT_ITEM_SEC
        segs: list[tuple[float, float]] = []
        p = 0
        while p < length:
            if covered[p]:
                q = p
                while q < length and covered[q]:
                    q += 1
                start, end = p * isec, q * isec
                if end - start >= min_seconds:
                    segs.append((start, end))
                p = q
            else:
                p += 1
        results.append(_merge(segs))
    return results
