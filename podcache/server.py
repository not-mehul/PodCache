"""FastAPI app: search, feed parsing, job creation, and the SSE event stream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import feed, podcastindex
from .config import config
from .pipeline import STAGES, manager

app = FastAPI(title="PodCache", version="1.0.0")

_STATIC = Path(__file__).resolve().parent.parent / "static"


# ── models ──────────────────────────────────────────────────────────────────
class ProcessRequest(BaseModel):
    episode: dict[str, Any]
    show: dict[str, Any]


# ── pages ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    """What backends are live — used to label the UI honestly."""
    return {
        "search_provider": config.search_provider,
        "detector": config.detector,
        "whisper_model": config.whisper_model,
        "stages": list(STAGES),
    }


# ── discovery ───────────────────────────────────────────────────────────────
@app.get("/api/search")
def search(q: str) -> dict[str, Any]:
    try:
        shows = podcastindex.search_shows(q)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search failed: {exc}")
    return {"shows": shows}


@app.get("/api/episodes")
def episodes(feed_url: str) -> dict[str, Any]:
    try:
        return feed.parse_feed(feed_url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Feed parse failed: {exc}")


# ── processing ──────────────────────────────────────────────────────────────
@app.post("/api/process")
async def process(req: ProcessRequest) -> dict[str, str]:
    if not req.episode.get("media_url"):
        raise HTTPException(status_code=400, detail="Episode has no media URL.")
    # Capture the server's running loop here (on the event-loop thread) so the
    # background worker can hand SSE events back to it across threads.
    loop = asyncio.get_running_loop()
    job = manager.create(req.episode, req.show, loop)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job.")

    async def stream():
        # Replay anything already emitted (covers reconnects / late subscribers).
        for event in list(job.events):
            yield f"data: {json.dumps(event)}\n\n"
        terminal = {"done", "error"}
        if job.events and job.events[-1].get("type") in terminal:
            return
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(job.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"  # comment frame keeps the connection open
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in terminal:
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/download")
def download_result(job_id: str) -> FileResponse:
    job = manager.get(job_id)
    if job is None or job.result_path is None or not job.result_path.exists():
        raise HTTPException(status_code=404, detail="Result not ready.")
    return FileResponse(
        job.result_path,
        filename=job.result_path.name,
        media_type="application/octet-stream",
    )
