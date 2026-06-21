"""Speech-to-text: NVIDIA Parakeet via sherpa-onnx, in-process on the CPU.

Fully on-device — no network, no API key, no sidecar, no port, no CUDA;
identical on Linux and Windows. make_transcriber() builds the recognizer from
config. Every backend exposes `.transcribe(wav_bytes) -> str` and `.stop()`."""
from __future__ import annotations

from .log import log


class TranscriptionError(Exception):
    pass


def make_transcriber(t_cfg):
    """Build the on-device Parakeet transcriber from config.

    Parakeet runs in-process via sherpa-onnx on the CPU — 20x+ faster than
    real-time even single-threaded, so speech never needs the GPU on any
    hardware and the GPU stays free for cleanup. The GUI provisions the ONNX
    bundle (english v2 or multilingual v3) on first run and points
    `parakeet_dir` at it. Raises if the bundle can't be loaded — there is no
    cloud fallback."""
    pdir = getattr(t_cfg, "parakeet_dir", "") or ""
    if not pdir:
        raise ValueError("[transcribe].parakeet_dir is not set")
    from .transcribe_parakeet import ParakeetTranscriber
    log().info("transcribe: parakeet bundle %s", pdir)
    return ParakeetTranscriber(
        model_dir=pdir,
        num_threads=getattr(t_cfg, "parakeet_threads", 0),
    )
