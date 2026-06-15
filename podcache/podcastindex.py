"""Search & discovery.

PodcastIndex.org is the primary backend — free, massive, and open. When no
PodcastIndex credentials are configured, PodCache falls back to the keyless
Apple/iTunes Search API so the tool still works on a fresh checkout. Both
return the same normalised shape: a list of shows, each carrying its RSS feed.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from .config import config

_PI_BASE = "https://api.podcastindex.org/api/1.0"
_ITUNES_BASE = "https://itunes.apple.com/search"
_UA = "PodCache/1.0"


def _podcastindex_headers() -> dict[str, str]:
    """PodcastIndex auth: sha1(key + secret + unix-time) in the Authorization header."""
    now = str(int(time.time()))
    digest = hashlib.sha1(
        (config.podcastindex_key + config.podcastindex_secret + now).encode()
    ).hexdigest()
    return {
        "User-Agent": _UA,
        "X-Auth-Key": config.podcastindex_key,
        "X-Auth-Date": now,
        "Authorization": digest,
    }


def _normalise_pi(feed: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": feed.get("id"),
        "title": feed.get("title", "").strip(),
        "author": feed.get("author", "").strip(),
        "description": (feed.get("description") or "").strip(),
        "image": feed.get("image") or feed.get("artwork") or "",
        "feed_url": feed.get("url", ""),
        "episode_count": feed.get("episodeCount"),
    }


def _normalise_itunes(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("collectionId"),
        "title": (item.get("collectionName") or "").strip(),
        "author": (item.get("artistName") or "").strip(),
        "description": "",
        "image": item.get("artworkUrl600") or item.get("artworkUrl100") or "",
        "feed_url": item.get("feedUrl", ""),
        "episode_count": item.get("trackCount"),
    }


def search_shows(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Return a clean list of shows matching `query`, each with its RSS feed URL."""
    query = query.strip()
    if not query:
        return []

    if config.has_podcastindex:
        url = f"{_PI_BASE}/search/byterm"
        params = {"q": query, "max": limit}
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, params=params, headers=_podcastindex_headers())
            resp.raise_for_status()
            feeds = resp.json().get("feeds", [])
        shows = [_normalise_pi(f) for f in feeds]
    else:
        params = {"term": query, "media": "podcast", "limit": limit}
        with httpx.Client(timeout=20) as client:
            resp = client.get(_ITUNES_BASE, params=params, headers={"User-Agent": _UA})
            resp.raise_for_status()
            results = resp.json().get("results", [])
        shows = [_normalise_itunes(r) for r in results]

    # Only return shows we can actually fetch episodes for.
    return [s for s in shows if s["feed_url"]]
