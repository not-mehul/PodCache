"""Transcription via faster-whisper (local, runs on CPU or GPU).

Produces a timestamped transcript: a list of sentences, each with an exact
`start` and `end` in seconds. Those timestamps are what the ad detector reasons
over and what the splicer ultimately cuts on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import config

_model = None  # lazily-loaded singleton; the model is expensive to construct


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # imported lazily

        _model = WhisperModel(
            config.whisper_model,
            device=config.whisper_device,
            compute_type=config.whisper_compute,
        )
    return _model


def transcribe(
    audio_path: Path,
    on_progress: Callable[[float], None] | None = None,
) -> list[Segment]:
    """Transcribe `audio_path` into timestamped segments.

    faster-whisper yields segments as it decodes; we translate each segment's
    end time against the known media duration into a 0..1 progress fraction.
    """
    model = _get_model()
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
