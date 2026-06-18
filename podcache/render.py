"""Server-side HTML rendering.

Every page is a full document produced here, so the core flow — search, browse,
download — works with zero JavaScript. JavaScript only enhances (theme toggle,
live download progress). All dynamic text is HTML-escaped.

The visual language is "Editorial Dusk & Dawn": a serif sentence-headline
masthead, hairline meta-grids for figures, and status-coloured lifecycle rows.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlencode

from .config import config

_GOOGLE_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;1,9..144,400&"
    'family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" />'
)

# ── icons (Lucide / Feather, MIT) ────────────────────────────────────────────
_ICON = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3-3"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
    "podcast": '<path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/>',
    "check": '<path d="m5 12 5 5L20 7"/>',
    "arrow-left": '<path d="m15 18-6-6 6-6"/>',
    "arrow-right": '<path d="m9 18 6-6-6-6"/>',
    "inbox": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "library": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    "scan": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
}


def icon(name: str, size: int = 16, stroke: float = 2) -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{_ICON.get(name, "")}</svg>'
    )


def esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def attr(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


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
    moon = (
        '<svg class="theme-ic theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
    )
    sun = (
        '<svg class="theme-ic theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
        'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    )
    return (
        '<button type="button" id="theme-toggle" class="theme-toggle" role="switch" '
        f'aria-checked="false" aria-label="Toggle theme" title="Toggle theme">{moon}{sun}'
        '<span class="theme-knob" aria-hidden="true"></span></button>'
    )


def _tabs(active: str, downloads_count: int) -> str:
    cnt = f' <span class="count">({downloads_count})</span>' if downloads_count else ""
    s = ' class="active"' if active == "search" else ""
    d = ' class="active"' if active == "downloads" else ""
    lib = ' class="active"' if active == "library" else ""
    return (
        '<nav class="tabs">'
        f'<a href="/"{s}>Search</a>'
        f'<a href="/downloads"{d}>Downloads{cnt}</a>'
        f'<a href="/library"{lib}>Library</a>'
        "</nav>"
    )


def layout(*, title: str, active: str, body: str, downloads_count: int = 0) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="theme-color" content="#0f0d0b" />
<title>{esc(title)} · PodCache</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
{_GOOGLE_FONTS}
<link rel="stylesheet" href="/static/app.css" />
<script>
  try {{
    var m = null; try {{ m = localStorage.getItem('theme'); }} catch (e) {{}}
    if (!m) m = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    if (m === 'light') {{
      document.documentElement.setAttribute('data-theme', 'light');
      var mc = document.querySelector('meta[name="theme-color"]'); if (mc) mc.setAttribute('content', '#efe6d8');
    }}
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="glow-layer" aria-hidden="true"><div class="glow glow-1"></div><div class="glow glow-2"></div><div class="glow glow-3"></div></div>
 <main class="page">
  <header class="masthead">
  <div class="masthead-top"><p class="eyebrow">PodCache · v3</p>{_theme_toggle()}</div>
  <h1 class="display">A quiet shelf for <em>podcast downloads</em>.</h1>
  <p class="lede">Search a show, choose the episodes, and pull them down in bulk — several at once, into a folder per podcast. Everything runs locally.</p>
  </header>
  {_tabs(active, downloads_count)}
  {body}
  <footer class="footer">
    <p><em>How.</em> PodCache reads a show's RSS feed and saves each episode's original audio as-is — tags and cover art intact, exactly as the publisher shipped it. <em>Where.</em> Files land in <span class="mono">{esc(config.download_dir)}</span>, one folder per show. <em>Privacy.</em> No telemetry; the server binds to <span class="mono">127.0.0.1</span>.</p>
      </footer>
</main>
<script src="/static/app.js" defer></script>
</body>
</html>"""


# ── shared bits ──────────────────────────────────────────────────────────────
def _thumb(url: str) -> str:
    if url:
        return (
            f'<div class="thumb-wrap"><img src="{attr(url)}" alt="" loading="lazy" '
            "onerror=\"this.parentNode.innerHTML='<div class=&quot;thumb-fallback&quot;>'+window.__mic+'</div>'\" /></div>"
        )
    return f'<div class="thumb-wrap"><div class="thumb-fallback">{icon("podcast", 30, 1.5)}</div></div>'


