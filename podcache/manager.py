"""Concurrent download queue.

A single global manager runs episode downloads through a thread pool whose size
is the configured concurrency limit, and broadcasts per-item progress to any
connected browsers over SSE. Files land in a dedicated downloads area, one
sub-folder per show.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import chapters, download, profile, repetition, splice
from .config import config
from .download import safe_name

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".wav", ".mp4")


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
        """After a batch downloads, learn/apply patterns per show folder."""
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
        groups: dict[Path, list[DownloadItem]] = defaultdict(list)
        for it in members:
            groups[(config.download_dir / it.rel_path).parent].append(it)
        for folder, items in groups.items():
            item_by_path = {
                str((config.download_dir / it.rel_path)): it for it in items
            }
            try:
                self._process_folder(folder, item_by_path)
            except Exception:
                pass  # best-effort; downloads are already safe on disk

    # ── show profiles ──────────────────────────────────────────────────────────
    def _audio_files(self, folder: Path) -> list[Path]:
        if not folder.exists():
            return []
        return sorted(
            p
            for p in folder.iterdir()
            if p.is_file()
            and p.suffix.lower() in AUDIO_EXTS
            and not p.name.endswith(".tmp")
        )

    def _process_folder(
        self, folder: Path, item_by_path: dict[str, DownloadItem] | None = None
    ) -> None:
        """Detect recurring segments across a show's files, persist them as
        patterns, and cut — auto, or (in review mode) only confirmed ones."""
        show_key = folder.name
        files = self._audio_files(folder)
        if not files:
            return
        prof = profile.load(show_key)

        fps: dict[Path, tuple[list[int], float]] = {}
        for p in files:
            fp = repetition.fingerprint(p)
            if fp is not None:
                fps[p] = fp
        if not fps:
            return

        paths = list(fps.keys())
        fingerprints = [fps[p][0] for p in paths]
        item_secs = [fps[p][1] for p in paths]
        review = config.review_ads

        # Cross-detect new recurring segments only when there are enough files.
        if len(paths) >= config.dedupe_min_episodes:
            detected = repetition.recurring_segments(fingerprints, item_secs)
        else:
            detected = [[] for _ in paths]

        win = repetition._WINDOW
        for idx, p in enumerate(paths):
            items_fp, isec = fingerprints[idx], item_secs[idx]
            auto_cut: list[tuple[float, float]] = []
            for (s, e) in detected[idx]:
                seg_items = items_fp[int(round(s / isec)) : int(round(e / isec))]
                if len(seg_items) < win:
                    continue
                pat, is_new = profile.add_or_update(
                    prof, seg_items, isec, e - s,
                    status="pending" if review else "confirmed",
                )
                if review and is_new:
                    # Save a preview clip from this (still-uncut) file.
                    clip_rel = f"{show_key}/clips/{pat.id}{p.suffix}"
                    if splice.extract_clip(p, s, e, config.profiles_dir / clip_rel):
                        pat.clip = clip_rel
                elif not review:
                    auto_cut.append((s, e))

            confirmed = profile.apply_profile(prof, items_fp, isec, statuses=("confirmed",))
            cut_segs = profile._merge(auto_cut + confirmed)
            if cut_segs:
                did_cut, seconds = splice.cut(p, cut_segs)
                if did_cut:
                    it = (item_by_path or {}).get(str(p))
                    if it is not None:
                        it.ads_removed += len(cut_segs)
                        it.ad_seconds += seconds
                        self._broadcast({"type": "item", **it.as_dict()})
        profile.save(prof)

    # Public profile operations (called from the web layer) ----------------------
    def list_shows(self) -> list[dict[str, Any]]:
        shows: list[dict[str, Any]] = []
        if config.download_dir.exists():
            for d in sorted(config.download_dir.iterdir()):
                if not d.is_dir():
                    continue
                prof = profile.load(d.name)
                shows.append(
                    {
                        "key": d.name,
                        "episodes": len(self._audio_files(d)),
                        "patterns": len(prof.patterns),
                        "pending": sum(1 for x in prof.patterns if x.status == "pending"),
                        "confirmed": sum(1 for x in prof.patterns if x.status == "confirmed"),
                    }
                )
        return shows

    def show_profile(self, show_key: str) -> profile.ShowProfile:
        return profile.load(show_key)

    def scan_show(self, show_key: str) -> bool:
        folder = config.download_dir / safe_name(show_key, "show")
        if folder.name != show_key:  # reject traversal / mismatched keys
            folder = config.download_dir / show_key
        if not folder.exists() or folder.parent != config.download_dir:
            return False
        if not (repetition.fpcalc_available() and splice.ffmpeg_available()):
            return False
        threading.Thread(target=self._process_folder, args=(folder, None), daemon=True).start()
        return True

    def confirm_pattern(self, show_key: str, pattern_id: str, label: str = "") -> bool:
        prof = profile.load(show_key)
        pat = prof.by_id(pattern_id)
        if pat is None:
            return False
        pat.status = "confirmed"
        if label in profile.LABELS:
            pat.label = label
        pat.updated_at = time.time()
        profile.save(prof)
        folder = config.download_dir / show_key
        if folder.exists():
            threading.Thread(target=self._process_folder, args=(folder, None), daemon=True).start()
        return True

    def reject_pattern(self, show_key: str, pattern_id: str) -> bool:
        prof = profile.load(show_key)
        pat = prof.by_id(pattern_id)
        if pat is None:
            return False
        pat.status = "rejected"
        pat.updated_at = time.time()
        if pat.clip:
            try:
                (config.profiles_dir / pat.clip).unlink()
            except Exception:
                pass
            pat.clip = ""
        profile.save(prof)
        return True

    def relabel_pattern(self, show_key: str, pattern_id: str, label: str) -> bool:
        if label not in profile.LABELS:
            return False
        prof = profile.load(show_key)
        pat = prof.by_id(pattern_id)
        if pat is None:
            return False
        pat.label = label
        pat.updated_at = time.time()
        profile.save(prof)
        return True

    def clip_path(self, show_key: str, pattern_id: str) -> Path | None:
        prof = profile.load(show_key)
        pat = prof.by_id(pattern_id)
        if pat is None or not pat.clip:
            return None
        path = (config.profiles_dir / pat.clip).resolve()
        if config.profiles_dir.resolve() not in path.parents or not path.exists():
            return None
        return path


manager = DownloadManager()
