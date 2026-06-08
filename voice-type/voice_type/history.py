"""Append-only JSONL history of dictations, capped at max_entries.

Each entry records both the raw Whisper transcript and the cleaned output,
plus a status and (when saved) a path to the dictation's audio so a failed
transcription can be retried from the history GUI.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .log import log


class History:
    def __init__(self, path: Path, max_entries: int = 1000) -> None:
        self._path = path
        self._max = max(10, max_entries)
        self._lock = threading.Lock()

    def append(
        self,
        raw: str,
        cleaned: str,
        duration_sec: float,
        kind: str = "dictation",
        status: str = "ok",
        audio: str = "",
        error: str = "",
        dictation_id: str = "",
    ) -> None:
        # Record failures even with empty text — they're the whole point of
        # the retry feature. Only skip truly empty *successful* dictations.
        if status == "ok" and not (raw or cleaned) and kind == "dictation":
            return
        rec = {
            "id": dictation_id or f"{int(time.time() * 1000)}",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": kind,
            "status": status,
            "duration": round(duration_sec, 2),
            "raw": raw,
            "cleaned": cleaned,
            "audio": audio,
            "error": error,
        }
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
                self._maybe_trim()
        except OSError as e:
            log().debug("history write failed: %s", e)

    def read_all(self) -> list[dict]:
        """Return every entry, oldest-first. Tolerates older-format lines."""
        out: list[dict] = []
        try:
            with self._lock, open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Backfill fields missing from older entries.
                    rec.setdefault("status", "ok")
                    rec.setdefault("audio", "")
                    rec.setdefault("error", "")
                    rec.setdefault("id", "")
                    out.append(rec)
        except FileNotFoundError:
            return []
        except OSError as e:
            log().debug("history read failed: %s", e)
        return out

    def update_by_id(self, dictation_id: str, **fields) -> bool:
        """Rewrite the entry with this id, updating the given fields. Used by
        the retry flow. Returns True if an entry was updated."""
        if not dictation_id:
            return False
        try:
            with self._lock:
                try:
                    lines = self._path.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    return False
                updated = False
                for i, line in enumerate(lines):
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("id") == dictation_id:
                        rec.update(fields)
                        lines[i] = json.dumps(rec, ensure_ascii=False)
                        updated = True
                        break
                if not updated:
                    return False
                tmp = self._path.with_suffix(self._path.suffix + ".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(self._path)
                return True
        except OSError as e:
            log().debug("history update failed: %s", e)
            return False

    def _maybe_trim(self) -> None:
        try:
            with open(self._path, "rb") as f:
                count = sum(1 for _ in f)
        except OSError:
            return
        if count <= int(self._max * 1.2):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            lines = lines[-self._max:]
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines)
            tmp.replace(self._path)
        except OSError as e:
            log().debug("history trim failed: %s", e)
