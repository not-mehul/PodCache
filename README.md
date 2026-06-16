# PodCache

> Find a podcast, select episodes, and download them in bulk.

PodCache is a local web app for **downloading podcast episodes** — search a
show, tick the episodes you want, and they download concurrently into a
dedicated folder, original audio and tags intact.

## How it works

1. **Search & discovery** — query a public podcast database, retrieve the
   show's RSS feed, and list its episodes.
2. **Select** — tick any number of episodes (or *Select all*).
3. **Bulk download** — episodes are queued and downloaded **concurrently**
   (configurable limit), with live per-item progress. Long back-catalogues are
   **paginated**, so you can reach and download well beyond the latest 50.
4. **Stored locally** — files land in a dedicated downloads area, **one
   sub-folder per show**. The original enclosure is saved as-is, so its ID3
   tags and cover art are preserved exactly as the publisher shipped them.

## Stack

| Concern | Tool |
| :-- | :-- |
| Search | [PodcastIndex.org](https://podcastindex.org) (free key) · keyless iTunes Search fallback |
| RSS parsing | `feedparser` |
| Download | `httpx` (chunked streaming) + a thread-pool queue for concurrency |
| Web server / UI | FastAPI + a single-page UI (the *Editorial Dusk & Dawn* design), live progress over Server-Sent Events |

## Setup

```bash
pip install -r requirements.txt

# (optional) configure the download folder, concurrency, or search keys
cp .env.example .env
```

No API keys are required — search falls back to the keyless iTunes Search API.

## Run

```bash
python run.py
```

Open **http://127.0.0.1:8000**. Search a show, click it, tick the episodes you
want, and hit **Download** — the queue at the top shows each download's progress
live, and finished files are linked from there (and saved on disk).

## Configuration

| Variable | Default | Purpose |
| :-- | :-- | :-- |
| `PODCACHE_DOWNLOAD_DIR` | `downloads` | Where episodes are saved (absolute or relative) |
| `PODCACHE_CONCURRENCY` | `3` | How many episodes download at once |
| `PODCACHE_PAGE_SIZE` | `50` | Episodes shown per page |
| `PODCACHE_FEED_LIMIT` | `2000` | Max episodes read from a feed |
| `PODCASTINDEX_API_KEY` / `_SECRET` | — | PodcastIndex search (optional) |
| `PODCACHE_HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |

## Architecture

PodCache is a **server-rendered multi-page app**. Every core action — search,
browse, download — is a plain server route and HTML form, so it works with
JavaScript disabled and never depends on a client-side fetch. JavaScript only
*enhances*: it persists the theme and live-updates the download queue over
Server-Sent Events.

```
podcache/
  server.py        routes (HTML pages, form POSTs, /events SSE, file serving)
  render.py        server-side HTML rendering (no template-engine dependency)
  podcastindex.py  search (PodcastIndex + iTunes fallback)
  feed.py          RSS parsing
  download.py      chunked HTTP download
  manager.py       concurrent download queue (thread pool + SSE broadcast)
static/
  app.css          the Editorial Dusk & Dawn design system
  app.js           progressive enhancement (theme toggle, live progress)
```

## Notes

- **Local-first.** The server binds to `127.0.0.1`; downloads stay in your
  chosen folder.
- Re-downloading an episode you already have is skipped automatically (the
  existing file on disk is reused).
- Works without JavaScript: search and downloads are server-rendered. With JS
  on, the download queue updates live.
