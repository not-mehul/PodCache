"""FastAPI app — a server-rendered, progressively-enhanced multi-page app.

The core flow (search → browse → download) is plain server routes and HTML
forms, so it works with JavaScript disabled and can never fall back to a blank
reload. JavaScript only enhances: it persists the theme and live-updates the
downloads queue over Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse

from . import feed, podcastindex, render
from .manager import manager

_STATIC = Path(__file__).resolve().parent.parent / "static"
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="PodCache", version="3.0.0", lifespan=lifespan)


def _page(html_text: str) -> HTMLResponse:
    return HTMLResponse(html_text, headers=_NO_STORE)


# ── static assets ────────────────────────────────────────────────────────────
@app.get("/static/app.css")
def css() -> FileResponse:
    return FileResponse(_STATIC / "app.css", media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/static/app.js")
def js() -> FileResponse:
    return FileResponse(
        _STATIC / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"}
    )


# ── pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return _page(render.page_home(downloads_count=manager.count()))


@app.get("/search", response_class=HTMLResponse)
def search(q: str = "") -> HTMLResponse:
    q = (q or "").strip()
    if not q:
        return _page(render.page_home(downloads_count=manager.count()))
    try:
        shows = podcastindex.search_shows(q)
        return _page(render.page_results(shows, q, downloads_count=manager.count()))
    except Exception as exc:
        return _page(render.page_results([], q, downloads_count=manager.count(), error=f"Search failed: {exc}"))


@app.get("/show", response_class=HTMLResponse)
def show(feed_url: str = Query(..., alias="feed"), q: str = "") -> HTMLResponse:
    try:
        data = feed.parse_feed(feed_url)
    except Exception as exc:
        return _page(
            render.page_results([], q, downloads_count=manager.count(), error=f"Could not read feed: {exc}")
        )
    return _page(render.page_show(data["show"], data["episodes"], q, downloads_count=manager.count()))


@app.post("/download")
async def start_download(request: Request) -> RedirectResponse:
    # Parse the URL-encoded form body with the stdlib (avoids a python-multipart
    # dependency just for a simple form).
    from urllib.parse import parse_qs

    body = (await request.body()).decode("utf-8")
    data = parse_qs(body, keep_blank_values=True)
    show_title = (data.get("show_title", ["Unknown Show"])[0] or "Unknown Show").strip()
    episodes = []
    for raw in data.get("episode", []):
        try:
            obj = json.loads(raw)
            if obj.get("u"):
                episodes.append({"media_url": obj["u"], "title": obj.get("t", "Episode")})
        except (json.JSONDecodeError, TypeError):
            continue
    if episodes:
        manager.enqueue({"title": show_title}, episodes)
    return RedirectResponse(url="/downloads", status_code=303)


@app.get("/downloads", response_class=HTMLResponse)
def downloads() -> HTMLResponse:
    return _page(render.page_downloads(manager.snapshot()))


@app.post("/downloads/clear")
def clear() -> RedirectResponse:
    manager.clear_finished()
    return RedirectResponse(url="/downloads", status_code=303)


@app.get("/file/{item_id}")
def file(item_id: str) -> Response:
    item = manager.get(item_id)
    path = manager.resolve_path(item) if item else None
    if path is None or not path.exists():
        return Response("File not available.", status_code=404)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


# ── live updates (enhancement) ─────────────────────────────────────────────────
@app.get("/events")
async def events(request: Request) -> StreamingResponse:
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
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
