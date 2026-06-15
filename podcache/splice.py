"""Audio splicing with FFmpeg.

Given the raw audio and the ad timestamps, we compute the *keep* segments (the
complement of the ads), extract each by stream-copy, and concatenate them back
into one seamless file — no full re-encode, so it's fast.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .detect import AdSegment


class FFmpegNotFound(RuntimeError):
    pass


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise FFmpegNotFound(
            "ffmpeg is not installed or not on PATH. Install it (e.g. "
            "`apt install ffmpeg` or `brew install ffmpeg`) and try again."
        )


def media_duration(path: Path) -> float:
    """Return the media duration in seconds via ffprobe."""
    if shutil.which("ffprobe") is None:
        return 0.0
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def _keep_ranges(ads: list[AdSegment], duration: float) -> list[tuple[float, float]]:
    """Complement of the ad ranges over [0, duration]."""
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for ad in sorted(ads, key=lambda a: a.start):
        start = max(0.0, ad.start)
        if start - cursor > 0.05:
            keep.append((cursor, start))
        cursor = max(cursor, ad.end)
    if duration - cursor > 0.05:
        keep.append((cursor, duration))
    return keep


def splice(audio_path: Path, ads: list[AdSegment], out_path: Path) -> Path:
    """Write `out_path` containing `audio_path` with the ad ranges removed."""
    _require_ffmpeg()
    duration = media_duration(audio_path)

    # Nothing to cut (or unknown duration with no ads): just copy through.
    if not ads or duration == 0.0:
        shutil.copyfile(audio_path, out_path)
        return out_path

    keep = _keep_ranges(ads, duration)
    if not keep:
        shutil.copyfile(audio_path, out_path)
        return out_path

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        parts: list[Path] = []
        for idx, (start, end) in enumerate(keep):
            part = tmpdir / f"part_{idx:04d}{audio_path.suffix}"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(audio_path),
                    "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                    "-c", "copy", str(part),
                ],
                check=True,
            )
            parts.append(part)

        # Concatenate the kept parts with the demuxer (stream copy).
        listfile = tmpdir / "concat.txt"
        listfile.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(listfile),
                "-c", "copy", str(out_path),
            ],
            check=True,
        )
    return out_path
