"""Transcription via faster-whisper (local, runs on CPU or GPU).

Produces a timestamped transcript: a list of sentences, each with an exact
`start` and `end` in seconds. Those timestamps are what the ad detector reasons
over and what the splicer ultimately cuts on.

The default device is `auto`, which uses the GPU when CTranslate2 finds one. If
the GPU path can't load its CUDA libraries (a common Windows situation), we fall
back to CPU automatically rather than failing the job.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import config

_model = None  # lazily-loaded singleton; the model is expensive to construct
_model_device: str | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _build_model(device: str, compute_type: str):
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
            _model = _build_model("cpu", "int8")
            _model_device = "cpu"
            return _run(_model, audio_path, on_progress)
        raise
