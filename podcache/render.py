"""Server-side HTML rendering.

Every page is a full document produced here, so the core flow — search, browse,
download — works with zero JavaScript. JS only enhances (theme persistence, live
download progress). All dynamic text is HTML-escaped.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlencode

from .config import config

# ── icons (Lucide, MIT), inlined ────────────────────────────────────────────
_ICON = {
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
    "podcast": '<path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/>',
    "check": '<path d="m5 12 5 5L20 7"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "inbox": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
}


def icon(name: str, size: int = 16, stroke: float = 2) -> str:
    paths = _ICON.get(name, "")
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


def esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def attr(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# ── status verbs (§15) ──────────────────────────────────────────────────────
VERB = {
    "queued": "Queued",
    "downloading": "Downloading",
    "completed": "Completed",
    "failed": "Failed",
    "skipped": "Already saved",
}


def _clock(seconds: int | None) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ── layout ──────────────────────────────────────────────────────────────────
def _theme_toggle() -> str:
    return (
        '<button id="themeToggle" class="theme-toggle" role="switch" aria-checked="false" aria-label="Toggle theme">'
        f'<span class="ghost-icon ghost-moon">{icon("moon", 13)}</span>'
        f'<span class="ghost-icon ghost-sun">{icon("sun", 13)}</span>'
        f'<span class="knob">{icon("moon", 13)}</span>'
        "</button>"
    )


def _tabs(active: str, downloads_count: int) -> str:
    cnt = f' <span class="count">({downloads_count})</span>' if downloads_count else ""
    s_cls = ' class="active"' if active == "search" else ""
    d_cls = ' class="active"' if active == "downloads" else ""
    return (
        '<nav class="tabs">'
        f'<a href="/"{s_cls}>Search</a>'
        f'<a href="/downloads"{d_cls}>Downloads{cnt}</a>'
        "</nav>"
    )


def layout(*, title: str, active: str, body: str, downloads_count: int = 0) -> str:
    status = (
        f'<span class="item">Search · <b>{esc(config.search_provider)}</b></span><span class="sep"></span>'
        f'<span class="item">Concurrency · <b>{config.concurrency}</b></span>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="theme-color" content="#0f0d0b" />
<title>{esc(title)} · PodCache</title>
<link rel="stylesheet" href="/static/app.css" />
<script>
  // Apply the saved theme before first paint. Guarded: never block the page.
  try {{
    var m = null; try {{ m = localStorage.getItem('theme'); }} catch (e) {{}}
    if (!m) m = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    if (m === 'light') document.documentElement.setAttribute('data-theme', 'light');
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="glow-layer" aria-hidden="true"><div class="glow glow-1"></div><div class="glow glow-2"></div><div class="glow glow-3"></div></div>
<main class="app-shell">
  <header class="masthead">
    <div class="topline"><span class="eyebrow">Bulk podcast downloader</span>{_theme_toggle()}</div>
    <h1 class="page-title">Pod<em>Cache</em></h1>
    <p class="lede">Find a show, select the episodes you want, and download them in bulk — several at once, straight into a dedicated folder.</p>
    <div class="status-bar">{status}</div>
  </header>
  {_tabs(active, downloads_count)}
  {body}
  <footer class="footer">
    <p><b>How.</b> PodCache reads a show's RSS feed and downloads each episode's original audio enclosure as-is — tags and cover art intact, exactly as the publisher shipped it.</p>
    <p><b>Where.</b> Files land in <code>{esc(config.download_dir)}</code>, one folder per show. <b>Concurrency.</b> Up to {config.concurrency} download at once. <b>Privacy.</b> The server binds to <code>127.0.0.1</code>.</p>
  </footer>
</main>
<script src="/static/app.js" defer></script>
</body>
</html>"""


# ── components ───────────────────────────────────────────────────────────────
def _thumb(url: str, cls: str, fb_cls: str) -> str:
    if url:
        return (
            f'<img class="{cls}" src="{attr(url)}" alt="" '
            f"onerror=\"this.outerHTML='<div class=&quot;{fb_cls}&quot;>'+window.__mic+'</div>'\" />"
        )
    return f'<div class="{fb_cls}">{icon("podcast", 28)}</div>'


def search_form(query: str = "") -> str:
    return f"""<section class="section">
  <div class="section-head"><div class="lhs"><span class="marker">Search.</span><h2 class="section-title">Find a podcast</h2></div></div>
  <div class="panel">
    <form class="search-row" method="get" action="/search">
      <input type="search" name="q" value="{attr(query)}" placeholder="Show name  ·  e.g. Darknet Diaries, 99% Invisible" autocomplete="off" autofocus />
      <button class="btn-primary" type="submit">{icon("search", 14)} Search</button>
    </form>
  </div>
</section>"""


