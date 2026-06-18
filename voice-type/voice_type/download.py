"""One-time model downloads (Quill cleanup GGUF, Parakeet STT bundle) with
progress reporting.

Each downloader pulls its files explicitly, verifies SHA-256, and writes a small
JSON status file the GUI polls to draw a progress bar. Invoked via the daemon's
`--download-cleanup` / `--download-parakeet` subcommands so the daemon then loads
the (now-present) models instantly.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import threading
import urllib.request
from pathlib import Path


def _ssl_context() -> ssl.SSLContext:
    """A CA bundle that also works inside a frozen PyInstaller build, where the
    OS trust store isn't available — certifi ships the root certs (and is
    already pulled in by huggingface_hub). Falls back to system defaults."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _state_dir() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return state / "voice-type"


def progress_file() -> Path:
    return _state_dir() / "download.json"


def read_progress() -> dict:
    """Current download status, or an 'idle' record if none exists."""
    try:
        return json.loads(progress_file().read_text())
    except (OSError, ValueError):
        return {"state": "idle", "model": "", "pct": 0}


def _write(record: dict) -> None:
    p = progress_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a poll never sees a half-written file.
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record))
        tmp.replace(p)
    except OSError:
        pass


# --- Cleanup GGUF download (the fine-tuned Quill models, public on HF) -------

# Public, Apache-2.0. The SHA-256s are verified against the uploaded files;
# a download whose hash doesn't match is rejected (never loaded).
QUILL_BASE_URL = "https://huggingface.co/quobi/quill/resolve/main"
CLEANUP_MANIFEST = {
    "0.8b": {
        "file": "quill-0.8b-Q4_K_M.gguf",
        "sha256": "aa54d6f6108d66e4b60a57bdc04ecca6e84e073504918a64b41ac4a0f816f16d",
    },
    "2b": {
        "file": "quill-2b-Q4_K_M.gguf",
        "sha256": "b877a22b773d2aac40b3c642c24f1cbbb0b3f1d42cbd3c6eb936533719317196",
    },
    "4b": {
        "file": "quill-4b-Q4_K_M.gguf",
        "sha256": "e5e6bd7e92690c6f954399c473e740561d9deff0862e1bfe42c1f6055535b987",
    },
}


def models_dir() -> Path:
    """Where cleanup GGUFs live — the same dir the GUI picker scans."""
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return data / "voice-type" / "models"


def cleanup_model_path(tier: str) -> Path | None:
    """The on-disk path a given tier downloads to (None if tier unknown)."""
    entry = CLEANUP_MANIFEST.get(tier.lower())
    return models_dir() / entry["file"] if entry else None


def download_cleanup_model(tier: str) -> int:
    """Download a Quill cleanup GGUF from HuggingFace into the models dir,
    reporting % to the status file and verifying SHA-256 before the file is
    made usable. Returns a process exit code (0 ok, 1 failure)."""
    tier = tier.lower()
    entry = CLEANUP_MANIFEST.get(tier)
    if not entry:
        _write({"state": "error", "model": tier, "pct": 0,
                "error": f"unknown model tier: {tier}"})
        return 1

    name = entry["file"]
    url = f"{QUILL_BASE_URL}/{name}"
    dest = models_dir()
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / name
    part = dest / (name + ".part")

    # Already present and valid? Treat as done (idempotent re-trigger).
    if final.exists():
        _write({"state": "done", "model": tier, "pct": 100})
        return 0

    _write({"state": "downloading", "model": tier, "pct": 0})
    if not url.startswith("https://"):  # defense-in-depth: HTTPS only
        _write({"state": "error", "model": tier, "pct": 0, "error": "non-HTTPS url"})
        return 1

    h = hashlib.sha256()
    last_pct = -1
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quobi"})
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310 (https-checked)
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)  # 1 MiB
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if total:
                        pct = max(0, min(99, int(done * 100 / total)))
                        if pct != last_pct:
                            last_pct = pct
                            _write({"state": "downloading", "model": tier,
                                    "pct": pct, "total_bytes": total})
    except Exception as e:  # noqa: BLE001 — surface any failure to the GUI
        part.unlink(missing_ok=True)
        _write({"state": "error", "model": tier, "pct": max(0, last_pct),
                "error": str(e)})
        return 1

    digest = h.hexdigest()
    if digest != entry["sha256"]:
        part.unlink(missing_ok=True)
        _write({"state": "error", "model": tier, "pct": 0,
                "error": f"checksum mismatch (got {digest[:16]}…)"})
        return 1

    part.replace(final)  # atomic: only a verified file ever appears as the GGUF
    _write({"state": "done", "model": tier, "pct": 100})
    return 0


# --- Parakeet STT model download (sherpa-onnx ONNX bundle) -------------------

