"""Transcription via faster-whisper (local, runs on CPU or GPU).

Produces a timestamped transcript: a list of sentences, each with an exact
`start` and `end` in seconds. Those timestamps are what the ad detector reasons
over and what the splicer ultimately cuts on.

GPU notes: `device=auto` uses CUDA when CTranslate2 can find it. The CUDA 12
runtime (cuBLAS + cuDNN) ships separately from the NVIDIA driver; the easiest way
to provide it is the pip wheels `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`. On
Windows those DLLs land in site-packages but aren't on the search path, so we
register their directories here. If CUDA still can't load, we fall back to CPU.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import config

_model = None  # lazily-loaded singleton; the model is expensive to construct
_model_device: str | None = None
_cuda_registered = False


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _register_cuda_dlls() -> None:
    """Make the NVIDIA pip-wheel CUDA libraries findable (Windows).

    `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12`
    drops the CUDA 12 runtime under site-packages/nvidia/*/bin, but those
    directories aren't on any search path. CTranslate2 resolves its CUDA
    dependencies via PATH (it does not honour os.add_dll_directory), so we add
    each directory to *both* the DLL search and PATH.
    """
    global _cuda_registered
    if _cuda_registered or sys.platform != "win32":
        return
    _cuda_registered = True

    import glob
    import site

    try:
        roots = list(site.getsitepackages())
        user = site.getusersitepackages()
        if user:
            roots.append(user)
    except Exception:
        roots = [p for p in sys.path if p.endswith("site-packages")]

    bindirs: set[str] = set()
    for root in roots:
        for dll in glob.glob(os.path.join(root, "nvidia", "**", "*.dll"), recursive=True):
            bindirs.add(os.path.dirname(dll))

    path = os.environ.get("PATH", "")
    for d in sorted(bindirs):
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
        if d not in path:
            path = d + os.pathsep + path
    os.environ["PATH"] = path


def _build_model(device: str, compute_type: str):
    _register_cuda_dlls()
    from faster_whisper import WhisperModel  # imported lazily

    return WhisperModel(config.whisper_model, device=device, compute_type=compute_type)


def _get_model():
    global _model, _model_device
    if _model is None:
        _model = _build_model(config.whisper_device, config.whisper_compute)
        _model_device = config.whisper_device
    return _model


def _is_gpu_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(tok in msg for tok in ("cublas", "cuda", "cudnn", "gpu", "cudart"))


def _run(model, audio_path: Path, on_progress: Callable[[float], None] | None) -> list[Segment]:
    segments_iter, info = model.transcribe(str(audio_path), vad_filter=True)
    duration = info.duration or 0
    out: list[Segment] = []
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            out.append(Segment(start=seg.start, end=seg.end, text=text))
        if on_progress and duration:
            on_progress(min(seg.end / duration, 1.0))
    if on_progress:
        on_progress(1.0)
    return out


def transcribe(
    audio_path: Path,
    on_progress: Callable[[float], None] | None = None,
) -> list[Segment]:
    """Transcribe `audio_path` into timestamped segments.

    Tries the configured device first; if that device's GPU libraries can't be
    loaded, it rebuilds the model on CPU and retries once.
    """
    global _model, _model_device
    try:
        return _run(_get_model(), audio_path, on_progress)
    except RuntimeError as exc:
        # GPU libraries missing/unloadable — rebuild on CPU and try again.
        if _is_gpu_error(exc) and (_model_device or config.whisper_device).lower() != "cpu":
            print(
                f"[PodCache] GPU transcription unavailable ({exc}). Falling back "
                "to CPU. Install nvidia-cublas-cu12 + nvidia-cudnn-cu12 to use the "
                "GPU. See the README.",
                file=sys.stderr,
                flush=True,
            )
            _model = _build_model("cpu", "int8")
            _model_device = "cpu"
            return _run(_model, audio_path, on_progress)
        raise