def search_section(query: str = "") -> str:
    return f"""<section class="section">
    <div class="section-head"><span class="marker">Source.</span><h2 class="section-title">Find a <em>podcast</em>.</h2></div>
    <form class="source-row" method="get" action="/search" autocomplete="off">
      <input type="search" name="q" value="{attr(query)}" spellcheck="false" placeholder="Show name  ·  e.g. Darknet Diaries, 99% Invisible" autofocus />
      <button class="btn btn-primary" type="submit">{icon("search", 14)} Search</button>
    </form>
    <p class="hint"><em>Discovery.</em> Results come from {esc(config.search_provider)}. Pick a show to browse and select its episodes.</p>
</section>"""


# ── pages ────────────────────────────────────────────────────────────────────
#
def page_home(downloads_count: int = 0) -> str:
    body = search_section() + (
        '<section class="section"><div class="empty-state"><div class="ei">'
        + icon("podcast", 38, 1.5)
        + "</div><h3>Search for a show to begin</h3><p>Pick a show to see its episodes, then download in bulk.</p></div></section>"
    )
    return layout(
        title="Search", active="search", body=body, downloads_count=downloads_count
    )


def _show_card(show: dict[str, Any], q: str) -> str:
    href = "/show?" + urlencode({"feed": show["feed_url"], "q": q})
    sub = (
        f'<div class="card-sub">{esc(show["author"])}</div>'
        if show.get("author")
        else ""
    )
    count = (
        f'<div class="card-meta">{esc(show["episode_count"])} episodes</div>'
        if show.get("episode_count")
        else ""
    )
    return (
        f'<a class="show-card" href="{attr(href)}">{_thumb(show.get("image", ""))}'
        f'<div class="card-body"><div class="card-title">{esc(show["title"])}</div>{sub}{count}</div></a>'
    )


def page_results(
    shows: list[dict[str, Any]], q: str, downloads_count: int = 0, error: str = ""
) -> str:
    if error:
        inner = f'<div class="msg-error">{esc(error)}</div>'
        heading = "Shows"
    elif not shows:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("search", 36, 1.5)
            + f"</div><h3>No shows for “{esc(q)}”</h3><p>Try a different spelling or a broader term.</p></div>"
        )
        heading = "Shows"
    else:
        inner = (
            '<div class="card-grid">'
            + "".join(_show_card(s, q) for s in shows)
            + "</div>"
        )
        heading = f"{len(shows)} show{'s' if len(shows) != 1 else ''}"
    body = search_section(q) + (
        f'<section class="section"><div class="section-head"><span class="marker">Candidates.</span>'
        f'<h2 class="section-title">{esc(heading)}</h2></div>{inner}</section>'
    )
    return layout(
        title="Results", active="search", body=body, downloads_count=downloads_count
    )


def _episode_row(ep: dict[str, Any]) -> str:
    payload = {"u": ep["media_url"], "t": ep.get("title", "Episode")}
    if ep.get("chapters_url"):
        payload["c"] = ep["chapters_url"]
    value = attr(json.dumps(payload, separators=(",", ":")))
    pub = ep.get("published") or ""
    date = esc(re.sub(r"\s\d{2}:\d{2}:\d{2}.*$", "", pub)) if pub else ""
    dur = _clock(ep.get("duration"))
    edur = f'<span class="edur">{esc(dur)}</span>' if dur else "<span></span>"
    return (
        '<label class="ep check">'
        f'<input type="checkbox" class="ep-cb" name="episode" value="{value}" />'
        f'<span class="box">{icon("check", 12)}</span>'
        f'<div><div class="etitle">{esc(ep.get("title", "Episode"))}</div><div class="emeta">{date}</div></div>'
        f"{edur}</label>"
    )


def _show_url(feed_url: str, q: str, page: int) -> str:
    params: dict[str, Any] = {"feed": feed_url}
    if q:
        params["q"] = q
    if page > 1:
        params["page"] = page
    return "/show?" + urlencode(params)


