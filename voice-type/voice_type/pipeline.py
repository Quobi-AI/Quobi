"""Hotkey -> chunked record -> parallel transcribe -> LLM cleanup -> output."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .audio import Recorder
from .audiostore import AudioStore, combine_wavs, new_dictation_id
from .config import Config
from .format import FormatError, Formatter
from .history import History
from .indicator import Indicator, State
from .log import log
from .output import OutputBackend
from .transcribe import TranscriptionError, WhisperClient

# Phrases recognized as "erase the last paste." Detection runs *after* LLM
# cleanup, so we allow trailing punctuation the model commonly adds. We
# only match at the start of the utterance — mid-sentence "scratch that"
# is left to the LLM to interpret as ordinary speech.
from ._shared import scratch_phrases as _shared_scratch_phrases
from ._shared import whisper_hallucinations as _shared_hallucinations
from .symbols import normalize_symbols

# Source of truth: shared/scratch-phrases.json and shared/hallucinations.json
SCRATCH_PHRASES = _shared_scratch_phrases()
_WHISPER_HALLUCINATIONS = _shared_hallucinations()


def _is_whisper_hallucination(text: str) -> bool:
    """Common Whisper outputs on silent/near-silent audio that should be
    suppressed rather than typed."""
    norm = text.strip().lower()
    if norm in _WHISPER_HALLUCINATIONS:
        return True
    stripped = norm.rstrip(".!?,").strip()
    return bool(stripped) and stripped in _WHISPER_HALLUCINATIONS


def parse_corrections(text: str) -> list[tuple[str, list[str]]]:
    """Parse the corrections config into (target, [variants]) rules.

    Format: one rule per line, "Target: variant1, variant2, ...".
    e.g.  "Rabih: Robbie, Robby, Rabia, Rabiah"
    """
    rules: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        target, rest = line.split(":", 1)
        target = target.strip()
        variants = [v.strip() for v in rest.split(",") if v.strip()]
        if target and variants:
            rules.append((target, variants))
    return rules


def apply_corrections(text: str, rules: list[tuple[str, list[str]]]) -> str:
    """Replace each variant (case-insensitive, whole-word, multi-word aware)
    with its target spelling. Deterministic — this is what reliably fixes
    mis-heard names like Robbie/Rabia -> Rabih."""
    for target, variants in rules:
        # longest first so multi-word variants win over their sub-words
        for v in sorted(variants, key=len, reverse=True):
            parts = [re.escape(w) for w in v.split()]
            pattern = r"\b" + r"\s+".join(parts) + r"\b"
            text = re.sub(pattern, target, text, flags=re.IGNORECASE)
    return text


def _is_mid_sentence(context: str | None) -> bool:
    """Decide if cleaned-text first letter should be lowercased.

    Rules (in priority order):
      - context is None (peek failed)            -> False (don't change)
      - context is empty                         -> False (at start of doc)
      - any newline in trailing whitespace       -> False (new line = sentence start)
      - last non-whitespace char is . ! ?        -> False (just ended a sentence)
      - last non-whitespace char is anything else (letter/digit/comma/etc.) -> True
    """
    if context is None or context == "":
        return False
    rstripped = context.rstrip()
    trailing_ws = context[len(rstripped):]
    if "\n" in trailing_ws or "\r" in trailing_ws:
        return False
    if not rstripped:
        return False
    last = rstripped[-1]
    if last in ".!?":
        return False
    return True


def _lowercase_first(text: str) -> str:
    if not text:
        return text
    return text[0].lower() + text[1:]


def _scratch_match(text: str) -> str | None:
    lower = text.lower().lstrip(",.!?;:- \t\n")
    for phrase in SCRATCH_PHRASES:
        if lower == phrase:
            return phrase
        if lower.startswith(phrase) and lower[len(phrase):len(phrase) + 1] in (
            "", " ", ",", ".", "!", "?", ";", ":", "-", "\t", "\n",
        ):
            return phrase
    return None


def _scratch_remainder(text: str, phrase: str) -> str:
    lower = text.lower()
    idx = lower.find(phrase)
    if idx < 0:
        return ""
    return text[idx + len(phrase):].lstrip(",.!?;:- \t\n")


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        whisper: WhisperClient,
        formatter: Formatter | None,
        output: OutputBackend,
        indicator: Indicator,
        history: History,
        audio_store: AudioStore | None = None,
    ) -> None:
        self._cfg = cfg
        self._audio_store = audio_store
        self._whisper = whisper
        self._formatter = formatter
        self._output = output
        self._indicator = indicator
        self._history = history
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="transcribe"
        )
        self._lock = threading.Lock()
        self._futures: dict[int, Future] = {}
        self._chunk_wavs: dict[int, bytes] = {}
        self._last_paste_chars = 0
        self._ticker_stop: threading.Event | None = None
        # Pre-erase is only needed when the listener can't suppress the
        # hotkey from reaching the focused window. The grab+replay evdev
        # listener handles every key cleanly; pynput on X11 with a
        # self-typing key does leak one char.
        self._pre_erase_chars = 0  # set by set_listener_pre_erase()
        self._corrections = parse_corrections(cfg.personalize.corrections)
        if self._corrections:
            log().info("loaded %d correction rule(s)", len(self._corrections))
        self._recorder = Recorder(
            sample_rate=cfg.audio.sample_rate,
            channels=cfg.audio.channels,
            chunk_sec=cfg.audio.chunk_sec,
            on_chunk=self._on_chunk,
            on_level=self._indicator.set_mic_level,
        )

    # ---- hotkey hooks --------------------------------------------------

    def on_press(self) -> None:
        if self._cfg.hotkey.mode == "toggle":
            if self._recorder.is_recording():
                self._stop_and_finalize()
            else:
                self._start_recording()
        else:
            self._start_recording()

    def on_release(self) -> None:
        if self._cfg.hotkey.mode == "toggle":
            return
        if self._recorder.is_recording():
            self._stop_and_finalize()

    def shutdown(self) -> None:
        self._stop_ticker()
        if self._recorder.is_recording():
            try:
                self._recorder.stop()
            except Exception as e:  # noqa: BLE001
                log().debug("recorder shutdown: %s", e)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def set_listener_pre_erase(self, requires: bool) -> None:
        """Called by __main__ after the listener is created — sets whether
        the pipeline should send a Backspace before each output to erase the
        hotkey character that leaked into the focused window."""
        self._pre_erase_chars = 1 if requires else 0
        if self._pre_erase_chars:
            log().info("listener cannot suppress hotkey; pre-erasing 1 char before output")
        else:
            log().info("listener suppresses hotkey; no pre-erase needed")

    # ---- recording lifecycle ------------------------------------------

    def _start_recording(self) -> None:
        with self._lock:
            for f in self._futures.values():
                f.cancel()
            self._futures.clear()
            self._chunk_wavs.clear()
        if self._recorder.start():
            self._indicator.set_state(State.RECORDING, "0.0s")
            self._start_ticker()

    def _stop_and_finalize(self) -> None:
        duration = self._recorder.stop()
        self._stop_ticker()
        if duration < self._cfg.audio.min_recording_sec:
            log().info("skip too-short (%.2fs)", duration)
            with self._lock:
                for f in self._futures.values():
                    f.cancel()
                self._futures.clear()
            self._indicator.set_state(State.IDLE)
            return
        threading.Thread(
            target=self._finalize, args=(duration,), daemon=True, name="finalize"
        ).start()

    def _start_ticker(self) -> None:
        stop = threading.Event()
        self._ticker_stop = stop
        started = time.monotonic()

        def _tick() -> None:
            while not stop.wait(0.25):
                elapsed = time.monotonic() - started
                self._indicator.update_detail(State.RECORDING, f"{elapsed:.1f}s")

        threading.Thread(target=_tick, daemon=True, name="ticker").start()

    def _stop_ticker(self) -> None:
        if self._ticker_stop is not None:
            self._ticker_stop.set()
            self._ticker_stop = None

    # ---- transcription pipeline ---------------------------------------

    def _on_chunk(self, seq: int, wav_bytes: bytes) -> None:
        log().debug("chunk %d -> dispatch (%d bytes)", seq, len(wav_bytes))
        future = self._executor.submit(self._whisper.transcribe, wav_bytes)
        with self._lock:
            self._futures[seq] = future
            # Keep the raw audio so we can persist the full dictation (for
            # retry) even if transcription fails.
            self._chunk_wavs[seq] = wav_bytes

    def _save_audio(self, dictation_id: str) -> str:
        """Combine accumulated chunk WAVs and persist them. Returns the path
        or '' if no store / nothing captured."""
        if self._audio_store is None:
            return ""
        with self._lock:
            chunks = [self._chunk_wavs[s] for s in sorted(self._chunk_wavs)]
        if not chunks:
            return ""
        try:
            combined = combine_wavs(chunks)
        except Exception as e:  # noqa: BLE001
            log().debug("combine_wavs failed: %s", e)
            return ""
        return self._audio_store.save(dictation_id, combined) or ""

    def _collect(self) -> tuple[list[str], list[str]]:
        with self._lock:
            pending = dict(self._futures)
            self._futures.clear()
        parts: list[str] = []
        errors: list[str] = []
        timeout = self._cfg.transcribe.timeout_sec + 5
        for seq in sorted(pending):
            try:
                parts.append(pending[seq].result(timeout=timeout))
            except TranscriptionError as e:
                log().error("chunk %d failed: %s", seq, e)
                errors.append(str(e))
            except Exception as e:  # noqa: BLE001
                log().exception("chunk %d unexpected: %s", seq, e)
                errors.append(str(e))
        return parts, errors

    def _finalize(self, duration: float) -> None:
        t0 = time.monotonic()
        self._indicator.set_state(State.TRANSCRIBING)
        dictation_id = new_dictation_id()

        parts, errors = self._collect()
        raw = " ".join(p for p in parts if p).strip()
        # Persist the audio now — before any early-return — so a failed or
        # empty transcription still has audio for the retry button.
        audio_path = self._save_audio(dictation_id)
        log().info(
            "transcribe %.2fs duration=%.2fs raw=%dch errors=%d",
            time.monotonic() - t0, duration, len(raw), len(errors),
        )

        # Whisper hallucinations: short low-amplitude clips often produce
        # "Thank you" / "thanks for watching" / etc. Drop them before they
        # waste the cleanup pass and land in the user's clipboard.
        if raw and duration < 2.5:
            mean_amp = self._recorder.mean_amplitude()
            if mean_amp < 300 and _is_whisper_hallucination(raw):
                log().info(
                    "suppressed Whisper hallucination: raw=%r duration=%.2fs amp=%.1f",
                    raw, duration, mean_amp,
                )
                self._indicator.set_state(State.IDLE)
                return

        if not raw:
            mean_amp = self._recorder.mean_amplitude()
            if errors:
                msg = errors[0]
                self._indicator.set_state(State.ERROR)
                # Record the failure with its audio so the user can retry
                # from the history GUI once their network is back.
                self._history.append(
                    "", "", duration, status="failed",
                    audio=audio_path, error=msg, dictation_id=dictation_id,
                )
                if self._cfg.indicator.notify_on_error:
                    self._indicator.notify(
                        "voice-type", f"Transcription failed: {msg}", urgent=True
                    )
            elif mean_amp < 80.0:
                log().warning("mic appears muted (mean amplitude=%.1f)", mean_amp)
                self._indicator.notify(
                    "voice-type",
                    "No audio detected — check your microphone (is it muted?)",
                    urgent=True,
                )
            else:
                log().info("skip empty-transcript (amp=%.1f)", mean_amp)
            self._indicator.set_state(State.IDLE)
            return

        if errors and self._cfg.indicator.notify_on_error:
            self._indicator.notify(
                "voice-type",
                f"{len(errors)} chunk(s) failed — transcript may be partial",
            )

        # LLM cleanup ----------------------------------------------------
        cleaned = raw
        if self._formatter is not None and self._cfg.cleanup.enabled:
            self._indicator.set_state(State.FORMATTING)
            t1 = time.monotonic()
            try:
                polished = self._formatter.clean(raw)
                cleaned = polished or raw
            except FormatError as e:
                log().warning("cleanup failed (%s); typing raw transcript", e)
                cleaned = raw
            log().info("cleanup %.2fs -> %dch", time.monotonic() - t1, len(cleaned))

        # Deterministic spoken-symbol normalization — fill in @ / . / newlines
        # that the cleanup model may have left literal (model-independent).
        normalized = normalize_symbols(cleaned)
        if normalized != cleaned:
            log().debug("symbols normalized")
            cleaned = normalized

        # Deterministic corrections — fix mis-heard names/terms (Robbie -> Rabih)
        # regardless of what Whisper heard or the cleanup model preserved.
        if self._corrections:
            corrected = apply_corrections(cleaned, self._corrections)
            if corrected != cleaned:
                log().debug("corrections applied")
                cleaned = corrected

        if not cleaned.strip():
            self._indicator.set_state(State.IDLE)
            return

        # Single sleep before any synthesized input — covers pre-erase,
        # scratch erase, and the new paste.
        time.sleep(self._cfg.output.release_delay_sec)

        # Pre-erase the character that leaked when a self-typing push-to-talk
        # key (e.g. backtick) was first pressed.
        if self._pre_erase_chars:
            try:
                self._output.erase(self._pre_erase_chars)
            except Exception as e:  # noqa: BLE001
                log().debug("pre-erase failed: %s", e)

        # Scratch-that: erase last paste, optionally replace with remainder.
        if self._cfg.voice_commands.scratch_enabled and self._last_paste_chars > 0:
            phrase = _scratch_match(cleaned)
            if phrase:
                n = self._last_paste_chars
                self._last_paste_chars = 0
                try:
                    self._output.erase(n)
                    log().info("scratch: erased %d chars", n)
                    self._history.append(raw, "", duration, kind="scratch")
                except Exception as e:  # noqa: BLE001
                    log().warning("erase failed: %s", e)
                remainder = _scratch_remainder(cleaned, phrase)
                if not remainder.strip():
                    if self._cfg.indicator.notify_on_success:
                        self._indicator.notify("voice-type", f"Scratched {n} chars")
                    self._indicator.set_state(State.IDLE)
                    return
                cleaned = remainder

        # Context-aware capitalization ----------------------------------
        # Peek the few chars before the cursor and decide whether to
        # lowercase the first letter (we're mid-sentence) or leave Llama's
        # capitalization (we're at sentence start / line start / doc start).
        if (
            self._cfg.output.smart_capitalize
            and self._cfg.output.mode == "paste"
            and hasattr(self._output, "peek_context")
        ):
            try:
                ctx = self._output.peek_context()
                if _is_mid_sentence(ctx):
                    cleaned = _lowercase_first(cleaned)
                    log().debug("smart-cap: lowered first letter (ctx=%r)", ctx)
                else:
                    log().debug("smart-cap: kept caps (ctx=%r)", ctx)
            except Exception as e:  # noqa: BLE001
                log().debug("smart-cap peek raised: %s", e)

        # Type / paste / clipboard --------------------------------------
        mode = self._cfg.output.mode
        try:
            if mode == "type":
                self._output.type_text(cleaned)
                self._last_paste_chars = len(cleaned)
                summary = f"Typed {len(cleaned)} chars"
            elif mode == "clipboard":
                # Clipboard-only: drop text in the clipboard for the user to
                # paste later. We do NOT track this in _last_paste_chars
                # because we never inserted into a focused window — scratch
                # has nothing to undo.
                self._output.set_clipboard(cleaned)
                summary = f"{len(cleaned)} chars copied to clipboard"
            else:  # "paste"
                self._output.paste_text(
                    cleaned,
                    self._cfg.output.clipboard_restore_delay_sec,
                    keep_clipboard=self._cfg.output.preserve_clipboard,
                )
                self._last_paste_chars = len(cleaned)
                summary = f"Pasted {len(cleaned)} chars"
            self._history.append(
                raw, cleaned, duration, status="ok",
                audio=audio_path, dictation_id=dictation_id,
            )
            total = time.monotonic() - t0
            log().info("output %s %dch total=%.2fs", mode, len(cleaned), total)
            # Clipboard mode has no visible output in the focused window, so
            # always notify in that mode (otherwise the user can't tell it
            # worked). Other modes only notify if explicitly requested.
            if mode == "clipboard" or self._cfg.indicator.notify_on_success:
                self._indicator.notify(
                    "voice-type", f"{summary} in {total:.1f}s"
                )
        except Exception as e:  # noqa: BLE001
            log().exception("output failed: %s", e)
            self._indicator.set_state(State.ERROR)
            # Transcription succeeded but we couldn't insert it — save it so
            # the user can copy it from the history GUI.
            self._history.append(
                raw, cleaned, duration, status="ok",
                audio=audio_path, error=f"output failed: {e}",
                dictation_id=dictation_id,
            )
            if self._cfg.indicator.notify_on_error:
                self._indicator.notify(
                    "voice-type", f"Output failed (text saved to history): {e}",
                    urgent=True,
                )
        finally:
            self._indicator.set_state(State.IDLE)
