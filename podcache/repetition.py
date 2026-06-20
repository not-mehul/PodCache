"""Tier 1 ad removal — cross-episode repetition detection.

Intros, outros, and baked-in host-read ads are the audio that repeats across a
show's episodes. We fingerprint each episode with Chromaprint (`fpcalc`) and find
the segments whose fingerprints recur across episodes.

Chromaprint fingerprints of the *same* audio in two different files are similar
but not bit-identical (re-encoding shifts a few bits per 32-bit item), so
matching is done by **Hamming distance**, not equality: an item matches when it
differs by at most a few bits, and a segment is a run of matching items
(tolerating small gaps). Candidate alignments are seeded cheaply with a masked
key, then verified bit-for-bit.

`fpcalc` (Chromaprint) and `ffmpeg` are optional: with either missing, this whole
tier is skipped and downloads are unaffected.
"""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .config import config

_DEFAULT_ITEM_SEC = 0.1238  # Chromaprint's ~seconds-per-item, if duration is unknown
_KEY_MASK = 0xFFFF0000      # high 16 bits used to seed candidate alignments
_TOP_OFFSETS = 8            # candidate alignments to verify per pair
_MASK32 = 0xFFFFFFFF


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def fingerprint(path: Path) -> tuple[list[int], float] | None:
    """Return (raw fingerprint items, seconds-per-item) for a file, or None.

    Items are normalised to unsigned 32-bit so bit math is consistent.
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
                items = [int(x) & _MASK32 for x in raw.split(",") if x != ""]
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


# ── low-level fingerprint matching (Hamming-tolerant) ────────────────────────
def candidate_offsets(a: list[int], b: list[int], key_mask: int, top: int) -> list[int]:
    """Propose alignment offsets (b_index - a_index) by voting on shared
    masked-key items. Cheap seed for the exact verification that follows."""
    bpos: dict[int, list[int]] = defaultdict(list)
    for j, v in enumerate(b):
        bpos[v & key_mask].append(j)
    votes: Counter[int] = Counter()
    for i, v in enumerate(a):
        for j in bpos.get(v & key_mask, ()):
            votes[j - i] += 1
    return [off for off, _ in votes.most_common(top)]


def coverage_at(a: list[int], b: list[int], offset: int, max_bit_err: int) -> bytearray:
    """Mark each position of `a` whose item matches `b` at `offset` within
    `max_bit_err` differing bits."""
    cov = bytearray(len(a))
    lb = len(b)
    for i, va in enumerate(a):
        j = i + offset
        if 0 <= j < lb and ((va ^ b[j]) & _MASK32).bit_count() <= max_bit_err:
            cov[i] = 1
    return cov


def match_cover(
    a: list[int], b: list[int], *, max_bit_err: int, key_mask: int = _KEY_MASK, top: int = _TOP_OFFSETS
) -> bytearray:
    """Positions of `a` that match `b` at any of the strong candidate offsets."""
    cover = bytearray(len(a))
    for off in candidate_offsets(a, b, key_mask, top):
        c = coverage_at(a, b, off, max_bit_err)
        for i in range(len(a)):
            if c[i]:
                cover[i] = 1
    return cover


def best_ratio(a: list[int], b: list[int], *, max_bit_err: int) -> float:
    """Best fraction of `a` that aligns to `b` (used for pattern de-duplication)."""
    if not a:
        return 0.0
    best = 0
    for off in candidate_offsets(a, b, _KEY_MASK, _TOP_OFFSETS):
        best = max(best, sum(coverage_at(a, b, off, max_bit_err)))
    return best / len(a)


def locate(a: list[int], b: list[int], *, max_bit_err: int, min_ratio: float) -> tuple[int, int] | None:
    """Find where `a` occurs inside `b`. Returns (start, end) indices in `b`."""
    best_cov: bytearray | None = None
    best_off = 0
    best_n = 0
    for off in candidate_offsets(a, b, _KEY_MASK, _TOP_OFFSETS):
        cov = coverage_at(a, b, off, max_bit_err)
        n = sum(cov)
        if n > best_n:
            best_n, best_cov, best_off = n, cov, off
    if best_cov is None or best_n / len(a) < min_ratio:
        return None
    hits = [i + best_off for i, c in enumerate(best_cov) if c]
    start = max(0, min(hits))
    end = min(len(b), max(hits) + 1)
    return (start, end) if end > start else None


def _runs(
    cover: bytearray, min_len: int, max_gap: int, min_density: float = 0.5
) -> list[tuple[int, int]]:
    """Runs of covered positions, allowing small gaps but requiring the run to be
    *mostly* covered (`min_density`). The density check rejects long stretches
    bridged from a few scattered (spurious) matches."""
    runs: list[tuple[int, int]] = []
    n = len(cover)
    p = 0
    while p < n:
        if not cover[p]:
            p += 1
            continue
        q = p
        last = p
        gap = 0
        count = 0
        while q < n:
            if cover[q]:
                last = q
                gap = 0
                count += 1
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
    min_shows: int | None = None,
    max_bit_err: int | None = None,
    min_seconds: float | None = None,
    max_gap_seconds: float = 0.5,
    min_density: float = 0.5,
) -> list[list[tuple[float, float]]]:
    """For each episode, the (start, end) ranges that recur across the others.

    A position is "recurring" when it matches the same audio in at least
    `min_shows - 1` other episodes; runs of recurring positions become segments.
    """
    min_shows = max(2, min_shows if min_shows is not None else config.fp_min_shows)
    max_bit_err = max_bit_err if max_bit_err is not None else config.fp_max_bit_err
    min_seconds = min_seconds if min_seconds is not None else config.fp_min_seconds

    n = len(fingerprints)
    if n < min_shows:
        return [[] for _ in range(n)]

    need = min_shows - 1
    results: list[list[tuple[float, float]]] = []
    for i in range(n):
        a = fingerprints[i]
        la = len(a)
        match_count = bytearray(la)  # capped at 255, plenty
        for j in range(n):
            if j == i:
                continue
            cov = match_cover(a, fingerprints[j], max_bit_err=max_bit_err)
            for p in range(la):
                if cov[p] and match_count[p] < 255:
                    match_count[p] += 1
        cover = bytearray(1 if match_count[p] >= need else 0 for p in range(la))
        isec = item_secs[i] or _DEFAULT_ITEM_SEC
        min_len = max(1, int(round(min_seconds / isec)))
        max_gap = max(0, int(round(max_gap_seconds / isec)))
        segs = [(s * isec, e * isec) for s, e in _runs(cover, min_len, max_gap, min_density)]
        results.append(_merge(segs))
    return results
