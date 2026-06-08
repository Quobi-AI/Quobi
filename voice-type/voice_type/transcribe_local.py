"""On-device transcription via faster-whisper (CTranslate2). No network, no
per-minute cost, audio never leaves the machine. Same .transcribe(wav_bytes)
interface as the cloud WhisperClient so the pipeline doesn't care which it gets.

We feed raw PCM (numpy float32) straight in, so faster-whisper needs no ffmpeg
/ PyAV to decode — our recorder already produces 16 kHz mono int16 WAV.
"""
from __future__ import annotations

import io
import threading
import time
import wave

import numpy as np

from .log import log


class LocalTranscribeError(Exception):
    pass


class LocalWhisper:
    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "",
    ) -> None:
        try:
            from faster_whisper import WhisperModel  # heavy; import lazily
        except ImportError as e:
            raise LocalTranscribeError(
                "faster-whisper not installed (pip install faster-whisper)"
            ) from e
        t0 = time.monotonic()
        # Loads from the HF cache; downloads ~base 150MB / small 500MB on first
        # run, then offline forever.
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._language = language or None
        # CTranslate2 models aren't guaranteed concurrency-safe; serialize.
        self._lock = threading.Lock()
        log().info(
            "local whisper loaded: model=%s device=%s compute=%s (%.1fs)",
            model_size, device, compute_type, time.monotonic() - t0,
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        # decode our known WAV (16k mono int16) → float32 [-1, 1], no ffmpeg
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                frames = wf.readframes(wf.getnframes())
        except (wave.Error, EOFError) as e:
            raise LocalTranscribeError(f"bad wav: {e}") from e
        if not frames:
            return ""
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        t0 = time.monotonic()
        with self._lock:
            segments, _info = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=1,          # greedy — fastest, fine for dictation
                vad_filter=False,
            )
            text = " ".join(seg.text for seg in segments).strip()
        log().debug("local whisper %.0fms -> %dch", (time.monotonic() - t0) * 1000, len(text))
        return text

    def stop(self) -> None:
        """No-op: in-process model, no sidecar to tear down. Present so callers
        can stop() whatever backend they got, uniformly."""
