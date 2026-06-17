"""Tier 0 ad removal — chapters (no transcription, no model).

Podcasts increasingly ship chapter markers, either embedded in the file's ID3
tags (CHAP frames) or referenced from the RSS feed as a Podcasting 2.0 JSON
chapters document. Sponsor reads are very often their own chapter, titled
something like "Sponsor: ACME" or "Ad". We read those chapters and treat any
whose title looks like an advertisement as a cut range.

This is essentially free: a metadata read plus a regex — the heavy lifting
(transcription, an LLM) is avoided entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

# Titles that mark a sponsor / advertisement chapter. Word-boundaries keep the
# short tokens ("ad", "ads") from matching words like "radio" or "add".
_AD_TITLE = re.compile(
    r"\b(ad|ads|advert|adverts|advertisement|advertisements|sponsor|sponsors"
    r"|sponsored|sponsorship|promo|promos|promotion|promotional)\b"
    r"|brought to you by|presented by|promo code|sponsored by|paid promotion"
    r"|a word from (our|your)",
    re.IGNORECASE,
)


@dataclass
class Chapter:
    start: float           # seconds
    end: float | None      # seconds, or None when unknown
    title: str


def is_ad_title(title: str) -> bool:
    return bool(_AD_TITLE.search(title or ""))


def from_id3(path: Path) -> list[Chapter]:
    """Read embedded ID3 CHAP chapters from an MP3 (free, offline)."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
    except Exception:
        return []
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return []
    except Exception:
        return []

    out: list[Chapter] = []
    for ch in tags.getall("CHAP"):
        start = getattr(ch, "start_time", None)
        end = getattr(ch, "end_time", None)
        if start is None:
            continue
        title = ""
        sub = getattr(ch, "sub_frames", None)
        if sub is not None:
            for key in ("TIT2", "TIT3"):
                try:
                    frames = sub.getall(key)
                except Exception:
                    frames = []
                if frames and getattr(frames[0], "text", None):
                    title = str(frames[0].text[0])
                    break
        # CHAP times are milliseconds; a sentinel 0xFFFFFFFF end means "unset".
        end_s = end / 1000.0 if end and end < 0xFFFFFFFF else None
        out.append(Chapter(start=start / 1000.0, end=end_s, title=title))
    out.sort(key=lambda c: c.start)
    return out


def from_pc20(url: str) -> list[Chapter]:
    """Fetch a Podcasting 2.0 JSON chapters document and parse it."""
    resp = httpx.get(url, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    out: list[Chapter] = []
    for c in data.get("chapters", []) or []:
        st = c.get("startTime")
        if st is None:
            continue
        et = c.get("endTime")
        out.append(
            Chapter(
                start=float(st),
                end=float(et) if et is not None else None,
                title=(c.get("title") or ""),
            )
        )
    out.sort(key=lambda c: c.start)
    return out


def extract(path: Path, chapters_url: str = "") -> list[Chapter]:
    """Return chapters for a downloaded file, preferring the embedded ones."""
    chapters = from_id3(path)
    if not chapters and chapters_url:
        try:
            chapters = from_pc20(chapters_url)
        except Exception:
            chapters = []
    return chapters


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


def ad_segments(
    chapters: list[Chapter], duration: float | None = None
) -> list[tuple[float, float]]:
    """Turn ad-titled chapters into merged (start, end) cut ranges.

    A chapter's end is its own `end` when known, else the next chapter's start,
    else the media duration.
    """
    if not chapters:
        return []
    n = len(chapters)
    segs: list[tuple[float, float]] = []
    for i, ch in enumerate(chapters):
        if not is_ad_title(ch.title):
            continue
        start = max(0.0, ch.start)
        end = ch.end
        if end is None or end <= start:
            end = chapters[i + 1].start if i + 1 < n else duration
        if end is None or end <= start:
            continue
        segs.append((start, end))
    return _merge(segs)