def _nav_btn(label: str, ic: str, href: str | None, *, right: bool = False) -> str:
    inner = f"{label} {icon(ic, 13)}" if right else f"{icon(ic, 13)} {label}"
    if href is None:
        return f'<span class="btn btn-ghost btn-sm disabled" aria-disabled="true">{inner}</span>'
    return f'<a class="btn btn-ghost btn-sm" href="{attr(href)}">{inner}</a>'


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
            'Downloading now — <a href="/downloads">view Downloads</a>.</div>'
        )

    if not episodes:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("podcast", 36, 1.5)
            + "</div><h3>No playable episodes</h3><p>This feed has no downloadable audio.</p></div>"
        )
    else:
        rows = "".join(_episode_row(ep) for ep in chunk)
        prev = _nav_btn(
            "Previous",
            "arrow-left",
            _show_url(feed_url, q, page - 1) if page > 1 else None,
        )
        nxt = _nav_btn(
            "Next",
            "arrow-right",
            _show_url(feed_url, q, page + 1) if page < pages else None,
            right=True,
        )
        meta_grid = f"""<div class="meta-grid">
  <div class="meta-cell"><span class="label">Show</span><span class="meta-value" title="{attr(show.get("title", ""))}">{esc(show.get("title", "—"))}</span></div>
  <div class="meta-cell"><span class="label">Page</span><span class="meta-value mono">{page} / {pages}</span></div>
  <div class="meta-cell"><span class="label">On this page</span><span class="meta-value mono">{len(chunk)}</span></div>
  <div class="meta-cell"><span class="label">Selected</span><span class="meta-value mono" id="metaSelected">0</span></div>
</div>"""
        inner = f"""{meta_grid}
<form method="post" action="/download">
  <input type="hidden" name="show_title" value="{attr(show.get("title", "Unknown Show"))}" />
  <input type="hidden" name="show_image" value="{attr(show.get("image", ""))}" />
  <input type="hidden" name="return_to" value="{attr(_show_url(feed_url, q, page))}" />
  <div class="meta-actions">
    {prev}{nxt}<span class="spacer"></span>
    <button type="button" class="btn btn-ghost btn-sm" id="selectPage">Select page</button>
    <button type="button" class="btn btn-ghost btn-sm" id="clearSel">Clear</button>
    <button type="submit" class="btn btn-primary btn-sm" id="downloadBtn">{icon("download", 14)} <span id="dlBtnLabel">Download selected</span></button>
    </div>
  <div class="ep-list">{rows}</div>
  <div class="meta-actions" style="margin-top:1rem"><span class="page-info">Page {page} of {pages} · {total} episodes</span><span class="spacer"></span>{prev}{nxt}</div>
</form>"""
        body = f"""<div class="back-link"><a class="btn btn-ghost btn-sm" href="{attr(back)}">{icon("arrow-left", 13)} Back to shows</a></div>
{notice}<section class="section">
<div class="section-head"><span class="marker">Episodes.</span><h2 class="section-title">{esc(show.get("title", "Episodes"))}</h2></div>
  {inner}
</section>"""
    return layout(
        title=esc(show.get("title", "Episodes")),
        active="search",
        body=body,
        downloads_count=downloads_count,
    )


def _qthumb(image: str) -> str:
    if image:
        return (
            f'<div class="qthumb"><img src="{attr(image)}" alt="" loading="lazy" '
            'onerror="this.parentNode.innerHTML=window.__mic" /></div>'
        )
    return f'<div class="qthumb">{icon("podcast", 22, 1.5)}</div>'


def queue_item(it: dict[str, Any]) -> str:
    status = it["status"]
    pct = round((it.get("progress") or 0) * 100)
    indet = status == "downloading" and not it.get("progress")
    chips = ""
    if status == "failed" and it.get("error"):
        chips = f'<span class="qchip mono">{esc(it["error"])}</span>'
    elif status in ("completed", "skipped") and it.get("rel_path"):
        chips = f'<a href="/file/{attr(it["id"])}" download>{esc(it["rel_path"])}</a>'
    elif status == "downloading":
        chips = f'<span class="qchip mono">{pct}%</span>'
    if it.get("ads_removed"):
        n = it["ads_removed"]
        cut = _clock(round(it.get("ad_seconds") or 0))
        chips += f'<span class="qchip ads">{n} ad{"s" if n != 1 else ""} cut · {cut}</span>'

    prog_cls = " indeterminate" if indet else ""
    return (
        f'<div class="qitem {esc(status)}" id="dl-{attr(it["id"])}" data-status="{attr(status)}">'
        f"{_qthumb(it.get('image', ''))}"
        '<div class="qbody">'
        f'<p class="qtitle">{esc(it["title"])}</p>'
        '<div class="qmeta">'
        f'<span class="status-verb">{esc(VERB.get(status, status))}</span>'
        f'<span class="qchip">{esc(it["show"])}</span>{chips}</div>'
        f'<div class="qprogress{prog_cls}"><div class="bar" style="width:{pct}%"></div></div>'
        "</div></div>"
    )


