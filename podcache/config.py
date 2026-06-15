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


@dataclass(frozen=True)
class Config:
    # Discovery
    podcastindex_key: str = os.environ.get("PODCASTINDEX_API_KEY", "")
    podcastindex_secret: str = os.environ.get("PODCASTINDEX_API_SECRET", "")

    # Ad detection
    anthropic_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    detect_model: str = os.environ.get("PODCACHE_DETECT_MODEL", "claude-opus-4-8")

    # Transcription
    whisper_model: str = os.environ.get("PODCACHE_WHISPER_MODEL", "base")
    whisper_compute: str = os.environ.get("PODCACHE_WHISPER_COMPUTE", "int8")
    whisper_device: str = os.environ.get("PODCACHE_WHISPER_DEVICE", "auto")

    # Storage / server
    data_dir: Path = _PROJECT_ROOT / os.environ.get("PODCACHE_DATA_DIR", "data")
    host: str = os.environ.get("PODCACHE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("PODCACHE_PORT", "8000"))

    @property
    def has_podcastindex(self) -> bool:
        return bool(self.podcastindex_key and self.podcastindex_secret)

    @property
    def has_claude(self) -> bool:
        return bool(self.anthropic_key)

    @property
    def search_provider(self) -> str:
        return "PodcastIndex" if self.has_podcastindex else "iTunes Search"

    @property
    def detector(self) -> str:
        return "Claude" if self.has_claude else "heuristic"


config = Config()
config.data_dir.mkdir(parents=True, exist_ok=True)
