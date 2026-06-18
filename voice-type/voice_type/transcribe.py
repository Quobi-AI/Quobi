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

    Local order of preference (engine='local'):
      1. Parakeet (sherpa-onnx) — when [transcribe].parakeet_dir is set. Runs
         in-process on CPU (no sidecar, no port), NVIDIA Parakeet RNN-T accuracy.
         This is the default local STT backend.
      2. whisper.cpp Vulkan server — when [transcribe].local_gguf is set. GPU on
         ANY card (NVIDIA/AMD/Intel), no CUDA; CPU fallback. Kept as a fallback.
      3. faster-whisper (CTranslate2) — the legacy local path (CPU, or CUDA).
    Cloud (OpenAI-compatible) is used when engine='cloud', or as a last resort if
    a local backend fails and an API key is available.
    """
    engine = getattr(t_cfg, "engine", "local")
    if engine == "local":
        # 1) Preferred: Parakeet via sherpa-onnx, in-process (set parakeet_dir).
        pdir = getattr(t_cfg, "parakeet_dir", "") or ""
        if pdir:
            try:
                from .transcribe_parakeet import ParakeetTranscriber
                return ParakeetTranscriber(
                    model_dir=pdir,
                    num_threads=getattr(t_cfg, "parakeet_threads", 0),
                )
            except Exception as e:  # noqa: BLE001
                log().error("Parakeet transcription unavailable (%s)", e)
                log().warning("falling back to whisper.cpp / faster-whisper")

        # 2) Fallback: whisper.cpp Vulkan sidecar (set local_gguf to enable).
        gguf = getattr(t_cfg, "local_gguf", "") or ""
        if gguf:
            try:
                from .local_whisper_server import LocalWhisperServer
                from .transcribe_whispercpp import WhisperCppClient
                server = LocalWhisperServer(
                    binary=getattr(t_cfg, "local_bin", "whisper-server"),
                    model_path=gguf,
                    port=getattr(t_cfg, "local_port", 8090),
                    accel=getattr(t_cfg, "local_accel", "auto"),
                    language=t_cfg.language,
                    threads=getattr(t_cfg, "local_threads", 0),
                    vad=getattr(t_cfg, "vad", True),
                    vad_model=getattr(t_cfg, "vad_model", ""),
                )
                server.start()
                return WhisperCppClient(
                    server, language=t_cfg.language,
                    timeout_sec=max(t_cfg.timeout_sec, 120),
                )
            except Exception as e:  # noqa: BLE001
                log().error("whisper.cpp transcription unavailable (%s)", e)
                # Fall through to faster-whisper / cloud below.
                log().warning("falling back to faster-whisper")

        # 2) Legacy: faster-whisper (CTranslate2).
        try:
            from .transcribe_local import LocalWhisper
            return LocalWhisper(
                model_size=t_cfg.local_model,
                device=t_cfg.local_device,
                compute_type=t_cfg.local_compute_type,
                language=t_cfg.language,
            )
        except Exception as e:  # noqa: BLE001
            log().error("local transcription unavailable (%s)", e)
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
