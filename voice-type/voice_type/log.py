"""Single namespaced logger. Stderr by default; optional rotating file handler."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER = logging.getLogger("voice-type")
_CONFIGURED = False
_FMT = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")


def configure(level: str = "INFO", file: str = "") -> None:
    global _CONFIGURED
    _LOGGER.handlers.clear()

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setFormatter(_FMT)
    _LOGGER.addHandler(stderr_h)

    if file:
        try:
            path = Path(os.path.expanduser(os.path.expandvars(file))).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            # 512KB × 3 — enough for a few sessions of dictation, not enough
            # to grow without bound on a workstation.
            file_h = RotatingFileHandler(
                path, maxBytes=512_000, backupCount=2, encoding="utf-8"
            )
            file_h.setFormatter(_FMT)
            _LOGGER.addHandler(file_h)
        except OSError as e:
            print(f"voice-type: log file disabled ({e})", file=sys.stderr)

    _LOGGER.setLevel(level.upper())
    _LOGGER.propagate = False
    _CONFIGURED = True


def log() -> logging.Logger:
    if not _CONFIGURED:
        configure()
    return _LOGGER
