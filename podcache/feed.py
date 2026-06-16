"""RSS parsing.

feedparser does the heavy lifting of navigating messy podcast XML. We extract
the playable enclosure (the .mp3/.m4a media URL) and the per-episode metadata
PodCache shows in the episode list.
"""

from __future__ import annotations

from typing import Any

import feedparser

from .config import config


def _enclosure_url(entry: Any) -> str:
    """Pull the audio media URL from an entry's enclosures / links."""
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href:
            return href
    for link in getattr(entry, "links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return ""


def _duration_seconds(entry: Any) -> int | None:
    """Parse the iTunes duration tag, which may be seconds or HH:MM:SS."""
    raw = entry.get("itunes_duration") if hasattr(entry, "get") else None
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        if ":" in raw:
            parts = [int(p) for p in raw.split(":")]
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + part
            return seconds
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def parse_feed(feed_url: str, limit: int | None = None) -> dict[str, Any]:
    """Return the show header plus a clean list of episodes.

    By default all episodes (up to the configured feed cap) are returned, so the
    show page can paginate through the full back-catalogue rather than only the
    most recent few.
    """
    if limit is None:
        limit = config.feed_limit
    parsed = feedparser.parse(feed_url)

    # Check for HTTP status errors (if feed was fetched over HTTP/HTTPS)
    status = parsed.get("status")
    if status is not None and status >= 400:
        raise ValueError(f"HTTP error {status} when fetching feed")

    # Check for connection/network/parsing failures that resulted in no feed data
    if parsed.get("bozo") and not parsed.get("feed") and not parsed.get("entries"):
        exc = parsed.get("bozo_exception")
        if isinstance(exc, Exception):
            raise exc
        raise ValueError("Failed to parse podcast feed")

    # Check for empty/invalid feed
    if not parsed.feed.get("title") and not parsed.entries:
        raise ValueError("Invalid or empty podcast feed")

    show_image = ""
    if parsed.feed.get("image"):
        show_image = parsed.feed.image.get("href", "")
    show_image = show_image or parsed.feed.get("itunes_image", {}).get("href", "")

    show = {
        "title": parsed.feed.get("title", "").strip(),
        "author": parsed.feed.get("author", "").strip()
        or parsed.feed.get("itunes_author", "").strip(),
        "image": show_image,
        "feed_url": feed_url,
    }

    episodes: list[dict[str, Any]] = []
    for entry in parsed.entries[:limit]:
        media_url = _enclosure_url(entry)
        if not media_url:
            continue
        ep_image = entry.get("itunes_image", {}).get("href", "") or show_image
        episodes.append(
            {
                "guid": entry.get("id") or media_url,
                "title": entry.get("title", "").strip(),
                "published": entry.get("published", ""),
                "summary": (entry.get("summary") or "").strip(),
                "duration": _duration_seconds(entry),
                "media_url": media_url,
                "image": ep_image,
            }
        )

    return {"show": show, "episodes": episodes}
