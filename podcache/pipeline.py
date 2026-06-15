"""Job orchestration.

A `Job` walks one episode through the whole pipeline — download, transcribe,
detect, splice, restore metadata — on a background thread, pushing typed
progress events onto an asyncio queue that the web layer streams to the browser
over Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import download, transcribe, detect, splice, metadata
from .config import config

# The ordered lifecycle the UI renders as a row per stage.
STAGES = ("download", "transcribe", "detect", "splice", "metadata")


@dataclass
class Job:
    id: str
    episode: dict[str, Any]
    show: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    status: str = "queued"  # queued | running | completed | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    result_path: Path | None = None

    def emit(self, event: dict[str, Any]) -> None:
        """Record an event and hand it to the SSE queue (thread-safe)."""
        self.events.append(event)
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def stage(self, name: str, status: str, progress: float = 0.0, message: str = "") -> None:
        self.emit(
            {
                "type": "stage",
                "stage": name,
                "status": status,
                "progress": round(progress, 3),
                "message": message,
            }
        )

    def log(self, message: str) -> None:
        self.emit({"type": "log", "message": message})


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def create(
        self,
        episode: dict[str, Any],
        show: dict[str, Any],
        loop: asyncio.AbstractEventLoop,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], episode=episode, show=show, loop=loop)
        self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    # ── worker ──────────────────────────────────────────────────────────────
    def _run(self, job: Job) -> None:
        job.status = "running"
        ep, show = job.episode, job.show
        work = config.data_dir / job.id
        work.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Download ----------------------------------------------------
            job.stage("download", "active", 0.0, "Fetching the raw episode…")
            raw = download.download(
                ep["media_url"],
                work,
                ep.get("title", "episode"),
                on_progress=lambda f: job.stage("download", "active", f),
            )
            job.stage("download", "completed", 1.0, f"Saved {raw.name}")

            # 2. Transcribe --------------------------------------------------
            job.stage(
                "transcribe", "active", 0.0,
                f"Transcribing with Whisper ({config.whisper_model})…",
            )
            segments = transcribe.transcribe(
                raw, on_progress=lambda f: job.stage("transcribe", "active", f)
            )
            job.stage(
                "transcribe", "completed", 1.0,
                f"{len(segments)} sentences transcribed",
            )

            # 3. Detect ads --------------------------------------------------
            job.stage(
                "detect", "active", 0.0,
                f"Finding ads with the {config.detector} detector…",
            )
            ads = detect.detect_ads(
                segments, on_progress=lambda f: job.stage("detect", "active", f)
            )
            job.emit({"type": "ads", "ads": [a.as_dict() for a in ads]})
            removed = sum(a.end - a.start for a in ads)
            job.stage(
                "detect", "completed", 1.0,
                f"{len(ads)} ad segment(s) found · {removed:.0f}s to cut",
            )

            # 4. Splice ------------------------------------------------------
            job.stage("splice", "active", 0.0, "Cutting and stitching audio…")
            out_name = f"{raw.stem}.adfree{raw.suffix}"
            out_path = work / out_name
            splice.splice(raw, ads, out_path)
            job.stage("splice", "completed", 1.0, "Audio reassembled")

            # 5. Restore metadata --------------------------------------------
            job.stage("metadata", "active", 0.0, "Restoring tags and cover art…")
            metadata.restore(raw, out_path, episode=ep, show=show)
            job.stage("metadata", "completed", 1.0, "Metadata embedded")

            job.result_path = out_path
            job.status = "completed"
            job.emit(
                {
                    "type": "done",
                    "filename": out_name,
                    "download_url": f"/api/jobs/{job.id}/download",
                    "removed_count": len(ads),
                    "saved_seconds": round(removed),
                }
            )
        except Exception as exc:  # surface a clean message; keep the trace in logs
            job.status = "failed"
            traceback.print_exc()
            job.emit({"type": "error", "message": str(exc) or exc.__class__.__name__})


manager = JobManager()