def page_home(downloads_count: int = 0) -> str:
    return layout(
        title="Search",
        active="search",
        downloads_count=downloads_count,
        body=search_form()
        + """<section class="section">
  <div class="empty-state"><div class="ei">"""
        + icon("podcast", 38, 1.5)
        + """</div><h3>Search for a show to begin</h3><p>Results appear here — pick a show to see its episodes.</p></div>
</section>""",
    )


def _show_card(show: dict[str, Any], q: str) -> str:
    href = "/show?" + urlencode({"feed": show["feed_url"], "q": q})
    author = f'<div class="cauthor">{esc(show["author"])}</div>' if show.get("author") else ""
    count = (
        f'<div class="cmeta">{esc(show["episode_count"])} episodes</div>'
        if show.get("episode_count")
        else ""
    )
    return (
        f'<a class="card" href="{attr(href)}">{_thumb(show.get("image", ""), "thumb", "thumb-fallback")}'
        f'<div class="body"><div class="ctitle">{esc(show["title"])}</div>{author}{count}</div></a>'
    )


def page_results(shows: list[dict[str, Any]], q: str, downloads_count: int = 0, error: str = "") -> str:
    if error:
        inner = f'<div class="msg-error">{esc(error)}</div>'
    elif not shows:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("search", 36, 1.5)
            + f"</div><h3>No shows for “{esc(q)}”</h3><p>Try a different spelling or a broader term.</p></div>"
        )
    else:
        cards = "".join(_show_card(s, q) for s in shows)
        inner = f'<div class="card-grid">{cards}</div>'
    title = f"{len(shows)} show{'s' if len(shows) != 1 else ''}" if shows and not error else "Shows"
    body = (
        search_form(q)
        + f"""<section class="section">
  <div class="section-head"><div class="lhs"><span class="marker">Candidates.</span><h2 class="section-title">{esc(title)}</h2></div></div>
  {inner}
</section>"""
    )
    return layout(title="Results", active="search", body=body, downloads_count=downloads_count)


def _episode_row(ep: dict[str, Any]) -> str:
    value = attr(json.dumps({"u": ep["media_url"], "t": ep.get("title", "Episode")}, separators=(",", ":")))
    # A clean date: drop any trailing time fragment from the RSS pubDate.
    pub = ep.get("published") or ""
    date = esc(re.sub(r"\s\d{2}:\d{2}:\d{2}.*$", "", pub)) if pub else ""
    dur = _clock(ep.get("duration"))
    meta = ""
    if date:
        meta += f"<span>{date}</span>"
    if date and dur:
        meta += '<span class="dot">·</span>'
    if dur:
        meta += f"<span>{esc(dur)}</span>"
    return (
        '<label class="ep check">'
        f'<input type="checkbox" class="ep-cb" name="episode" value="{value}" />'
        f'<span class="box">{icon("check", 12)}</span>'
        f'<div><div class="etitle">{esc(ep.get("title", "Episode"))}</div><div class="emeta">{meta}</div></div>'
        "</label>"
    )


def _show_url(feed_url: str, q: str, page: int) -> str:
    params: dict[str, Any] = {"feed": feed_url}
    if q:
        params["q"] = q
    if page > 1:
        params["page"] = page
    return "/show?" + urlencode(params)


def _pagination(feed_url: str, q: str, page: int, pages: int, total: int) -> str:
    if pages <= 1:
        return ""
    if page > 1:
        prev = f'<a class="btn-ghost" href="{attr(_show_url(feed_url, q, page - 1))}">{icon("arrow-left", 13)} Previous</a>'
    else:
        prev = f'<span class="btn-ghost disabled" aria-disabled="true">{icon("arrow-left", 13)} Previous</span>'
    if page < pages:
        nxt = f'<a class="btn-ghost" href="{attr(_show_url(feed_url, q, page + 1))}">Next {icon("arrow-right", 13)}</a>'
    else:
        nxt = f'<span class="btn-ghost disabled" aria-disabled="true">Next {icon("arrow-right", 13)}</span>'
    return (
        f'<div class="pagination">{prev}'
        f'<span class="page-info">Page {page} of {pages} · {total} episodes</span>{nxt}</div>'
    )


