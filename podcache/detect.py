"""Ad detection — the magic, running entirely on-device.

The timestamped transcript is read by a small local instruct model (llama.cpp /
GGUF). Because a small model has a modest context window, the transcript is fed
in overlapping windows; the model flags the lines that are sponsor reads in each
window, and we stitch the flagged lines back into timestamped ad segments for
the splicer. With no local model installed, an offline heuristic detector
(phrase matching) stands in so the pipeline still completes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Callable

from .config import config
from .transcribe import Segment

# Windowing for the local model: how many transcript lines per pass, and how
# much each window overlaps the previous so an ad straddling a boundary is still
# seen whole by at least one window.
_WINDOW = 40
_OVERLAP = 8


@dataclass
class AdSegment:
    start: float
    end: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Phrases that reliably open or sit inside a sponsor read. Used by the offline
# detector and to prime the model's instructions.
_AD_PHRASES = (
    "this episode is brought to you by",
    "this episode is sponsored by",
    "brought to you by",
    "sponsored by",
    "today's sponsor",
    "our sponsor",
    "use promo code",
    "use code",
    "promo code",
    "discount code",
    "percent off",
    "% off",
    "free trial",
    "sign up at",
    "go to ",
    "dot com slash",
    ".com/",
    "terms and conditions apply",
    "support for this podcast comes from",
    "support for this show comes from",
    "a word from our sponsor",
    "we'll be right back",
)

_SYSTEM = (
    "You are an audio editor that finds advertisements and sponsor reads in "
    "podcast transcripts. You are precise: flag only genuine paid promotions "
    "(host-read sponsor spots, dynamically inserted ads, promo-code reads, "
    "'brought to you by' segments). Never flag the host's own content, listener "
    "mail, or discussion that merely mentions a product."
)

_INSTRUCTIONS = (
    "Each line of the transcript below is numbered. List the line numbers that "
    "are part of an advertisement or sponsor read — include the lead-in and the "
    "call to action (promo codes, URLs). Respond with a JSON object of the form "
    '{"ad_lines": [<numbers>]}. If there are no ads, respond {"ad_lines": []}.\n\n'
    "Transcript:\n"
)


def _merge(ads: list[AdSegment], gap: float = 1.0) -> list[AdSegment]:
    """Sort and coalesce ad segments that touch, overlap, or sit within `gap`."""
    if not ads:
        return []
    ads = sorted(ads, key=lambda a: a.start)
    merged = [ads[0]]
    for ad in ads[1:]:
        last = merged[-1]
        if ad.start <= last.end + gap:
            last.end = max(last.end, ad.end)
            if ad.reason not in last.reason:
                last.reason = f"{last.reason}; {ad.reason}"
        else:
            merged.append(ad)
    return merged


def _indices_to_segments(
    segments: list[Segment], indices: set[int], reason: str
) -> list[AdSegment]:
    """Turn a set of ad line indices into merged, timestamped ad segments."""
    ads = [
        AdSegment(start=segments[i].start, end=segments[i].end, reason=reason)
        for i in sorted(indices)
        if 0 <= i < len(segments)
    ]
    # Bridge small holes (a missed line in the middle of an otherwise-flagged ad).
    return _merge(ads, gap=8.0)


def _detect_local(
    segments: list[Segment],
    on_progress: Callable[[float], None] | None = None,
) -> list[AdSegment]:
    """Slide a small local model over the transcript, window by window."""
    from . import llm  # local import keeps llama.cpp off the import path

    ad_indices: set[int] = set()
    step = max(1, _WINDOW - _OVERLAP)
    starts = list(range(0, len(segments), step))

    for n, offset in enumerate(starts):
        window = segments[offset : offset + _WINDOW]
        # Number lines locally (0-based) — small models renumber unreliably, so
        # we map the returned indices back to global positions via `offset`.
        listing = "\n".join(f"{i}: {seg.text}" for i, seg in enumerate(window))
        raw = llm.chat_json(_SYSTEM, _INSTRUCTIONS + listing, max_tokens=256)
        try:
            data = json.loads(raw)
            lines = data.get("ad_lines", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, TypeError):
            lines = []
        for idx in lines:
            try:
                local = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= local < len(window):
                ad_indices.add(offset + local)
        if on_progress:
            on_progress((n + 1) / len(starts))

    if on_progress:
        on_progress(1.0)
    return _indices_to_segments(segments, ad_indices, "advertisement")


def _detect_heuristic(segments: list[Segment]) -> list[AdSegment]:
    """Offline fallback: flag segments containing known ad phrasing, then grow
    each flagged segment to temporally-adjacent neighbours and merge nearby hits."""
    flagged: list[AdSegment] = []
    last = len(segments) - 1
    for i, seg in enumerate(segments):
        low = seg.text.lower()
        if not any(phrase in low for phrase in _AD_PHRASES):
            continue
        # Absorb the immediate lead-in / call-to-action, but only when the
        # neighbour is temporally adjacent — never bridge a large time gap
        # (a sponsor mention 15 minutes later is a separate ad, not one block).
        lo, hi = i, i
        if i > 0 and seg.start - segments[i - 1].end < 5.0:
            lo = i - 1
        if i < last and segments[i + 1].start - seg.end < 5.0:
            hi = i + 1
        flagged.append(
            AdSegment(
                start=segments[lo].start,
                end=segments[hi].end,
                reason="matched sponsor phrasing",
            )
        )
    # Merge hits that sit within ~20s of each other into single ad blocks.
    return _merge(flagged, gap=20.0)


def detect_ads(
    segments: list[Segment],
    on_progress: Callable[[float], None] | None = None,
) -> list[AdSegment]:
    """Return ad segments, using the local model when available."""
    if not segments:
        return []
    if config.has_local_llm:
        try:
            return _detect_local(segments, on_progress)
        except Exception:
            # Never fail the whole job over a detection hiccup — fall back.
            return _detect_heuristic(segments)
    return _detect_heuristic(segments)
