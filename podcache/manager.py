"""Concurrent download queue.

A single global manager runs episode downloads through a thread pool whose size
is the configured concurrency limit, and broadcasts per-item progress to any
connected browsers over SSE. Files land in a dedicated downloads area, one
sub-folder per show.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from collections import defaultdict

from . import chapters, download, repetition, splice
from .config import config
from .download import safe_name


@dataclass
class DownloadItem:
    id: str
    show_title: str
    episode_title: str
    media_url: str
    image: str = ""
    chapters_url: str = ""
    batch_id: str = ""
    status: str = "queued"  # queued | downloading | completed | failed | skipped
    progress: float = 0.0
    error: str = ""
    rel_path: str = ""  # path relative to the downloads dir, once known
    ads_removed: int = 0  # number of ad segments cut
    ad_seconds: float = 0.0  # seconds of audio removed

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
            "ads_removed": self.ads_removed,
            "ad_seconds": round(self.ad_seconds, 1),
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
        # Per-batch remaining item ids, so repetition detection can fire once a
        # whole batch from one show has finished downloading.
        self._batches: dict[str, set[str]] = {}

    # ── wiring ────────────────────────────────────────────────────────────────
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the server's event loop so worker threads can reach it."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def _broadcast(self, event: dict[str, Any]) -> None:
        """Push an event to every subscriber (safe to call from any thread)."""
        if self._loop is None:
            return
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
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
        batch_id = uuid.uuid4().hex[:12]
        new_items: list[DownloadItem] = []
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
                chapters_url=ep.get("chapters_url") or "",
                batch_id=batch_id,
            )
            with self._lock:
                self._items[item.id] = item
                self._order.append(item.id)
            new_items.append(item)

        # Register the batch before any worker can finish, so the
        # batch-complete trigger can't be missed.
        with self._lock:
            self._batches[batch_id] = {it.id for it in new_items}

        for item in new_items:
            self._broadcast({"type": "item", **item.as_dict()})
            self._executor.submit(self._run, item)
        return [it.id for it in new_items]

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
        # Use the same name the downloader will write, so the skip-existing
        # check below actually matches the file on disk.
        dest_dir = config.download_dir / safe_name(item.show_title, "show")
        stem = safe_name(item.episode_title, "episode")

        # Skip if we already have this episode on disk. Match by filename prefix
        # (not glob) so titles containing [ ] * ? can't break the lookup.
        existing = None
        if dest_dir.exists():
            prefix = f"{stem}."
            for p in dest_dir.iterdir():
                if p.is_file() and p.name.startswith(prefix) and not p.name.endswith(".tmp"):
                    existing = p
                    break
        if existing is not None:
            item.rel_path = str(existing.relative_to(config.download_dir))
            self._update(item, status="skipped", progress=1.0)
            self._on_item_done(item)
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
            self._remove_ads(item, path)
            item.rel_path = str(path.relative_to(config.download_dir))
            self._update(item, status="completed", progress=1.0)
        except Exception as exc:
            self._update(
                item, status="failed", error=str(exc) or exc.__class__.__name__
            )
        self._on_item_done(item)

    def _remove_ads(self, item: DownloadItem, path: Path) -> None:
        """Tier 0 ad removal: cut sponsor-titled chapters. Never fails the job."""
        if not config.remove_ads:
            return
        try:
            chs = chapters.extract(path, item.chapters_url)
            ads = chapters.ad_segments(chs, splice.media_duration(path))
            if not ads:
                return
            did_cut, seconds = splice.cut(path, ads)
            if did_cut:
                item.ads_removed = len(ads)
                item.ad_seconds = seconds
        except Exception:
            pass  # ad removal is best-effort; keep the downloaded file regardless

    # ── cross-episode repetition (Tier 1) ─────────────────────────────────────
    def _on_item_done(self, item: DownloadItem) -> None:
        """When every item in a batch has finished, kick off repetition detection."""
        bid = item.batch_id
        if not bid:
            return
        fire = False
        with self._lock:
            remaining = self._batches.get(bid)
            if remaining is not None:
                remaining.discard(item.id)
                if not remaining:
                    self._batches.pop(bid, None)
                    fire = True
        if fire:
            threading.Thread(target=self._run_repetition, args=(bid,), daemon=True).start()

    def _run_repetition(self, batch_id: str) -> None:
        """Detect and cut segments that recur across the batch's episodes."""
        if not config.remove_ads:
            return
        if not (repetition.fpcalc_available() and splice.ffmpeg_available()):
            return
        with self._lock:
            members = [
                it
                for it in self._items.values()
                if it.batch_id == batch_id
                and it.status in ("completed", "skipped")
                and it.rel_path
            ]
        # Group by the on-disk show folder; dedupe each group independently.
        groups: dict[Path, list[DownloadItem]] = defaultdict(list)
        for it in members:
            groups[(config.download_dir / it.rel_path).parent].append(it)
        for items in groups.values():
            if len(items) >= config.dedupe_min_episodes:
                try:
                    self._dedupe_group(items)
                except Exception:
                    pass  # best-effort; downloads are already safe on disk

    def _dedupe_group(self, items: list[DownloadItem]) -> None:
        fingerprints: list[list[int]] = []
        item_secs: list[float] = []
        paired: list[tuple[DownloadItem, Path]] = []
        for it in items:
            path = config.download_dir / it.rel_path
            fp = repetition.fingerprint(path)
            if fp is None:
                continue
            fingerprints.append(fp[0])
            item_secs.append(fp[1])
            paired.append((it, path))
        if len(paired) < config.dedupe_min_episodes:
            return
        per_file = repetition.recurring_segments(fingerprints, item_secs)
        for (it, path), segs in zip(paired, per_file):
            if not segs:
                continue
            did_cut, seconds = splice.cut(path, segs)
            if did_cut:
                it.ads_removed += len(segs)
                it.ad_seconds += seconds
                self._broadcast({"type": "item", **it.as_dict()})


manager = DownloadManager()
