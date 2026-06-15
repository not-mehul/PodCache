"""Ad detection — the magic.

The timestamped transcript is handed to a language model, which returns the
segment ranges that are sponsor reads. We map those ranges back to exact audio
timestamps for the splicer.

Primary backend: Claude (claude-opus-4-8 by default), whose 1M-token context
window reads an entire multi-hour transcript in one pass. When no Anthropic key
is configured, an offline heuristic detector (phrase matching) stands in so the
pipeline still completes locally.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

from .config import config
from .transcribe import Segment


@dataclass
class AdSegment:
    start: float
    end: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Phrases that reliably open or sit inside a sponsor read. Used by the offline
# detector and to nudge the model's instructions.
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
    "You are an audio editor that removes advertisements and sponsor reads from "
    "podcast transcripts. You are precise: you only flag genuine paid promotions "
    "(host-read sponsor spots, dynamically inserted ads, promo-code reads, "
    "'brought to you by' segments), never the host's own content, listener mail, "
    "or topical discussion that merely mentions a product."
)

_INSTRUCTIONS = (
    "Below is a podcast transcript. Each line is numbered and prefixed with its "
    "start and end time in seconds:\n\n"
    "    [index] start-end: text\n\n"
    "Identify every advertisement / sponsor read. Return contiguous ranges of "
    "line indices that should be cut. Be inclusive of the full ad — include the "
    "lead-in ('we'll be right back', 'this episode is brought to you by…') and "
    "the call to action (promo codes, URLs), but do not cut surrounding editorial "
    "content. If there are no ads, return an empty list.\n\n"
    "Transcript:\n"
)

# JSON schema constraining Claude's response to clean, parseable ad ranges.
_SCHEMA = {
    "type": "object",
    "properties": {
        "ads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_index": {"type": "integer"},
                    "end_index": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["start_index", "end_index", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ads"],
    "additionalProperties": False,
}


def _render_transcript(segments: list[Segment]) -> str:
    return "\n".join(
        f"[{i}] {seg.start:.1f}-{seg.end:.1f}: {seg.text}"
        for i, seg in enumerate(segments)
    )


def _ranges_to_segments(
    segments: list[Segment], ranges: list[dict[str, Any]]
) -> list[AdSegment]:
    ads: list[AdSegment] = []
    n = len(segments)
    for r in ranges:
        lo = max(0, min(int(r["start_index"]), n - 1))
        hi = max(lo, min(int(r["end_index"]), n - 1))
        ads.append(
            AdSegment(
                start=segments[lo].start,
                end=segments[hi].end,
                reason=r.get("reason", "advertisement"),
            )
        )
    return _merge(ads)


def _merge(ads: list[AdSegment], gap: float = 1.0) -> list[AdSegment]:
    """Sort and coalesce ad segments that touch or overlap."""
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


def _detect_claude(segments: list[Segment]) -> list[AdSegment]:
    import anthropic

    client = anthropic.Anthropic(api_key=config.anthropic_key)
    prompt = _INSTRUCTIONS + _render_transcript(segments)

    # Stream for safety: multi-hour transcripts make for long requests.
    with client.messages.stream(
        model=config.detect_model,
        max_tokens=16000,
        system=_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()

    text = next((b.text for b in message.content if b.type == "text"), "{}")
    data = json.loads(text)
    return _ranges_to_segments(segments, data.get("ads", []))


def _detect_heuristic(segments: list[Segment]) -> list[AdSegment]:
    """Offline fallback: flag segments containing known ad phrasing, then grow
    each flagged segment outward to neighbours and merge nearby hits."""
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


def detect_ads(segments: list[Segment]) -> list[AdSegment]:
    """Return ad segments for `segments`, using Claude when available."""
    if not segments:
        return []
    if config.has_claude:
        try:
            return _detect_claude(segments)
        except Exception:
            # Never fail the whole job over a detection hiccup — fall back.
            return _detect_heuristic(segments)
    return _detect_heuristic(segments)
