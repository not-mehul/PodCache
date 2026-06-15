"""Metadata restoration.

Copies ID3 tags and embedded cover art from the original download onto the
finished, ad-free file so it still looks right in any podcast player. Falls
back to writing fresh tags from the RSS metadata when the source has none.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


def _copy_id3(source: Path, dest: Path) -> bool:
    """Copy the raw ID3 tag block (title, artist, album, year, APIC art)."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
    except Exception:
        return False
    try:
        src_tags = ID3(source)
    except ID3NoHeaderError:
        return False
    except Exception:
        return False
    try:
        try:
            dst_tags = ID3(dest)
        except Exception:
            dst_tags = ID3()
        for key in src_tags.keys():
            dst_tags[key] = src_tags[key]
        dst_tags.save(dest)
        return True
    except Exception:
        return False


def _write_from_rss(dest: Path, episode: dict[str, Any], show: dict[str, Any]) -> None:
    """Write a minimal tag set from the RSS metadata, including cover art."""
    try:
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
    except Exception:
        return
    try:
        tags = ID3(dest)
    except ID3NoHeaderError:
        tags = ID3()
    except Exception:
        tags = ID3()

    if episode.get("title"):
        tags["TIT2"] = TIT2(encoding=3, text=episode["title"])
    if show.get("author"):
        tags["TPE1"] = TPE1(encoding=3, text=show["author"])
    if show.get("title"):
        tags["TALB"] = TALB(encoding=3, text=show["title"])

    image_url = episode.get("image") or show.get("image")
    if image_url and "APIC:" not in tags:
        try:
            resp = httpx.get(image_url, timeout=20, follow_redirects=True)
            if resp.status_code == 200:
                mime = resp.headers.get("content-type", "image/jpeg")
                tags["APIC"] = APIC(
                    encoding=3, mime=mime, type=3, desc="Cover", data=resp.content
                )
        except Exception:
            pass
    try:
        tags.save(dest)
    except Exception:
        pass


def restore(
    source: Path,
    dest: Path,
    episode: dict[str, Any] | None = None,
    show: dict[str, Any] | None = None,
) -> None:
    """Restore metadata onto `dest`. Only meaningful for MP3/ID3 files."""
    if dest.suffix.lower() != ".mp3":
        return  # ID3 is MP3-specific; leave other containers untouched.
    copied = _copy_id3(source, dest)
    if not copied and episode is not None and show is not None:
        _write_from_rss(dest, episode, show)
