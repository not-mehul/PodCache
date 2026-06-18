"""Persistent show profiles — learned, reusable ad/intro/outro patterns.

A *pattern* is a stretch of audio that recurs across a show's episodes, stored
by its Chromaprint fingerprint plus a label (ad / intro / outro). Patterns are
saved per show under the profiles directory, so once PodCache has learned a
show's boilerplate it can strip it from *future* downloads and from episodes
that were downloaded earlier — without re-deriving anything.

Matching a stored pattern inside a new episode is an offset-aligned fingerprint
search: cheap, and independent of where the segment happens to sit.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import config

_WINDOW = 10            # fingerprint items per shingle
_FIND_MIN_RATIO = 0.55  # fraction of a pattern's shingles that must align to match
_SAME_PATTERN = 0.6     # overlap coefficient above which two patterns are "the same"

LABELS = ("ad", "intro", "outro")
STATUSES = ("pending", "confirmed", "rejected")


@dataclass
class Pattern:
    id: str
    items: list[int]
    item_sec: float
    seconds: float
    label: str = "ad"          # ad | intro | outro
    status: str = "pending"    # pending | confirmed | rejected
    episodes_seen: int = 1
    clip: str = ""             # preview clip path, relative to the profiles dir
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Lightweight view for the UI (omits the bulky fingerprint items)."""
        d = self.to_dict()
        d.pop("items", None)
        d["n_items"] = len(self.items)
        return d


@dataclass
class ShowProfile:
    show_key: str
    patterns: list[Pattern] = field(default_factory=list)

    def by_id(self, pattern_id: str) -> Pattern | None:
        return next((p for p in self.patterns if p.id == pattern_id), None)


# ── persistence ──────────────────────────────────────────────────────────────
def _path(show_key: str) -> Path:
    config.profiles_dir.mkdir(parents=True, exist_ok=True)
    return config.profiles_dir / f"{show_key}.json"


def load(show_key: str) -> ShowProfile:
    path = _path(show_key)
    if not path.exists():
        return ShowProfile(show_key=show_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        patterns = [Pattern(**p) for p in data.get("patterns", [])]
        return ShowProfile(show_key=show_key, patterns=patterns)
    except Exception:
        return ShowProfile(show_key=show_key)


def save(profile: ShowProfile) -> None:
    path = _path(profile.show_key)
    tmp = path.with_suffix(".json.tmp")
    payload = {"show_key": profile.show_key, "patterns": [p.to_dict() for p in profile.patterns]}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def list_profiles() -> list[str]:
    if not config.profiles_dir.exists():
        return []
    return sorted(p.stem for p in config.profiles_dir.glob("*.json"))


# ── fingerprint matching ─────────────────────────────────────────────────────
def _shingles(items: list[int], window: int = _WINDOW) -> list[int]:
    return [hash(tuple(items[p : p + window])) for p in range(len(items) - window + 1)]


def patterns_similar(a: list[int], b: list[int]) -> bool:
    """True when two fingerprints describe (mostly) the same audio."""
    if len(a) < _WINDOW or len(b) < _WINDOW:
        return a == b
    sa, sb = set(_shingles(a)), set(_shingles(b))
    if not sa or not sb:
        return False
    overlap = len(sa & sb) / min(len(sa), len(sb))
    return overlap >= _SAME_PATTERN


def find_in(pattern_items: list[int], target_items: list[int]) -> tuple[int, int] | None:
    """Locate `pattern_items` inside `target_items`. Returns (start, end) item
    indices in the target, or None. Robust to the pattern sitting at any offset."""
    lp, lt = len(pattern_items), len(target_items)
    if lp < _WINDOW or lt < _WINDOW:
        return None
    target_pos: dict[int, list[int]] = defaultdict(list)
    for tp, h in enumerate(_shingles(target_items)):
        target_pos[h].append(tp)
    pat_sh = _shingles(pattern_items)
    votes: Counter[int] = Counter()
    for pp, h in enumerate(pat_sh):
        for tp in target_pos.get(h, ()):
            votes[tp - pp] += 1
    if not votes:
        return None
    best_off, score = votes.most_common(1)[0]
    if score / len(pat_sh) < _FIND_MIN_RATIO:
        return None
    start = max(0, best_off)
    end = min(lt, best_off + lp)
    return (start, end) if end > start else None


def apply_profile(
    profile: ShowProfile,
    target_items: list[int],
    item_sec: float,
    statuses: tuple[str, ...] = ("confirmed",),
) -> list[tuple[float, float]]:
    """Return (start, end) second-ranges in the target matched by stored patterns."""
    segs: list[tuple[float, float]] = []
    for pat in profile.patterns:
        if pat.status not in statuses:
            continue
        loc = find_in(pat.items, target_items)
        if loc:
            s, e = loc
            segs.append((s * item_sec, e * item_sec))
    return _merge(segs)


def add_or_update(
    profile: ShowProfile,
    items: list[int],
    item_sec: float,
    seconds: float,
    *,
    label: str = "ad",
    status: str = "pending",
) -> tuple[Pattern, bool]:
    """Record a freshly-detected segment. Returns (pattern, is_new)."""
    for pat in profile.patterns:
        if patterns_similar(pat.items, items):
            pat.episodes_seen += 1
            pat.updated_at = time.time()
            return pat, False
    pat = Pattern(
        id=uuid.uuid4().hex[:12],
        items=items,
        item_sec=item_sec,
        seconds=seconds,
        label=label,
        status=status,
    )
    profile.patterns.append(pat)
    return pat, True


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
