"""Load cross-platform assets from the WhisperFlowClone/shared/ directory.

Same content is consumed by the Android app via its assets source set. The
contract: the *files* are the single source of truth — the Python loader is
just a thin path-resolver."""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path


def _shared_root() -> Path:
    """Resolve the shared/ directory in either dev or PyInstaller-frozen mode."""
    # PyInstaller-frozen: extracted into sys._MEIPASS via the spec's datas.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "shared"
    # Dev mode: <repo>/shared/, found relative to this file.
    # voice_type/_shared.py -> voice_type/ -> voice-type/ -> repo root.
    return Path(__file__).resolve().parent.parent.parent / "shared"


def _read_text(name: str) -> str:
    path = _shared_root() / name
    return path.read_text(encoding="utf-8")


def _read_json(name: str):
    path = _shared_root() / name
    return json.loads(path.read_text(encoding="utf-8"))


_VALID_STYLES = ("verbatim", "tidy", "formatted")


@lru_cache(maxsize=4)
def cleanup_prompt(style: str = "verbatim") -> str:
    """Compose the cleanup system prompt: the universal base with the chosen
    editing-style block substituted in. Unknown styles fall back to verbatim."""
    if style not in _VALID_STYLES:
        style = "verbatim"
    base = _read_text("cleanup-base.txt")
    style_block = _read_text(f"style-{style}.txt").strip()
    return base.replace("{{STYLE}}", style_block).rstrip() + "\n"


@lru_cache(maxsize=1)
def scratch_phrases() -> tuple[str, ...]:
    """Voice commands that erase the last paste."""
    return tuple(_read_json("scratch-phrases.json"))


@lru_cache(maxsize=1)
def whisper_hallucinations() -> frozenset[str]:
    """Whisper outputs to filter on short/silent clips."""
    return frozenset(s.lower() for s in _read_json("hallucinations.json"))
