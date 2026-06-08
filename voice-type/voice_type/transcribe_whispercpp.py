"""Transcription client for the whisper.cpp `whisper-server` sidecar.

Same `.transcribe(wav_bytes) -> str` interface as the cloud WhisperClient and
the faster-whisper LocalWhisper, so the pipeline doesn't care which backend it
got. POSTs our 16 kHz mono WAV to the server's /inference endpoint and returns
the transcript. The server (Vulkan GGML) does the GPU work; this is just the
thin HTTP shim.

The server is a managed sidecar (LocalWhisperServer) owned by this client: it
is started on construction-by-the-caller and stopped via .stop() at shutdown.
"""
from __future__ import annotations

import threading
import time

import requests

from .log import log


class WhisperCppError(Exception):
    pass


class WhisperCppClient:
    def __init__(self, server, language: str = "", timeout_sec: int = 120) -> None:
        # `server` is a started LocalWhisperServer (or None for an externally
        # managed endpoint, in which case set `inference_url` directly).
        self._server = server
        self._url = server.inference_url if server is not None else ""
        self._language = language or "auto"
        self._timeout = timeout_sec
        self._session = requests.Session()
        # whisper-server processes one request at a time; serialize.
        self._lock = threading.Lock()

    def transcribe(self, wav_bytes: bytes) -> str:
        if not wav_bytes:
            return ""
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {
            "response_format": "json",
            "temperature": "0",
            "language": self._language,
        }
        t0 = time.monotonic()
        try:
            with self._lock:
                resp = self._session.post(self._url, files=files, data=data, timeout=self._timeout)
        except requests.RequestException as e:
            raise WhisperCppError(f"whisper-server network: {e}") from e
        if not resp.ok:
            raise WhisperCppError(f"whisper-server {resp.status_code}: {resp.text[:200]}")
        try:
            text = (resp.json().get("text") or "")
        except ValueError as e:
            raise WhisperCppError(f"whisper-server parse: {e}") from e
        # The server splits the transcript across segments with newlines; for
        # dictation we want one flowing string. Collapse runs of whitespace.
        text = " ".join(text.split()).strip()
        log().debug("whispercpp %.0fms -> %dch", (time.monotonic() - t0) * 1000, len(text))
        return text

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()
