"""Whisper transcription client (OpenAI-compatible). Thread-safe; one instance
reusable across chunks. Works against any provider exposing the OpenAI
/audio/transcriptions endpoint (Groq, Together, Fireworks, DeepInfra, ...)."""
from __future__ import annotations

import time

import requests

from .log import log

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


class TranscriptionError(Exception):
    pass


class WhisperClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        language: str = "",
        prompt: str = "",
        timeout_sec: int = 30,
        temperature: float = 0.0,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._language = language
        self._prompt = prompt
        self._timeout = timeout_sec
        self._temperature = temperature
        # requests.Session reuses TCP connections — meaningful win when
        # several chunks fire in parallel.
        self._session = requests.Session()

    def transcribe(self, wav_bytes: bytes) -> str:
        data = {
            "model": self._model,
            "response_format": "json",
            "temperature": str(self._temperature),
        }
        if self._language:
            data["language"] = self._language
        if self._prompt:
            data["prompt"] = self._prompt

        t0 = time.monotonic()
        try:
            resp = self._session.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data=data,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            raise TranscriptionError(f"network: {e}") from e

        if not resp.ok:
            body = resp.text[:300].replace("\n", " ")
            raise TranscriptionError(f"groq {resp.status_code}: {body}")
        try:
            text = (resp.json().get("text") or "").strip()
        except ValueError as e:
            raise TranscriptionError(f"parse: {e}") from e
        log().debug("whisper %.0fms %dB -> %dch", (time.monotonic() - t0) * 1000, len(wav_bytes), len(text))
        return text

    def stop(self) -> None:
        """No-op: the cloud client owns no sidecar. Present so callers can
        stop() whatever backend they got, uniformly."""


def make_transcriber(t_cfg, api_key: str):
    """Build the transcription backend from config. Every backend exposes
    `.transcribe(wav_bytes) -> str` and `.stop()`.

    Local STT (engine='local') runs NVIDIA Parakeet via sherpa-onnx, in-process
    on the CPU. The multilingual parakeet-tdt-0.6b-v3 (25 languages, auto language
    detection) is 20x+ faster than real-time even single-threaded, so STT never
    needs the GPU on any hardware; the GPU stays free for cleanup. If Parakeet
    can't load and an API key is set, cloud transcription is the last resort.
    Cloud (OpenAI-compatible) is also used directly when engine='cloud'.
    """
    engine = getattr(t_cfg, "engine", "local")
    if engine == "local":
        pdir = getattr(t_cfg, "parakeet_dir", "") or ""
        try:
            if not pdir:
                raise ValueError("[transcribe].parakeet_dir is not set")
            from .transcribe_parakeet import ParakeetTranscriber
            return ParakeetTranscriber(
                model_dir=pdir,
                num_threads=getattr(t_cfg, "parakeet_threads", 0),
            )
        except Exception as e:  # noqa: BLE001
            log().error("Parakeet transcription unavailable (%s)", e)
            if not api_key:
                raise
            log().warning("falling back to cloud transcription")
    # cloud
    if not api_key:
        raise ValueError("cloud transcription needs an API key")
    return WhisperClient(
        api_key=api_key,
        model=t_cfg.model,
        base_url=t_cfg.base_url,
        language=t_cfg.language,
        prompt=t_cfg.prompt,
        timeout_sec=t_cfg.timeout_sec,
        temperature=t_cfg.temperature,
    )
