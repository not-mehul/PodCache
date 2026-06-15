# PodCache

> Find a podcast, pick an episode, get it back without the ads.

PodCache removes sponsor reads from podcast episodes using a **text-first**
approach. Rather than chasing unreliable signals like volume drops or jingles,
it converts the audio to text, finds the ads *in the transcript*, and cuts the
audio on those exact timestamps.

## How it works

1. **Search & discovery** — query a public podcast database, retrieve the
   show's RSS feed, and list the available episodes.
2. **Audio ingestion** — download the selected episode's media file (`.mp3` /
   `.m4a`) to a local folder.
3. **Transcription** — run the audio through Whisper to produce a transcript
   with an exact start/end timestamp for every sentence.
4. **Ad detection** — feed the timestamped transcript to a language model,
   which returns the segments that are sponsor reads.
5. **Audio splicing** — slice out the ad segments with FFmpeg and stitch the
   remaining content back together (stream copy — no full re-encode).
6. **Metadata restoration** — copy the ID3 tags and cover art from the original
   onto the finished file so it looks right in any player.

## Stack

| Concern | Tool |
| :-- | :-- |
| Search | [PodcastIndex.org](https://podcastindex.org) (free key) · keyless iTunes Search fallback |
| RSS parsing | `feedparser` |
| Download | `httpx` (chunked streaming) |
| Transcription | [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (local, CPU or GPU) |
| Ad detection | Claude (`claude-opus-4-8`) · offline heuristic fallback |
| Splicing | FFmpeg |
| Metadata | `mutagen` |
| Web server / UI | FastAPI + a single-page UI (the *Editorial Dusk & Dawn* design) |

## Setup

```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Install FFmpeg (required for splicing)
#    macOS:  brew install ffmpeg
#    Debian: sudo apt install ffmpeg

# 3. (Optional) configure keys for the best results
cp .env.example .env
# edit .env — add ANTHROPIC_API_KEY for Claude ad detection,
# and PodcastIndex keys for the richest search. Both are optional.
```

PodCache runs **out of the box with no API keys**: search falls back to the
keyless iTunes Search API, and ad detection falls back to an offline phrase
detector. Add an `ANTHROPIC_API_KEY` to use Claude for far more accurate ad
detection.

## Run

```bash
python run.py
```

Then open **http://127.0.0.1:8000**. Search a show, click it to load its
episodes, and pick one — PodCache streams live progress through each stage and
hands you a download link to the ad-free file.

## Configuration

All settings are environment variables (see `.env.example`):

| Variable | Default | Purpose |
| :-- | :-- | :-- |
| `ANTHROPIC_API_KEY` | — | Enables Claude ad detection |
| `PODCACHE_DETECT_MODEL` | `claude-opus-4-8` | Detection model (1M-token context) |
| `PODCASTINDEX_API_KEY` / `_SECRET` | — | PodcastIndex search |
| `PODCACHE_WHISPER_MODEL` | `base` | Whisper size (`tiny`…`large-v3`) |
| `PODCACHE_WHISPER_COMPUTE` | `int8` | `int8` (CPU) / `float16` (GPU) |
| `PODCACHE_WHISPER_DEVICE` | `auto` | `cpu` / `cuda` / `auto` |
| `PODCACHE_DATA_DIR` | `data` | Where files are written |
| `PODCACHE_HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |

## Notes

- **Local-first.** The server binds to `127.0.0.1` and all files stay in a
  local folder. Nothing about the audio leaves your machine except the
  transcript text sent to Claude (only when ad detection is enabled).
- The first transcription downloads the Whisper model weights; subsequent runs
  reuse the cache.
