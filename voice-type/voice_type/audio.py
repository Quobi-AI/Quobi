"""Mic capture with rolling chunk emission for parallel transcription."""
from __future__ import annotations

import io
import threading
import time
import wave
from typing import Callable, Iterable

import numpy as np
import sounddevice as sd

from .log import log


class Recorder:
    """Hold-to-talk audio capture.

    Emits ~chunk_sec WAV blobs via on_chunk during recording so transcription
    can run in parallel. The final tail (< chunk_sec) is emitted on stop().
    Also tracks a running mean amplitude so the pipeline can detect a muted
    mic, and (optionally) streams normalized mic level to on_level for the
    floating overlay's level bar.
    """

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        chunk_sec: float,
        on_chunk: Callable[[int, bytes], None],
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_frames = max(1, int(sample_rate * chunk_sec))
        self._on_chunk = on_chunk
        self._on_level = on_level
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._buf: list[np.ndarray] = []
        self._buf_frames = 0
        self._chunk_seq = 0
        self._started_at = 0.0
        self._recording = False
        self._energy_sum = 0.0
        self._energy_n = 0
        # Throttle on_level to ~30 Hz so the GUI thread doesn't get flooded.
        self._last_level_at = 0.0

    def is_recording(self) -> bool:
        return self._recording

    def mean_amplitude(self) -> float:
        n = max(1, self._energy_n)
        return self._energy_sum / n

    def start(self) -> bool:
        with self._lock:
            if self._recording:
                return False
            self._buf = []
            self._buf_frames = 0
            self._chunk_seq = 0
            self._energy_sum = 0.0
            self._energy_n = 0
            self._started_at = time.monotonic()
            try:
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    dtype="int16",
                    callback=self._callback,
                )
                self._stream.start()
                self._recording = True
                return True
            except Exception as e:  # noqa: BLE001
                log().error("audio start failed: %s", e)
                self._stream = None
                return False

    def stop(self) -> float:
        """Close the stream, flush tail audio, return duration in seconds."""
        with self._lock:
            if not self._recording:
                return 0.0
            self._recording = False
            stream = self._stream
            self._stream = None
            tail = self._buf
            tail_frames = self._buf_frames
            self._buf = []
            self._buf_frames = 0
            seq = self._chunk_seq
            self._chunk_seq += 1
            started = self._started_at
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:  # noqa: BLE001
                log().warning("audio stop error: %s", e)
        if tail_frames > 0:
            self._emit(seq, tail)
        return time.monotonic() - started

    def _callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            log().debug("audio status: %s", status)
        # Energy + level outside the inner lock — only modify recorder state
        # from the audio thread, no other writer.
        amp = float(np.abs(indata).mean())
        self._energy_sum += amp
        self._energy_n += 1
        if self._on_level is not None:
            now = time.monotonic()
            if now - self._last_level_at > 0.033:
                self._last_level_at = now
                # int16 speech is typically ~1k-5k mean-abs; normalize to 0-1.
                level = min(1.0, amp / 4000.0)
                try:
                    self._on_level(level)
                except Exception as e:  # noqa: BLE001
                    log().debug("on_level: %s", e)

        with self._lock:
            if not self._recording:
                return
            self._buf.append(indata.copy())
            self._buf_frames += indata.shape[0]
            if self._buf_frames < self._chunk_frames:
                return
            chunk = self._buf
            self._buf = []
            self._buf_frames = 0
            seq = self._chunk_seq
            self._chunk_seq += 1
        self._emit(seq, chunk)

    def _emit(self, seq: int, frames: list[np.ndarray]) -> None:
        try:
            wav = self._frames_to_wav(frames)
        except ValueError as e:
            log().warning("chunk %d encode failed: %s", seq, e)
            return
        try:
            self._on_chunk(seq, wav)
        except Exception as e:  # noqa: BLE001
            log().exception("on_chunk failed for seq=%d: %s", seq, e)

    def _frames_to_wav(self, frames: Iterable[np.ndarray]) -> bytes:
        audio = np.concatenate(list(frames), axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()
