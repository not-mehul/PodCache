"""FastAPI app: search, feed parsing, and a concurrent download queue with a
live (SSE) view of every download's progress."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import feed, podcastindex
from .config import config
from .manager import manager

_STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Hand the manager the server's loop so worker threads can push SSE events.
    manager.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="PodCache", version="2.0.0", lifespan=lifespan)


# ── models ──────────────────────────────────────────────────────────────────
class DownloadRequest(BaseModel):
    show: dict[str, Any]
    episodes: list[dict[str, Any]]


# ── pages / config ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "search_provider": config.search_provider,
        "concurrency": config.concurrency,
        "download_dir": str(config.download_dir),
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


# ── downloads ───────────────────────────────────────────────────────────────
@app.post("/api/download")
def start_download(req: DownloadRequest) -> dict[str, Any]:
    if not req.episodes:
        raise HTTPException(status_code=400, detail="No episodes selected.")
    ids = manager.enqueue(req.show, req.episodes)
    if not ids:
        raise HTTPException(status_code=400, detail="No downloadable episodes.")
    return {"queued": ids}


@app.get("/api/downloads")
def list_downloads() -> dict[str, Any]:
    return {"items": manager.snapshot()}


@app.post("/api/downloads/clear")
def clear_downloads() -> dict[str, str]:
    manager.clear_finished()
    return {"status": "cleared"}


@app.get("/api/downloads/{item_id}/file")
def download_file(item_id: str) -> FileResponse:
    item = manager.get(item_id)
    path = manager.resolve_path(item) if item else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="File not available.")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """Global SSE stream: a snapshot of the queue, then live per-item updates."""

    async def stream():
        queue = manager.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'snapshot', 'items': manager.snapshot()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            manager.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
