"""On-device transcription with NVIDIA Parakeet via sherpa-onnx.

This is Quobi's local speech-to-text engine. Parakeet runs **in-process**
through sherpa-onnx's ONNX Runtime bindings: there's no separate server, no
localhost port, and no sidecar to manage. It runs on CPU (ONNX Runtime's default
provider), which keeps one identical code path on Linux and Windows with zero
GPU dependency, and is fast enough that CPU stays far under real-time for
dictation-length clips (20x+ faster than real-time even single-threaded). That
leaves the GPU entirely for the Quill cleanup model.

The model is `parakeet-tdt-0.6b-v3` (FastConformer TDT) which is multilingual:
25 languages with automatic language detection. We use k2-fsa's prebuilt
sherpa-onnx ONNX bundle (encoder/decoder/joiner + tokens.txt); sherpa-onnx loads
TDT and RNN-T the same way (model_type="nemo_transducer").

Same `.transcribe(wav_bytes) -> str` / `.stop()` interface as the optional cloud
backend, so the pipeline doesn't care which one it got.
"""
from __future__ import annotations

import io
import threading
import time
import wave
from pathlib import Path

from .log import log


class ParakeetError(Exception):
    pass


# The four files a NeMo transducer export produces. We prefer the int8-quantized
# encoder (much smaller/faster, negligible WER cost); decoder/joiner are tiny and
# usually shipped unquantized. Each entry is a list of accepted names, best first.
_ENCODER = ["encoder.int8.onnx", "encoder.onnx"]
_DECODER = ["decoder.int8.onnx", "decoder.onnx"]
_JOINER = ["joiner.int8.onnx", "joiner.onnx"]
_TOKENS = ["tokens.txt"]


def _pick(model_dir: Path, names: list[str]) -> str:
    for n in names:
        p = model_dir / n
        if p.is_file():
            return str(p)
    raise ParakeetError(
        f"Parakeet model file not found in {model_dir} (looked for {', '.join(names)}). "
        "Download the Parakeet ONNX bundle, or point [transcribe].parakeet_dir at it."
    )


def _wav_to_float(wav_bytes: bytes) -> tuple[list[float], int]:
    """Decode our 16 kHz mono 16-bit PCM WAV into float32 samples in [-1, 1].

    Uses numpy when available (fast), else a pure-stdlib fallback so the daemon
    never hard-depends on numpy just for STT.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        sampwidth = w.getsampwidth()
        raw = w.readframes(n)
    if sampwidth != 2:
        raise ParakeetError(f"expected 16-bit PCM WAV, got {sampwidth * 8}-bit")
    try:
        import numpy as np
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:  # average channels down to mono
            samples = samples.reshape(-1, ch).mean(axis=1)
        return samples, sr
    except ImportError:
        import array
        a = array.array("h")
        a.frombytes(raw)
        floats = [x / 32768.0 for x in a]
        if ch > 1:
            floats = [sum(floats[i:i + ch]) / ch for i in range(0, len(floats), ch)]
        return floats, sr


class ParakeetTranscriber:
    """Loads a sherpa-onnx Parakeet transducer once and reuses it. Thread-safe:
    decoding is serialized (the recognizer holds native state)."""

    def __init__(self, model_dir: str, num_threads: int = 0) -> None:
        d = Path(model_dir)
        if not d.is_dir():
            raise ParakeetError(
                f"Parakeet model dir not found: {model_dir}. Download the ONNX "
                "bundle, or switch [transcribe].engine to 'cloud'."
            )
        try:
            import sherpa_onnx
        except ImportError as e:
            raise ParakeetError(
                "sherpa-onnx is not installed (pip install sherpa-onnx). It is "
                "required for the Parakeet STT backend."
            ) from e

        encoder = _pick(d, _ENCODER)
        decoder = _pick(d, _DECODER)
        joiner = _pick(d, _JOINER)
        tokens = _pick(d, _TOKENS)

        # 0 = let sherpa-onnx/ORT pick a sensible thread count for this box.
        threads = num_threads if num_threads and num_threads > 0 else 4
        t0 = time.monotonic()
        try:
            self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                tokens=tokens,
                num_threads=threads,
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                model_type="nemo_transducer",
            )
        except Exception as e:  # noqa: BLE001
            raise ParakeetError(f"could not load Parakeet model: {e}") from e
        self._lock = threading.Lock()
        log().info(
            "Parakeet STT ready (%s, %d threads, %.0fms load)",
            Path(encoder).name, threads, (time.monotonic() - t0) * 1000,
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        if not wav_bytes:
            return ""
        samples, sr = _wav_to_float(wav_bytes)
        t0 = time.monotonic()
        with self._lock:
            stream = self._rec.create_stream()
            stream.accept_waveform(sr, samples)
            self._rec.decode_stream(stream)
            text = stream.result.text or ""
        # Parakeet emits lower-case text with no leading/trailing space; collapse
        # any internal whitespace runs for a clean single dictation string.
        text = " ".join(text.split()).strip()
        log().debug("parakeet %.0fms -> %dch", (time.monotonic() - t0) * 1000, len(text))
        return text

    def stop(self) -> None:
        """No sidecar to tear down — the recognizer is freed with this object."""
