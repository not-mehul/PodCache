"""Runtime configuration, read once from the environment.

A `.env` file in the project root is loaded if present (without taking a hard
dependency on python-dotenv — a tiny parser handles it).
"""

from __future__ import annotations

import importlib.util
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

    # Ad detection — a small, local GGUF instruct model run via llama.cpp.
    # Point PODCACHE_LLM_PATH at a .gguf you already have for fully offline use,
    # or leave it unset to fetch `llm_repo`/`llm_file` once into `models/`.
    llm_path: str = os.environ.get("PODCACHE_LLM_PATH", "")
    llm_repo: str = os.environ.get(
        "PODCACHE_LLM_REPO", "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    )
    llm_file: str = os.environ.get(
        "PODCACHE_LLM_FILE", "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    )
    llm_ctx: int = int(os.environ.get("PODCACHE_LLM_CTX", "4096"))
    llm_threads: int = int(os.environ.get("PODCACHE_LLM_THREADS", "0"))  # 0 = auto

    # Transcription
    whisper_model: str = os.environ.get("PODCACHE_WHISPER_MODEL", "base")
    whisper_compute: str = os.environ.get("PODCACHE_WHISPER_COMPUTE", "int8")
    whisper_device: str = os.environ.get("PODCACHE_WHISPER_DEVICE", "auto")

    # Storage / server
    data_dir: Path = _PROJECT_ROOT / os.environ.get("PODCACHE_DATA_DIR", "data")
    models_dir: Path = _PROJECT_ROOT / os.environ.get("PODCACHE_MODELS_DIR", "models")
    host: str = os.environ.get("PODCACHE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("PODCACHE_PORT", "8000"))

    @property
    def has_podcastindex(self) -> bool:
        return bool(self.podcastindex_key and self.podcastindex_secret)

    @property
    def has_local_llm(self) -> bool:
        """True when llama-cpp-python is installed and a model is resolvable
        (either a local path on disk, or a repo/file we can fetch once)."""
        if importlib.util.find_spec("llama_cpp") is None:
            return False
        if self.llm_path and Path(self.llm_path).exists():
            return True
        return bool(self.llm_repo and self.llm_file)

    @property
    def llm_name(self) -> str:
        if self.llm_path:
            return Path(self.llm_path).stem
        return self.llm_file or "local model"

    @property
    def search_provider(self) -> str:
        return "PodcastIndex" if self.has_podcastindex else "iTunes Search"

    @property
    def detector(self) -> str:
        return "local LLM" if self.has_local_llm else "heuristic"


config = Config()
config.data_dir.mkdir(parents=True, exist_ok=True)
