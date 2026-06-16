"""Audio ingestion.

Streams the episode enclosure to a local file in chunks (so a 200 MB episode
never has to sit in memory), reporting progress as it goes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

_CHUNK = 1 << 16  # 64 KiB


def _extension_for(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".mp3", ".m4a", ".aac", ".ogg", ".wav", ".mp4"):
        if path.endswith(ext):
            return ext
    if "mpeg" in content_type:
        return ".mp3"
    if "mp4" in content_type or "m4a" in content_type or "aac" in content_type:
        return ".m4a"
    return ".mp3"


def download(
    url: str,
    dest_dir: Path,
    basename: str,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Download `url` into `dest_dir`. Returns the path to the saved file.

    `on_progress` receives a 0..1 fraction (best-effort; some feeds omit the
    Content-Length header, in which case progress is reported as indeterminate
    via fractions that stay at 0).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("_") or "episode"

    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        ext = _extension_for(str(resp.url), resp.headers.get("content-type", ""))
        dest = dest_dir / f"{safe}{ext}"
        dest_tmp = dest.with_suffix(dest.suffix + ".tmp")
        total = int(resp.headers.get("content-length") or 0)
        written = 0
        try:
            with dest_tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    fh.write(chunk)
                    written += len(chunk)
                    if on_progress and total:
                        on_progress(min(written / total, 1.0))
            if dest_tmp.exists():
                dest_tmp.replace(dest)
        except Exception:
            if dest_tmp.exists():
                try:
                    dest_tmp.unlink()
                except Exception:
                    pass
            raise

    if on_progress:
        on_progress(1.0)
    return dest
