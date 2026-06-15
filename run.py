#!/usr/bin/env python3
"""Launch the PodCache web app.

    python run.py

Then open http://127.0.0.1:8000 in a browser.
"""

from __future__ import annotations

import os
import sys

# Make the package importable no matter where the script is launched from
# (double-click, IDE run button, a different working directory, etc.).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from podcache.config import config


def main() -> None:
    print(f"PodCache → http://{config.host}:{config.port}")
    print(f"  search:        {config.search_provider}")
    print(f"  ad detection:  {config.detector}")
    print(f"  transcription: faster-whisper ({config.whisper_model})")
    uvicorn.run("podcache.server:app", host=config.host, port=config.port)


if __name__ == "__main__":
    main()