# NVIDIA Parakeet TDT 0.6B (FastConformer TDT, CC-BY-4.0), exported to sherpa-onnx
# ONNX by k2-fsa and published as a single tarball on their GitHub release. STT
# runs in-process via sherpa-onnx (CPU, no sidecar). Two variants:
#   "english"      — v2: the best English model (HF Open ASR #1 English). DEFAULT.
#   "multilingual" — v3: 25 languages with automatic language detection. Opt-in
#                    for users who dictate in something other than English.
# Each downloads to models_dir()/parakeet/<variant>/. SHA pinned per asset.
PARAKEET_VARIANTS = {
    "english": {
        "model_id": "parakeet-tdt-0.6b-v2",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
               "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
        "sha256": "157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad",
        "bytes": 482468385,
    },
    "multilingual": {
        "model_id": "parakeet-tdt-0.6b-v3",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
               "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
        "sha256": "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
        "bytes": 487170055,
    },
}
DEFAULT_PARAKEET_VARIANT = "english"
# The files we keep from the archive (it also ships a test_wavs/ dir we drop).
# Archive members are nested under a top-level dir; we extract by basename.
PARAKEET_MEMBERS = ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"]


def parakeet_dir_path(variant: str = DEFAULT_PARAKEET_VARIANT) -> Path:
    """The dir a Parakeet variant extracts to (and that [transcribe].parakeet_dir
    points at when that variant is selected)."""
    return models_dir() / "parakeet" / variant


def parakeet_ready(variant: str = DEFAULT_PARAKEET_VARIANT) -> bool:
    """True if every file of the given Parakeet variant is present on disk."""
    d = parakeet_dir_path(variant)
    return all((d / name).exists() for name in PARAKEET_MEMBERS)


def download_parakeet_model(variant: str = DEFAULT_PARAKEET_VARIANT) -> int:
    """Download a Parakeet variant's sherpa-onnx tarball, SHA-256 verify it, and
    extract the ONNX bundle into models_dir()/parakeet/<variant>/. Reports % to
    the status file. Returns a process exit code (0 ok, 1 failure)."""
    import bz2
    import tarfile
    entry = PARAKEET_VARIANTS.get(variant)
    if not entry:
        _write({"state": "error", "model": variant, "pct": 0,
                "error": f"unknown parakeet variant: {variant}"})
        return 1
    model = entry["model_id"]
    url = entry["url"]
    dest = parakeet_dir_path(variant)
    dest.mkdir(parents=True, exist_ok=True)

    if parakeet_ready(variant):
        _write({"state": "done", "model": model, "pct": 100})
        return 0

    _write({"state": "downloading", "model": model, "pct": 0})
    if not url.startswith("https://"):  # defense-in-depth: HTTPS only
        _write({"state": "error", "model": model, "pct": 0, "error": "non-HTTPS url"})
        return 1

    tarball = dest / "bundle.tar.bz2.part"
    h = hashlib.sha256()
    last_pct = -1
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quobi"})
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310 (https-checked)
            total = int(resp.headers.get("Content-Length") or entry["bytes"])
            done = 0
            with open(tarball, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)  # 1 MiB
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if total:
                        # Reserve the last few % for the extract step.
                        pct = max(0, min(97, int(done * 97 / total)))
                        if pct != last_pct:
                            last_pct = pct
                            _write({"state": "downloading", "model": model,
                                    "pct": pct, "total_bytes": total})
    except Exception as e:  # noqa: BLE001 — surface any failure to the GUI
        tarball.unlink(missing_ok=True)
        _write({"state": "error", "model": model, "pct": max(0, last_pct), "error": str(e)})
        return 1

    if h.hexdigest() != entry["sha256"]:
        tarball.unlink(missing_ok=True)
        _write({"state": "error", "model": model, "pct": 0,
                "error": f"checksum mismatch (got {h.hexdigest()[:16]}…)"})
        return 1

    # Extract only the four files we need, by basename, into the parakeet dir.
    # Taking basename (not the archived path) also neutralizes any path-traversal
    # member names — we never honor a member's directory component.
    _write({"state": "downloading", "model": model, "pct": 98})
    wanted = set(PARAKEET_MEMBERS)
    try:
        with tarfile.open(fileobj=bz2.open(tarball, "rb")) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                base = os.path.basename(member.name)
                if base not in wanted:
                    continue
                src = tf.extractfile(member)
                if src is None:
                    continue
                tmp = dest / (base + ".part")
                with src, open(tmp, "wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                tmp.replace(dest / base)  # atomic per file
    except Exception as e:  # noqa: BLE001
        _write({"state": "error", "model": model, "pct": 98, "error": f"extract: {e}"})
        return 1
    finally:
        tarball.unlink(missing_ok=True)

    if not parakeet_ready():
        _write({"state": "error", "model": model, "pct": 98,
                "error": "extract incomplete (missing files after unpack)"})
        return 1

    _write({"state": "done", "model": model, "pct": 100})
    return 0
