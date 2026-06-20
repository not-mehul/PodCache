"""Runtime configuration, read once from the environment.

A `.env` file in the project root is loaded if present (without taking a hard
dependency on python-dotenv — a tiny parser handles it).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a .env file, never overriding real env vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(_PROJECT_ROOT / ".env")


def _resolve_dir(value: str, default: Path) -> Path:
    """Allow PODCACHE_DOWNLOAD_DIR to be absolute or relative to the project."""
    if not value:
        return default
    p = Path(value).expanduser()
    return p if p.is_absolute() else _PROJECT_ROOT / p


@dataclass(frozen=True)
class Config:
    # Discovery
    podcastindex_key: str = os.environ.get("PODCASTINDEX_API_KEY", "")
    podcastindex_secret: str = os.environ.get("PODCASTINDEX_API_SECRET", "")

    # Downloads — the dedicated area finished files are written to, and how many
    # episodes may download at once.
    download_dir: Path = _resolve_dir(
        os.environ.get("PODCACHE_DOWNLOAD_DIR", ""), _PROJECT_ROOT / "downloads"
    )
    # Learned show profiles (recurring ad/intro/outro fingerprints) live here.
    profiles_dir: Path = _resolve_dir(
        os.environ.get("PODCACHE_PROFILES_DIR", ""), _PROJECT_ROOT / "profiles"
    )
    concurrency: int = max(1, int(os.environ.get("PODCACHE_CONCURRENCY", "3")))

    # Episode listing: how many per page, and the most to read from a feed.
    page_size: int = max(1, int(os.environ.get("PODCACHE_PAGE_SIZE", "50")))
    feed_limit: int = max(1, int(os.environ.get("PODCACHE_FEED_LIMIT", "2000")))

    # Ad removal — cut sponsor-titled chapters after download (needs ffmpeg on
    # PATH; a no-op when no chapters/ffmpeg are present).
    remove_ads: bool = os.environ.get("PODCACHE_REMOVE_ADS", "1").strip().lower() not in (
        "0", "false", "no", "off", ""
    )
    # Cross-episode repetition detection runs once a batch of at least this many
    # episodes from the same show has downloaded (needs fpcalc + ffmpeg).
    dedupe_min_episodes: int = max(
        2, int(os.environ.get("PODCACHE_DEDUPE_MIN_EPISODES", "3"))
    )
    # Review mode: when on, newly-detected recurring segments are saved as
    # *pending* patterns to listen to and confirm, instead of being cut
    # automatically. Confirmed patterns are always applied.
    review_ads: bool = os.environ.get("PODCACHE_REVIEW_ADS", "0").strip().lower() in (
        "1", "true", "yes", "on"
    )
    # Fingerprint matching tolerance (cross-episode + stored patterns). Chromaprint
    # items of the same audio differ by a few bits across re-encodes, so matching
    # is by Hamming distance, not equality.
    fp_max_bit_err: int = max(0, int(os.environ.get("PODCACHE_FP_MAX_BIT_ERR", "8")))
    # Minimum length (seconds) for a recurring segment to count as a pattern.
    fp_min_seconds: float = max(0.5, float(os.environ.get("PODCACHE_FP_MIN_SECONDS", "5")))
    fp_min_shows: int = max(2, int(os.environ.get("PODCACHE_FP_MIN_SHOWS", "2")))

    # Server
    host: str = os.environ.get("PODCACHE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("PODCACHE_PORT", "8000"))

    @property
    def has_podcastindex(self) -> bool:
        return bool(self.podcastindex_key and self.podcastindex_secret)

    @property
    def search_provider(self) -> str:
        return "PodcastIndex" if self.has_podcastindex else "iTunes Search"


config = Config()
config.download_dir.mkdir(parents=True, exist_ok=True)