def page_show(
    show: dict[str, Any],
    episodes: list[dict[str, Any]],
    q: str,
    page: int = 1,
    downloads_count: int = 0,
    queued: int = 0,
) -> str:
    feed_url = show.get("feed_url", "")
    back = "/search?" + urlencode({"q": q}) if q else "/"
    page_size = config.page_size
    total = len(episodes)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    chunk = episodes[start : start + page_size]

    notice = ""
    if queued:
        notice = (
            f'<div class="notice"><b>{queued} episode{"s" if queued != 1 else ""} queued.</b> '
            'They are downloading — <a href="/downloads">view Downloads</a>.</div>'
        )

    if not episodes:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("podcast", 36, 1.5)
            + "</div><h3>No playable episodes</h3><p>This feed has no downloadable audio.</p></div>"
        )
    else:
        rows = "".join(_episode_row(ep) for ep in chunk)
        pag = _pagination(feed_url, q, page, pages, total)
        return_to = _show_url(feed_url, q, page)
        inner = f"""<form method="post" action="/download">
  <input type="hidden" name="show_title" value="{attr(show.get('title', 'Unknown Show'))}" />
  <input type="hidden" name="return_to" value="{attr(return_to)}" />
  <div class="ep-toolbar">
    <label class="check"><input type="checkbox" id="selectAll" /><span class="box">{icon('check', 12)}</span><span class="sel-label" id="selCount">Select all on page</span></label>
    <button class="btn-primary" type="submit" id="downloadBtn">{icon('download', 14)} <span id="dlBtnLabel">Download selected</span></button>
  </div>
  <div class="ep-list">{rows}</div>
  {pag}
</form>"""
    body = f"""<div class="back-link"><a class="btn-ghost" href="{attr(back)}">{icon('arrow-left', 13)} Back to shows</a></div>
{notice}<section class="section">
  <div class="section-head"><div class="lhs"><span class="marker">Episodes.</span><h2 class="section-title">{esc(show.get('title', 'Episodes'))}</h2></div></div>
  {inner}
</section>"""
    return layout(title=esc(show.get("title", "Episodes")), active="search", body=body, downloads_count=downloads_count)


def queue_item(it: dict[str, Any]) -> str:
    status = it["status"]
    pct = round((it.get("progress") or 0) * 100)
    indet = status == "downloading" and not it.get("progress")
    if status == "failed" and it.get("error"):
        meta_tail = f"<span>· {esc(it['error'])}</span>"
    elif status in ("completed", "skipped") and it.get("rel_path"):
        meta_tail = f'<span>· <a href="/file/{attr(it["id"])}" download>{esc(it["rel_path"])}</a></span>'
    elif status == "downloading":
        meta_tail = f"<span>· {pct}%</span>"
    else:
        meta_tail = ""
    prog_cls = " indeterminate" if indet else ""
    return (
        f'<div class="qitem {esc(status)}" id="dl-{attr(it["id"])}" data-status="{attr(status)}">'
        f'<div class="qrow"><span class="qname">{esc(it["title"])}</span>'
        f'<span class="qstatus">{esc(VERB.get(status, status))}</span></div>'
        f'<div class="qmeta"><span class="show">{esc(it["show"])}</span>{meta_tail}</div>'
        f'<div class="progress{prog_cls}"><div class="bar" style="width:{pct}%"></div></div>'
        "</div>"
    )


def page_downloads(items: list[dict[str, Any]]) -> str:
    active = sum(1 for i in items if i["status"] in ("queued", "downloading"))
    if not items:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("inbox", 38, 1.5)
            + "</div><h3>No downloads yet</h3><p>Search a show and select episodes to download.</p></div>"
        )
        clear = ""
        heading = "Queue"
    else:
        rows = "".join(queue_item(i) for i in items)
        inner = f'<div class="dl-list" id="dlList">{rows}</div>'
        clear = '<form method="post" action="/downloads/clear" style="margin:0"><button class="btn-ghost" type="submit">Clear finished</button></form>'
        heading = (
            f"{active} in progress · {len(items)} total" if active else f"{len(items)} download{'s' if len(items) != 1 else ''}"
        )
    body = f"""<section class="section" id="downloadsSection" data-active="{active}">
  <div class="section-head"><div class="lhs"><span class="marker">Downloads.</span><h2 class="section-title" id="downloadsTitle">{esc(heading)}</h2></div>{clear}</div>
  {inner}
</section>"""
    return layout(title="Downloads", active="downloads", body=body, downloads_count=len(items))