def page_downloads(items: list[dict[str, Any]]) -> str:
    active = sum(1 for i in items if i["status"] in ("queued", "downloading"))
    if not items:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("inbox", 38, 1.5)
            + "</div><h3>No downloads yet</h3><p>Search a show and select episodes to download.</p></div>"
        )
        controls = ""
        heading = "Queue"
    else:
        inner = (
            '<div class="queue-list">'
            + "".join(queue_item(i) for i in items)
            + "</div>"
        )
        controls = (
            '<div class="controls"><form method="post" action="/downloads/clear" style="margin:0">'
            '<button class="btn btn-ghost btn-sm" type="submit">Clear finished</button></form></div>'
        )
        heading = (
            f"{active} in progress · {len(items)} total"
            if active
            else f"{len(items)} download{'s' if len(items) != 1 else ''}"
        )
    body = f"""<section class="section">
      <div class="section-head"><span class="marker">Queue.</span><h2 class="section-title" id="downloadsTitle">{esc(heading)}</h2>{controls}</div>
      {inner}
</section>"""
    return layout(
        title="Downloads", active="downloads", body=body, downloads_count=len(items)
    )


# ── library / show profiles ──────────────────────────────────────────────────
def _scan_button(show_key: str, tools_ok: bool, label: str = "Scan for ads") -> str:
    dis = "" if tools_ok else " disabled"
    return (
        f'<form method="post" action="/library/scan" style="margin:0">'
        f'<input type="hidden" name="show" value="{attr(show_key)}" />'
        f'<button class="btn btn-ghost btn-sm" type="submit"{dis}>{icon("scan", 13)} {esc(label)}</button>'
        "</form>"
    )


def _tools_notice(tools_ok: bool) -> str:
    if tools_ok:
        return ""
    return (
        '<div class="notice"><b>Ad scanning is off.</b> Install <span class="mono">ffmpeg</span> '
        'and <span class="mono">fpcalc</span> (Chromaprint) to detect and cut recurring '
        "intros, outros, and ads across a show’s episodes.</div>"
    )


def page_library(shows: list[dict[str, Any]], tools_ok: bool, downloads_count: int = 0) -> str:
    if not shows:
        inner = (
            '<div class="empty-state"><div class="ei">'
            + icon("library", 38, 1.5)
            + "</div><h3>No shows downloaded yet</h3><p>Download some episodes; they’ll appear here to scan and review.</p></div>"
        )
    else:
        rows = []
        for s in shows:
            href = "/library/show?" + urlencode({"name": s["key"]})
            meta = f'{s["episodes"]} episode{"s" if s["episodes"] != 1 else ""} · {s["patterns"]} pattern{"s" if s["patterns"] != 1 else ""}'
            pend = (
                f'<span class="qchip ads">{s["pending"]} to review</span>'
                if s.get("pending")
                else ""
            )
            rows.append(
                '<div class="lib-row">'
                f'<div class="lib-main"><div class="lib-title">{esc(s["key"])}</div>'
                f'<div class="lib-meta">{esc(meta)} {pend}</div></div>'
                f'<div class="lib-actions">{_scan_button(s["key"], tools_ok)}'
                f'<a class="btn btn-ghost btn-sm" href="{attr(href)}">Review {icon("arrow-right", 13)}</a></div>'
                "</div>"
            )
        inner = '<div class="lib-list">' + "".join(rows) + "</div>"
    body = _tools_notice(tools_ok) + (
        '<section class="section"><div class="section-head"><span class="marker">Library.</span>'
        f'<h2 class="section-title">Your <em>shows</em></h2></div>{inner}</section>'
    )
    return layout(title="Library", active="library", body=body, downloads_count=downloads_count)


