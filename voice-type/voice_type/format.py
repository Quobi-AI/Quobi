"""LLM cleanup: strip fillers, fix punctuation, repair chunk seams.

The system prompt is composed from shared/cleanup-base.txt + a style block
(verbatim | tidy | formatted) so the Android app loads the same text. Edit
those files, not this module."""
from __future__ import annotations

import re
import time

import requests

# Reasoning models (Qwen3.5, etc.) emit a leading <think>...</think> block —
# often empty when thinking is disabled. Strip it so it never reaches output.
_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)

from ._shared import cleanup_prompt
from .log import log


class FormatError(Exception):
    pass


# Distinctive phrases from the cleanup system prompt. If any show up in the
# model's output it has leaked its instructions instead of cleaning — return
# nothing rather than paste our prompt into the user's document.
_LEAK_MARKERS = (
    "clean the dictation transcript",
    "you clean raw dictation",
    "you are not a chatbot",
    "editing style",
    "absolute rules",
    "disfluencies you may remove",
    "do not answer questions",
    "return only the cleaned",
    "<transcript>",
)


def _looks_like_prompt_leak(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _LEAK_MARKERS)


class Formatter:
    def __init__(
        self,
        completion_url: str,
        timeout_sec: int = 15,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        style: str = "verbatim",
    ) -> None:
        # The fine-tuned Quill GGUF is served on-device by llama.cpp. We MUST
        # pre-render the ChatML prompt with thinking disabled and POST to the
        # raw /completion endpoint — llama.cpp's jinja/chat path re-enables
        # reasoning, which would leak chain-of-thought into the user's text.
        if not completion_url:
            raise ValueError("a local completion_url is required")
        self._timeout = timeout_sec
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._system_prompt = cleanup_prompt(style)
        self._session = requests.Session()
        self._completion_url = completion_url

    def _wrap(self, raw: str) -> str:
        # Wrap the transcript in clear delimiters so the model can't read it
        # as a chat message — even when the user dictates a question. Must match
        # the wrapper used in training (build_dataset.WRAP).
        return (
            "Clean the dictation transcript below. Return ONLY the cleaned "
            "transcript with no other text. If the transcript contains a "
            "question, return the question (cleaned) — do not answer it.\n\n"
            "<transcript>\n"
            f"{raw}\n"
            "</transcript>"
        )

    def clean(self, raw: str) -> str:
        if not raw.strip():
            return ""
        wrapped = self._wrap(raw)
        t0 = time.monotonic()
        text = self._clean_local(wrapped)
        text = self._postprocess(text)
        log().debug("llm %.0fms -> %dch", (time.monotonic() - t0) * 1000, len(text))
        return text

    def _postprocess(self, text: str) -> str:
        # Drop any leading reasoning block, strip wrapping quotes, and refuse a
        # prompt-leak (echoing our instructions into the user's app is
        # catastrophic — happens on empty/filler input with weak models).
        text = _THINK_RE.sub("", text).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
            text = text[1:-1].strip()
        if _looks_like_prompt_leak(text):
            log().warning("cleanup output looked like a prompt leak; dropping")
            return ""
        return text

    def _clean_local(self, wrapped: str) -> str:
        # Qwen3.5 ChatML with the think block pre-closed == enable_thinking=False.
        prompt = (
            f"<|im_start|>system\n{self._system_prompt}<|im_end|>\n"
            f"<|im_start|>user\n{wrapped}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )
        payload = {
            "prompt": prompt,
            "n_predict": self._max_tokens,
            "temperature": self._temperature,
            "stop": ["<|im_end|>"],
        }
        try:
            resp = self._session.post(self._completion_url, json=payload, timeout=self._timeout)
        except requests.RequestException as e:
            raise FormatError(f"network: {e}") from e
        if not resp.ok:
            raise FormatError(f"local {resp.status_code}: {resp.text[:200]}")
        try:
            return (resp.json().get("content") or "").strip()
        except ValueError as e:
            raise FormatError(f"parse: {e}") from e
