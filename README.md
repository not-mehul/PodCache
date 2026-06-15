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
4. **Ad detection** — feed the timestamped transcript to a small **local**
   language model (no cloud, no API), which returns the segments that are
   sponsor reads.
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
| Ad detection | small local GGUF model via [`llama-cpp-python`](https://github.com/abetlen/llama-cpp-python) · offline heuristic fallback |
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

# 3. (Optional) configure for the best results
cp .env.example .env
# edit .env — point PODCACHE_LLM_PATH at a .gguf you already have (fully
# offline), or leave the defaults to fetch a small model once. PodcastIndex
# keys give the richest search. All optional.
```

Everything runs **locally** — there are no cloud APIs or keys in the detection
path. The first run fetches the small ad-detection model once into `models/`
(after which inference is entirely on-device); supply your own `.gguf` via
`PODCACHE_LLM_PATH` for a fully air-gapped setup. If `llama-cpp-python` isn't
installed, ad detection falls back to an offline phrase detector. Search falls
back to the keyless iTunes Search API when no PodcastIndex keys are set.

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
| `PODCACHE_LLM_PATH` | — | Path to a local `.gguf` (fully offline) |
| `PODCACHE_LLM_REPO` / `_FILE` | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` | Model fetched once if no path is set |
| `PODCACHE_LLM_CTX` / `_THREADS` | `4096` / `auto` | Context window · CPU threads |
| `PODCASTINDEX_API_KEY` / `_SECRET` | — | PodcastIndex search |
| `PODCACHE_WHISPER_MODEL` | `base` | Whisper size (`tiny`…`large-v3`) |
| `PODCACHE_WHISPER_COMPUTE` | `int8` | `int8` (CPU) / `float16` (GPU) |
| `PODCACHE_WHISPER_DEVICE` | `auto` | `cpu` / `cuda` / `auto` |
| `PODCACHE_DATA_DIR` | `data` | Where files are written |
| `PODCACHE_HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |

## Notes

- **Fully local.** The server binds to `127.0.0.1`, all files stay in a local
  folder, and both transcription and ad detection run on-device — nothing about
  the audio or its transcript is sent to any external service.
- The first run downloads the Whisper and ad-detection model weights once;
  subsequent runs reuse the cache.
