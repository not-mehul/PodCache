"""Local language model (llama.cpp / GGUF).

Loads a small instruct model once and serves JSON-constrained completions on
CPU. Nothing leaves the machine: point `PODCACHE_LLM_PATH` at a `.gguf` for a
fully offline setup, or let PodCache fetch the configured model once into
`models/` (a one-time weight download, after which inference is entirely local).
"""

from __future__ import annotations

from pathlib import Path

from .config import config

_llm = None  # lazily-loaded singleton; constructing the model is expensive


class ModelUnavailable(RuntimeError):
    pass


def _resolve_model_path() -> str:
    """Return a local path to the GGUF, downloading it once if necessary."""
    if config.llm_path:
        path = Path(config.llm_path)
        if not path.exists():
            raise ModelUnavailable(f"PODCACHE_LLM_PATH does not exist: {path}")
        return str(path)

    if not (config.llm_repo and config.llm_file):
        raise ModelUnavailable("No local model configured (set PODCACHE_LLM_PATH).")

    config.models_dir.mkdir(parents=True, exist_ok=True)
    cached = config.models_dir / config.llm_file
    if cached.exists():
        return str(cached)

    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:  # pragma: no cover - import guard
        raise ModelUnavailable(
            "Model weights are not present locally and huggingface_hub is not "
            "installed to fetch them. Install it, or set PODCACHE_LLM_PATH."
        ) from exc

    downloaded = hf_hub_download(
        repo_id=config.llm_repo,
        filename=config.llm_file,
        local_dir=str(config.models_dir),
    )
    return downloaded


def get_llm():
    """Return the loaded llama.cpp model, constructing it on first use."""
    global _llm
    if _llm is None:
        try:
            from llama_cpp import Llama
        except Exception as exc:  # pragma: no cover - import guard
            raise ModelUnavailable("llama-cpp-python is not installed.") from exc
        _llm = Llama(
            model_path=_resolve_model_path(),
            n_ctx=config.llm_ctx,
            n_threads=config.llm_threads or None,
            verbose=False,
        )
    return _llm


def chat_json(system: str, user: str, max_tokens: int = 512) -> str:
    """Run a deterministic chat completion constrained to a JSON object.

    llama.cpp enforces the JSON grammar, so the returned string always parses.
    """
    llm = get_llm()
    resp = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return resp["choices"][0]["message"]["content"]