_LABEL_NOUN = {"ad": "Ad", "intro": "Intro", "outro": "Outro"}


def _pattern_card(show_key: str, pat: Any, tools_ok: bool) -> str:
    secs = _clock(round(pat.seconds))
    label_chip = f'<span class="qchip ads">{esc(_LABEL_NOUN.get(pat.label, pat.label))}</span>'
    status_chip = f'<span class="qchip">{esc(pat.status)}</span>'
    seen = f'<span class="qchip">seen ×{pat.episodes_seen}</span>'
    length = f'<span class="qchip mono">{esc(secs)}</span>'

    audio = ""
    if pat.clip:
        src = "/clip?" + urlencode({"show": show_key, "pattern": pat.id})
        audio = f'<audio controls preload="none" src="{attr(src)}"></audio>'
    else:
        audio = '<p class="hint">Preview unavailable (no clip on disk).</p>'

    def _post(action: str, label: str, text: str, primary: bool = False) -> str:
        cls = "btn btn-primary btn-sm" if primary else "btn btn-ghost btn-sm"
        extra = f'<input type="hidden" name="label" value="{attr(label)}" />' if label else ""
        return (
            '<form method="post" action="/library/pattern" style="margin:0">'
            f'<input type="hidden" name="show" value="{attr(show_key)}" />'
            f'<input type="hidden" name="pattern_id" value="{attr(pat.id)}" />'
            f'<input type="hidden" name="action" value="{attr(action)}" />{extra}'
            f'<button class="{cls}" type="submit">{esc(text)}</button></form>'
        )

    if pat.status == "pending":
        actions = (
            _post("confirm", "ad", "Confirm: Ad", primary=True)
            + _post("confirm", "intro", "Intro")
            + _post("confirm", "outro", "Outro")
            + _post("reject", "", "Not an ad")
        )
    elif pat.status == "confirmed":
        actions = (
            _post("relabel", "ad", "Ad")
            + _post("relabel", "intro", "Intro")
            + _post("relabel", "outro", "Outro")
            + _post("reject", "", "Remove")
        )
    else:  # rejected
        actions = _post("confirm", pat.label, "Restore")

    return (
        '<div class="pat-card">'
        f'<div class="chip-row">{label_chip}{status_chip}{length}{seen}</div>'
        f"{audio}"
        f'<div class="pat-actions">{actions}</div>'
        "</div>"
    )


def page_show_profile(show_key: str, prof: Any, tools_ok: bool, downloads_count: int = 0) -> str:
    order = {"pending": 0, "confirmed": 1, "rejected": 2}
    pats = sorted(prof.patterns, key=lambda p: (order.get(p.status, 3), -p.episodes_seen))
    visible = [p for p in pats if p.status != "rejected"]
    if not visible:
        cards = (
            '<div class="empty-state"><div class="ei">'
            + icon("scissors", 36, 1.5)
            + "</div><h3>No patterns yet</h3><p>Scan this show to detect intros, outros, and recurring ads.</p></div>"
        )
    else:
        cards = '<div class="stack">' + "".join(_pattern_card(show_key, p, tools_ok) for p in visible) + "</div>"
    pending = sum(1 for p in prof.patterns if p.status == "pending")
    sub = f"{pending} to review · {len(visible)} pattern{'s' if len(visible) != 1 else ''}"
    body = (
        f'<div class="back-link"><a class="btn btn-ghost btn-sm" href="/library">{icon("arrow-left", 13)} Back to library</a></div>'
        + _tools_notice(tools_ok)
        + '<section class="section"><div class="section-head"><span class="marker">Patterns.</span>'
        + f'<h2 class="section-title">{esc(show_key)}</h2>'
        + f'<div class="controls">{_scan_button(show_key, tools_ok, "Re-scan")}</div></div>'
        + f'<p class="hint" style="margin-top:0">{esc(sub)}</p>'
        + cards
        + "</section>"
    )
    return layout(title=esc(show_key), active="library", body=body, downloads_count=downloads_count)
