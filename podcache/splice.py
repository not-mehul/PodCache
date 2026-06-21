"""Audio splicing with FFmpeg.

Given a file and a list of ad ranges, cut the complement (the parts to keep) by
stream-copy and concatenate them — no re-encode, so it is fast and lossless.
Descriptive tags and cover art are restored afterwards; chapter frames are
dropped because their timestamps no longer line up once segments are removed.

FFmpeg is optional: if it isn't on PATH, `cut()` reports that nothing was done
and the original file is left untouched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def media_duration(path: Path) -> float:
    """Return the media duration in seconds via ffprobe (0.0 if unavailable)."""
    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def keep_ranges(
    ads: list[tuple[float, float]], duration: float
) -> list[tuple[float, float | None]]:
    """Complement of the ad ranges over [0, duration].

    When `duration` is unknown (0), the final kept range runs to end-of-file,
    represented as an open end (None).
    """
    keep: list[tuple[float, float | None]] = []
    cursor = 0.0
    for start, end in sorted(ads):
        start = max(0.0, start)
        if start - cursor > 0.05:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if duration <= 0:
        keep.append((cursor, None))
    elif duration - cursor > 0.05:
        keep.append((cursor, duration))
    return keep


def _restore_tags(src: Path, dst: Path) -> None:
    """Copy descriptive ID3 tags (incl. cover art) from src onto dst, but drop
    chapter frames whose timestamps are now invalid."""
    if dst.suffix.lower() != ".mp3":
        return
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
    except Exception:
        return
    try:
        src_tags = ID3(src)
    except (ID3NoHeaderError, Exception):
        return
    try:
        dst_tags = ID3(dst)
    except ID3NoHeaderError:
        dst_tags = ID3()
    except Exception:
        return
    for key in list(src_tags.keys()):
        if key.startswith("CHAP") or key.startswith("CTOC"):
            continue
        dst_tags[key] = src_tags[key]
    try:
        dst_tags.save(dst)
    except Exception:
        pass


def extract_clip(src: Path, start: float, end: float, out: Path) -> bool:
    """Write the [start, end] slice of `src` to `out` (an .mp3 preview clip).

    Tries a fast stream copy first; falls back to re-encoding to MP3 so the clip
    is always produced even when the source codec can't be copied into MP3.
    """
    if not ffmpeg_available():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.2, end - max(0.0, start))
    base = ["ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{max(0.0, start):.3f}", "-i", str(src), "-t", f"{dur:.3f}", "-vn"]
    for codec in (["-c", "copy"], ["-c:a", "libmp3lame", "-q:a", "5"]):
        try:
            subprocess.run(base + codec + [str(out)], check=True)
            if out.exists() and out.stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def cut(audio_path: Path, ads: list[tuple[float, float]]) -> tuple[bool, float]:
    """Remove `ads` from `audio_path` in place. Returns (did_cut, seconds_removed)."""
    audio_path = Path(audio_path)
    if not ads or not ffmpeg_available():
        return (False, 0.0)

    duration = media_duration(audio_path)
    removed = sum(max(0.0, e - s) for s, e in ads)

    # Safety: never cut nearly the whole file (a sign the heuristic misfired).
    if duration > 0 and removed > 0.8 * duration:
        return (False, 0.0)

    keep = keep_ranges(ads, duration)
    if not keep:
        return (False, 0.0)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            parts: list[Path] = []
            for idx, (start, end) in enumerate(keep):
                part = tmpdir / f"part_{idx:04d}{audio_path.suffix}"
                # Input seeking (-ss before -i) + duration is reliable with
                # stream copy. -vn drops any embedded cover-art *video* stream,
                # which otherwise gets no packets in a slice and fails the mux
                # ("at least one of its streams received no packets"). The cover
                # art is restored from the tags afterwards.
                cmd = ["ffmpeg", "-y", "-loglevel", "error",
                       "-ss", f"{start:.3f}", "-i", str(audio_path)]
                if end is not None:
                    dur = end - start
                    if dur <= 0.03:
                        continue
                    cmd += ["-t", f"{dur:.3f}"]
                cmd += ["-vn", "-c", "copy", str(part)]
                subprocess.run(cmd, check=True)
                if part.exists() and part.stat().st_size > 0:
                    parts.append(part)

            if not parts:
                return (False, 0.0)

            listfile = tmpdir / "concat.txt"
            listfile.write_text(
                "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
            )
            out_tmp = audio_path.with_suffix(audio_path.suffix + ".cut")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(listfile), "-vn", "-c", "copy", str(out_tmp)],
                check=True,
            )
        _restore_tags(audio_path, out_tmp)
        out_tmp.replace(audio_path)
        return (True, removed)
    except Exception:
        # Leave the original intact on any failure.
        try:
            stray = audio_path.with_suffix(audio_path.suffix + ".cut")
            if stray.exists():
                stray.unlink()
        except Exception:
            pass
        return (False, 0.0)
