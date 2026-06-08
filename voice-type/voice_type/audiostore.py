"""Persist per-dictation audio so failed transcriptions can be retried.

The pipeline transcribes in parallel chunks; this module combines those chunk
WAVs into one file per dictation, saves it under the state dir, and prunes to
the last N so disk usage stays bounded. Audio never leaves the machine except
to Groq for (re)transcription — same trust boundary as everything else.
"""
from __future__ import annotations

import io
import os
import threading
import time
import wave
from pathlib import Path

from .log import log


def combine_wavs(chunks: list[bytes]) -> bytes:
    """Concatenate several 16k/mono/PCM-16 WAV blobs into one WAV.

    Each chunk carries its own RIFF header; we strip those and re-emit one
    container around the concatenated PCM. Assumes all chunks share format
    (they do — the recorder produces them identically)."""
    pcm = bytearray()
    params = None
    for blob in chunks:
        if not blob:
            continue
        with wave.open(io.BytesIO(blob), "rb") as wf:
            if params is None:
                params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
            pcm += wf.readframes(wf.getnframes())
    if params is None:
        return b""
    ch, width, rate = params
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(bytes(pcm))
    return out.getvalue()


class AudioStore:
    def __init__(self, directory: Path, max_files: int = 50) -> None:
        self._dir = directory
        self._max = max(5, max_files)
        self._lock = threading.Lock()

    def save(self, dictation_id: str, wav_bytes: bytes) -> str | None:
        """Write a WAV for this dictation, prune old ones, return its path
        (str) or None on failure."""
        if not wav_bytes:
            return None
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                path = self._dir / f"{dictation_id}.wav"
                path.write_bytes(wav_bytes)
                self._prune()
            return str(path)
        except OSError as e:
            log().debug("audio save failed: %s", e)
            return None

    def load(self, path: str) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except OSError as e:
            log().debug("audio load failed: %s", e)
            return None

    def _prune(self) -> None:
        try:
            wavs = sorted(
                self._dir.glob("*.wav"), key=lambda p: p.stat().st_mtime
            )
        except OSError:
            return
        for old in wavs[:-self._max]:
            try:
                old.unlink()
            except OSError:
                pass


def new_dictation_id() -> str:
    # Sortable, unique-enough for a single user: epoch ms.
    return f"{int(time.time() * 1000)}"
