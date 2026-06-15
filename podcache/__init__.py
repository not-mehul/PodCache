"""PodCache — search podcasts, transcribe episodes, and splice out the ads.

The pipeline is deliberately text-first: rather than guessing at volume drops or
jingles, PodCache transcribes the audio, has a language model locate sponsor
reads in the transcript, and cuts the audio on those timestamps.
"""

__version__ = "1.0.0"
