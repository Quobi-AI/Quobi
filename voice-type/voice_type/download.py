"""One-time Whisper model download with progress reporting.

faster-whisper downloads its model the first time you construct it, silently,
which looks like a freeze. This module pulls the model explicitly and writes a
small JSON status file the GUI polls to draw a progress bar. Run via the
`--download-model NAME` subcommand; the daemon then loads the (now-cached)
model instantly.
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

# NVIDIA Parakeet RNN-T 1.1B (English), exported from NeMo to sherpa-onnx ONNX
# and hosted on HF. This is the default local STT backend (runs in-process via
# sherpa-onnx, CPU, no sidecar). The bundle is a handful of files that all land
# in models_dir()/parakeet/.
#
# IMPORTANT: the sha256s below are pinned to the uploaded files. They are EMPTY
# until the one-time export+upload is done (see docs/PARAKEET.md). download_
# parakeet_model() REFUSES to run while any sha is empty, so we never ship an
# unverified model. After uploading, fill these in from the upload manifest.
PARAKEET_BASE_URL = "https://huggingface.co/quobi/parakeet-rnnt-1.1b-onnx/resolve/main"
PARAKEET_FILES = [
    {"file": "encoder.int8.onnx", "sha256": ""},
    {"file": "decoder.int8.onnx", "sha256": ""},
    {"file": "joiner.int8.onnx", "sha256": ""},
    {"file": "tokens.txt", "sha256": ""},
]


def parakeet_dir_path() -> Path:
    """The dir the Parakeet ONNX bundle downloads to (and that the daemon's
    [transcribe].parakeet_dir points at)."""
    return models_dir() / "parakeet"


def parakeet_ready() -> bool:
    """True if every Parakeet bundle file is present on disk."""
    d = parakeet_dir_path()
    return all((d / e["file"]).exists() for e in PARAKEET_FILES)


def download_parakeet_model() -> int:
    """Download the Parakeet sherpa-onnx ONNX bundle into models_dir()/parakeet/,
    reporting aggregate % to the status file and verifying SHA-256 per file before
    any file is made usable. Returns a process exit code (0 ok, 1 failure)."""
    model = "parakeet-rnnt-1.1b"
    if any(not e["sha256"] for e in PARAKEET_FILES):
        _write({"state": "error", "model": model, "pct": 0,
                "error": "Parakeet manifest not pinned yet (export+upload pending; "
                         "see docs/PARAKEET.md)"})
        return 1

    dest = parakeet_dir_path()
    dest.mkdir(parents=True, exist_ok=True)

    if parakeet_ready():
        _write({"state": "done", "model": model, "pct": 100})
        return 0

    _write({"state": "downloading", "model": model, "pct": 0})

    # Download each file, advancing a single aggregate bar across the whole set.
    # We don't know per-file sizes until the response headers arrive, so the bar
    # is weighted by bytes-as-they-stream against the sum of Content-Lengths
    # seen so far; for a fixed small file set this is smooth enough.
    last_pct = -1
    grand_total = 0
    grand_done = 0
    # First pass would need HEADs to know totals up front; instead we just split
    # the bar evenly across files and fill each file's slice 0..1 as it streams.
    n = len(PARAKEET_FILES)
    for idx, entry in enumerate(PARAKEET_FILES):
        fname = entry["file"]
        url = f"{PARAKEET_BASE_URL}/{fname}"
        if not url.startswith("https://"):
            _write({"state": "error", "model": model, "pct": max(0, last_pct),
                    "error": "non-HTTPS url"})
            return 1
        final = dest / fname
        part = dest / (fname + ".part")
        if final.exists():
            continue
        h = hashlib.sha256()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quobi"})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310 (https-checked)
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(part, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        frac = (done / total) if total else 0.0
                        pct = max(0, min(99, int((idx + frac) * 100 / n)))
                        if pct != last_pct:
                            last_pct = pct
                            _write({"state": "downloading", "model": model, "pct": pct})
        except Exception as e:  # noqa: BLE001
            part.unlink(missing_ok=True)
            _write({"state": "error", "model": model, "pct": max(0, last_pct), "error": str(e)})
            return 1

        if h.hexdigest() != entry["sha256"]:
            part.unlink(missing_ok=True)
            _write({"state": "error", "model": model, "pct": 0,
                    "error": f"checksum mismatch on {fname} (got {h.hexdigest()[:16]}…)"})
            return 1
        part.replace(final)  # only verified files appear

    _write({"state": "done", "model": model, "pct": 100})
    return 0


# --- whisper.cpp STT model download (ggml, the Vulkan transcription model) ---

# Official whisper.cpp ggml models, public on HF. SHA-256 verified before use.
# "small" is the default (lightweight, ~488 MB, downloads fast); large-v3-turbo
# is the accuracy upgrade (~1.6 GB).
WHISPER_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
DEFAULT_WHISPER = "small"
WHISPER_MANIFEST = {
    "small": {
        "file": "ggml-small.bin",
        "sha256": "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
    },
    "large-v3-turbo": {
        "file": "ggml-large-v3-turbo.bin",
        "sha256": "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
    },
}


def whisper_model_path(name: str = DEFAULT_WHISPER) -> Path | None:
    """On-disk path a given whisper.cpp model downloads to (None if unknown)."""
    entry = WHISPER_MANIFEST.get(name.lower())
    return models_dir() / "whisper" / entry["file"] if entry else None


# Silero VAD ggml — lets whisper.cpp drop silence so it can't hallucinate
# "Thank you" / "thanks for watching" on trailing silence. Tiny (~0.9 MB).
WHISPER_VAD = {
    "url": "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin",
    "file": "ggml-silero-v5.1.2.bin",
    "sha256": "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf",
}


def _ensure_vad_model(dest: Path) -> None:
    """Best-effort: fetch the Silero VAD ggml into `dest`, SHA-verified, sitting
    next to the whisper model where the daemon auto-detects it. Non-fatal — STT
    still works without it (just without the silence-hallucination guard)."""
    final = dest / WHISPER_VAD["file"]
    if final.exists():
        return
    part = dest / (WHISPER_VAD["file"] + ".part")
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(WHISPER_VAD["url"], headers={"User-Agent": "quobi"})
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() == WHISPER_VAD["sha256"]:
            part.replace(final)
        else:
            part.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — VAD is optional, never fail the STT download over it
        part.unlink(missing_ok=True)


def download_whisper_model(name: str = DEFAULT_WHISPER) -> int:
    """Download a whisper.cpp ggml STT model from HuggingFace into the models
    dir (verifying SHA-256 before it's usable), reporting % to the status file.
    Returns a process exit code (0 ok, 1 failure)."""
    name = name.lower()
    entry = WHISPER_MANIFEST.get(name)
    if not entry:
        _write({"state": "error", "model": name, "pct": 0,
                "error": f"unknown whisper model: {name}"})
        return 1

    fname = entry["file"]
    url = f"{WHISPER_BASE_URL}/{fname}"
    dest = models_dir() / "whisper"
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / fname
    part = dest / (fname + ".part")

    if final.exists():
        _ensure_vad_model(dest)  # make sure VAD lands even if the model was already there
        _write({"state": "done", "model": name, "pct": 100})
        return 0

    _write({"state": "downloading", "model": name, "pct": 0})
    if not url.startswith("https://"):
        _write({"state": "error", "model": name, "pct": 0, "error": "non-HTTPS url"})
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
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if total:
                        pct = max(0, min(99, int(done * 100 / total)))
                        if pct != last_pct:
                            last_pct = pct
                            _write({"state": "downloading", "model": name,
                                    "pct": pct, "total_bytes": total})
    except Exception as e:  # noqa: BLE001
        part.unlink(missing_ok=True)
        _write({"state": "error", "model": name, "pct": max(0, last_pct), "error": str(e)})
        return 1

    digest = h.hexdigest()
    if digest != entry["sha256"]:
        part.unlink(missing_ok=True)
        _write({"state": "error", "model": name, "pct": 0,
                "error": f"checksum mismatch (got {digest[:16]}…)"})
        return 1

    part.replace(final)
    _ensure_vad_model(dest)  # fetch the small VAD model alongside the STT model
    _write({"state": "done", "model": name, "pct": 100})
    return 0


def download_model(name: str) -> int:
    """Download Systran/faster-whisper-<name>, reporting % to the status file.
    Returns a process exit code (0 ok, 1 failure)."""
    _write({"state": "downloading", "model": name, "pct": 0})
    try:
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm as _base_tqdm
    except ImportError as e:
        _write({"state": "error", "model": name, "pct": 0, "error": str(e)})
        return 1

    # Resolve the HF repo the SAME way faster-whisper does at load time, so a
    # name we offer always points at a repo that exists. Most live under
    # Systran/faster-whisper-<name>, but turbo and distil tiers are hosted by
    # other orgs (e.g. large-v3-turbo -> mobiuslabsgmbh/...). Falling back to the
    # Systran pattern keeps arbitrary CT2 model ids working.
    try:
        from faster_whisper.utils import _MODELS
        repo_id = _MODELS.get(name, f"Systran/faster-whisper-{name}")
    except Exception:  # noqa: BLE001
        repo_id = f"Systran/faster-whisper-{name}"

    # huggingface_hub creates one tqdm per file; aggregate their byte counts so
    # the bar reflects the whole download (model.bin dominates). Throttle file
    # writes to whole-percent changes.
    #
    # We aggregate ONLY the per-file byte bars (unit == "B"), recomputing the
    # total/done from every live bar's current state on each update. This is
    # deliberately not a one-shot sum at __init__: hf_hub creates an outer
    # "Fetching N files" *count* bar (total = N, unit = "it") plus per-file byte
    # bars whose real byte total only arrives AFTER construction (it's None at
    # init, esp. on the Xet HTTP fallback). Summing totals at init therefore
    # captured just the file count (e.g. 7) and the bar shot to a false 99%.
    state = {"last_pct": -1}
    bars: list = []
    lock = threading.Lock()

    def _is_bytes(bar) -> bool:
        return getattr(bar, "unit", "") == "B" and bool(bar.total)

    class _ProgressTqdm(_base_tqdm):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            with lock:
                bars.append(self)

        def update(self, n=1):
            super().update(n)
            with lock:
                total = sum(int(b.total) for b in bars if _is_bytes(b))
                done = sum(int(b.n) for b in bars if _is_bytes(b))
                if total <= 0:
                    return
                pct = max(0, min(99, int(done * 100 / total)))  # 100 on return
                if pct != state["last_pct"]:
                    state["last_pct"] = pct
                    _write({"state": "downloading", "model": name, "pct": pct,
                            "total_bytes": total})

    try:
        snapshot_download(repo_id, tqdm_class=_ProgressTqdm)
    except Exception as e:  # noqa: BLE001 — surface any failure to the GUI
        _write({"state": "error", "model": name, "pct": state["last_pct"], "error": str(e)})
        return 1
    _write({"state": "done", "model": name, "pct": 100})
    return 0
