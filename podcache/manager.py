"""Concurrent download queue.

A single global manager runs episode downloads through a thread pool whose size
is the configured concurrency limit, and broadcasts per-item progress to any
connected browsers over SSE. Files land in a dedicated downloads area, one
sub-folder per show.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import download
from .config import config


def _safe(name: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or fallback


@dataclass
class DownloadItem:
    id: str
    show_title: str
    episode_title: str
    media_url: str
    image: str = ""
    status: str = "queued"  # queued | downloading | completed | failed | skipped
    progress: float = 0.0
    error: str = ""
    rel_path: str = ""  # path relative to the downloads dir, once known

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "show": self.show_title,
            "title": self.episode_title,
            "image": self.image,
            "status": self.status,
            "progress": round(self.progress, 3),
            "error": self.error,
            "rel_path": self.rel_path,
        }


class DownloadManager:
    def __init__(self) -> None:
        self._items: dict[str, DownloadItem] = {}
        self._order: list[str] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._executor = ThreadPoolExecutor(
            max_workers=config.concurrency, thread_name_prefix="podcache-dl"
        )
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── wiring ────────────────────────────────────────────────────────────────
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the server's event loop so worker threads can reach it."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, event: dict[str, Any]) -> None:
        """Push an event to every subscriber (safe to call from any thread)."""
        if self._loop is None:
            return
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, event)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._items[i].as_dict() for i in self._order]

    def count(self) -> int:
        return len(self._order)

    # ── queue control ─────────────────────────────────────────────────────────
    def enqueue(
        self, show: dict[str, Any], episodes: list[dict[str, Any]]
    ) -> list[str]:
        show_title = (show.get("title") or "Unknown Show").strip()
        image = show.get("image") or ""
        ids: list[str] = []
        for ep in episodes:
            url = ep.get("media_url")
            if not url:
                continue
            item = DownloadItem(
                id=uuid.uuid4().hex[:12],
                show_title=show_title,
                episode_title=(ep.get("title") or "Episode").strip(),
                media_url=url,
                image=ep.get("image") or image,
            )
            with self._lock:
                self._items[item.id] = item
                self._order.append(item.id)
            self._broadcast({"type": "item", **item.as_dict()})
            self._executor.submit(self._run, item)
            ids.append(item.id)
        return ids

    def clear_finished(self) -> None:
        """Drop completed/failed/skipped items from the list (files stay on disk)."""
        with self._lock:
            keep = {"queued", "downloading"}
            self._order = [i for i in self._order if self._items[i].status in keep]
            self._items = {i: self._items[i] for i in self._order}
        self._broadcast({"type": "snapshot", "items": self.snapshot()})

    def get(self, item_id: str) -> DownloadItem | None:
        return self._items.get(item_id)

    def resolve_path(self, item: DownloadItem) -> Path | None:
        if not item.rel_path:
            return None
        return config.download_dir / item.rel_path

    # ── worker ────────────────────────────────────────────────────────────────
    def _update(self, item: DownloadItem, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(item, k, v)
        self._broadcast({"type": "item", **item.as_dict()})

    def _run(self, item: DownloadItem) -> None:
        dest_dir = config.download_dir / _safe(item.show_title, "show")
        stem = _safe(item.episode_title, "episode")

        # Skip if we already have this episode on disk.
        existing = (
            next(iter(dest_dir.glob(f"{stem}.*")), None) if dest_dir.exists() else None
        )
        if existing is not None and existing.is_file():
            item.rel_path = str(existing.relative_to(config.download_dir))
            self._update(item, status="skipped", progress=1.0)
            return

        self._update(item, status="downloading", progress=0.0)

        # Throttle progress events so many concurrent downloads don't flood SSE.
        last = 0.0

        def on_progress(frac: float) -> None:
            nonlocal last
            if frac - last >= 0.01 or frac >= 1.0:
                last = frac
                self._update(item, progress=frac)

        try:
            path = download.download(
                item.media_url, dest_dir, stem, on_progress=on_progress
            )
            item.rel_path = str(path.relative_to(config.download_dir))
            self._update(item, status="completed", progress=1.0)
        except Exception as exc:
            self._update(
                item, status="failed", error=str(exc) or exc.__class__.__name__
            )


manager = DownloadManager()
