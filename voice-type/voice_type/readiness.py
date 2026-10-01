"""Daemon startup self-report.

The daemon writes `readiness.json` at the end of every startup attempt — success
or fatal — so the GUI can explain *why* dictation is (not) working without
parsing the human-readable log. This captures the facts only the daemon knows:
which output/hotkey backend it actually selected, and a stable machine-readable
code for its last fatal error (e.g. the compositor refusing virtual-keyboard, or
the user not being in the `input` group).

Writing is best-effort: a failure here must never take the daemon down, so every
path swallows OSError.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .log import log

# Stable error codes the GUI maps to remediation cards. Keep in sync with the
# Rust `readiness.rs` deserializer and the design doc's error-code table.
CODE_NO_INPUT_GROUP = "no_input_group"
CODE_UINPUT_DENIED = "uinput_denied"
CODE_NO_OUTPUT_BACKEND = "no_output_backend"
CODE_YDOTOOL_SOCKET_MISSING = "ydotool_socket_missing"
CODE_WTYPE_REFUSED = "wtype_refused"
CODE_MISSING_WL_CLIPBOARD = "missing_wl_clipboard"
CODE_PARAKEET_MISSING = "parakeet_missing"
CODE_UNKNOWN = "unknown"

# Substring -> code, first match wins. The daemon funnels several distinct
# RuntimeErrors through single `except` blocks (esp. output.make_backend), so we
# classify by the message text those call-sites already raise. Order matters:
# put the most specific signatures first.
_CODE_SIGNATURES: list[tuple[str, str]] = [
    ("no readable keyboard", CODE_NO_INPUT_GROUP),
    ("cannot create uinput", CODE_UINPUT_DENIED),
    ("ydotoold socket not found", CODE_YDOTOOL_SOCKET_MISSING),
    ("refused virtual-keyboard", CODE_WTYPE_REFUSED),
    ("wl-clipboard", CODE_MISSING_WL_CLIPBOARD),
    ("wl-copy", CODE_MISSING_WL_CLIPBOARD),
    ("wl-paste", CODE_MISSING_WL_CLIPBOARD),
    ("parakeet", CODE_PARAKEET_MISSING),
    # Generic output-backend failure last, so a more specific socket/clipboard
    # signature above wins when the message contains both.
    ("no working wayland output backend", CODE_NO_OUTPUT_BACKEND),
    ("ydotool not found", CODE_NO_OUTPUT_BACKEND),
]


def classify(message: str) -> str:
    """Map a daemon error message to a stable code (best-effort)."""
    m = (message or "").lower()
    for sig, code in _CODE_SIGNATURES:
        if sig in m:
            return code
    return CODE_UNKNOWN


def _state_dir() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return state / "voice-type"


def readiness_path() -> Path:
    return _state_dir() / "readiness.json"


def write_readiness(
    *,
    ok: bool,
    session: str,
    de: str,
    output_backend: str | None = None,
    hotkey_backend: str | None = None,
    error_code: str | None = None,
    error_backend: str | None = None,
    error_message: str | None = None,
) -> None:
    """Atomically write the startup outcome to readiness.json. Best-effort.

    error_backend identifies which subsystem failed: "transcribe", "output",
    "hotkey", or "cleanup". None of the error_* fields are set when ok is True.
    """
    payload: dict = {
        "ts": int(time.time()),
        "session": session,
        "de": de,
        "output_backend": output_backend,
        "hotkey_backend": hotkey_backend,
        "ok": ok,
        "error": None
        if ok
        else {
            "code": error_code or CODE_UNKNOWN,
            "backend": error_backend,
            "message": error_message,
        },
    }
    try:
        p = readiness_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(p)  # atomic on POSIX
    except OSError as e:
        log().debug("could not write readiness.json: %s", e)
