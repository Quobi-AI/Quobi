"""Daemon entry point: `python -m voice_type` (dev) or the bundled `voice-type` binary."""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .config import load, user_config_dir
from .format import Formatter
from .history import History
from .indicator import make_indicator
from .input import make_listener
from .log import configure, log
from .output import detect_session, make_backend
from .pipeline import Pipeline
from .transcribe import make_transcriber


def _set_proc_name(name: str) -> None:
    """Brand this process as `name` in ps / top / System Monitor. Linux shows a
    process's `comm` (<=15 chars), which defaults to the binary basename
    ("voice-type"); prctl(PR_SET_NAME) overrides it. No-op off Linux / on error
    (Windows is branded via the PE version resource instead)."""
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(15, name.encode()[:15], 0, 0, 0)
        except Exception:  # noqa: BLE001
            pass


def _load_env() -> None:
    # Prefer the XDG location (where `make install` puts it). Fall back to a
    # .env in the CWD for `make dev` from the source tree.
    env_path = user_config_dir() / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
    else:
        load_dotenv()


def _state_dir() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return state / "voice-type"


def _history_path() -> Path:
    return _state_dir() / "history.jsonl"


def _audio_dir() -> Path:
    return _state_dir() / "audio"


def _notify(title: str, body: str, urgent: bool = False) -> None:
    """User-facing desktop notification via notify-send, so autostart-launched
    (no-terminal) daemons can still talk to the user. Used both for fatal setup
    errors and for slow one-time events like a first model download."""
    try:
        import shutil as _shutil
        import subprocess as _sp
        if _shutil.which("notify-send"):
            _sp.run(
                [
                    "notify-send", "-a", "Quobi",
                    "-u", "critical" if urgent else "normal",
                    f"Quobi - {title}", body,
                ],
                check=False,
            )
    except OSError:
        pass


def _notify_setup_error(title: str, body: str) -> None:
    """Critical notification for failures that mean the daemon can't start."""
    _notify(title, body, urgent=True)


# Rough on-disk sizes for the local Whisper models, for the download notice.
_WHISPER_SIZES = {
    "tiny": "~75 MB",
    "base": "~150 MB",
    "small": "~500 MB",
    "large-v3-turbo": "~1.6 GB",
    "large-v3": "~3 GB",
}


def _local_model_cached(model_size: str) -> bool:
    """True if the faster-whisper model is already in the HuggingFace cache, so
    constructing it won't trigger a (slow, silent) download. Mirrors how
    huggingface_hub resolves its cache dir, honouring HF_HOME / the hub var."""
    from pathlib import Path
    hub = os.environ.get("HUGGINGFACE_HUB_CACHE")
    if hub:
        base = Path(hub)
    elif os.environ.get("HF_HOME"):
        base = Path(os.environ["HF_HOME"]) / "hub"
    else:
        base = Path.home() / ".cache" / "huggingface" / "hub"
    repo = base / f"models--Systran--faster-whisper-{model_size}"
    snaps = repo / "snapshots"
    # A finished download leaves at least one snapshot dir with files in it.
    return snaps.is_dir() and any(p.iterdir() for p in snaps.iterdir() if p.is_dir())


def main() -> int:
    # The daemon has NO GUI of its own — Quobi (the Tauri desktop app) is the
    # single front end. This binary only runs the engine + its download/util
    # subcommands below.
    args = sys.argv[1:]
    _set_proc_name("quobi-daemon")  # ps/top/System Monitor shows "quobi-daemon"

    # Explicit model download with progress reporting (the GUI calls this so it
    # can draw a progress bar, then restarts the daemon to pick up the model).
    if "--download-model" in args:
        from .download import download_model
        i = args.index("--download-model")
        if i + 1 >= len(args):
            print("usage: voice-type --download-model <name>", file=sys.stderr)
            return 2
        return download_model(args[i + 1])

    # Cleanup-model (Quill GGUF) download with progress + SHA-256 verify. The
    # GUI calls this for the first-run / Settings model download.
    if "--download-cleanup" in args:
        from .download import download_cleanup_model
        i = args.index("--download-cleanup")
        if i + 1 >= len(args):
            print("usage: voice-type --download-cleanup <0.8b|2b|4b>", file=sys.stderr)
            return 2
        return download_cleanup_model(args[i + 1])

    # whisper.cpp STT model (ggml) download with progress + SHA-256 verify. The
    # GUI calls this on first run to fetch the Vulkan transcription model.
    if "--download-whisper" in args:
        from .download import download_whisper_model
        i = args.index("--download-whisper")
        name = args[i + 1] if i + 1 < len(args) else "small"
        return download_whisper_model(name)

    _load_env()
    cfg = load()
    configure(cfg.log.level, cfg.log.file)
    log().info(
        "voice-type %s starting (session=%s config=%s)",
        __version__, detect_session(), cfg.config_path,
    )

    # A key is only required for cloud work: cloud transcription, or a CLOUD
    # cleanup LLM. Fully-local STT + local cleanup needs no key at all.
    cloud_cleanup = cfg.cleanup.enabled and cfg.cleanup.engine != "local"
    needs_key = cloud_cleanup or cfg.transcribe.engine == "cloud"
    if needs_key and not cfg.groq_api_key:
        env_path = user_config_dir() / ".env"
        log().error("API key missing — expected in %s", env_path)
        _notify_setup_error("setup needed", f"Set your API key in {env_path}")
        return 2

    # Per-stage API keys: transcription and cleanup can use different
    # providers, so each can have its own key. Fall back to GROQ_API_KEY.
    transcribe_key = os.environ.get("VOICETYPE_TRANSCRIBE_KEY", "").strip() or cfg.groq_api_key
    cleanup_key = os.environ.get("VOICETYPE_CLEANUP_KEY", "").strip() or cfg.groq_api_key

    # Local STT downloads the model on first use, synchronously, before the
    # tray even exists — which otherwise looks like the daemon hanging. Warn the
    # user up front (they'd never guess), then confirm when it's ready.
    model = cfg.transcribe.local_model
    # The whisper.cpp path uses a pre-provisioned ggml file, not the
    # faster-whisper HF cache — so the cache-miss warning doesn't apply to it.
    using_whispercpp = cfg.transcribe.engine == "local" and bool(cfg.transcribe.local_gguf)
    will_download = (
        cfg.transcribe.engine == "local"
        and not using_whispercpp
        and not _local_model_cached(model)
    )
    if will_download:
        size = _WHISPER_SIZES.get(model, "")
        log().info("downloading whisper model %s (%s)", model, size or "size unknown")
        _notify(
            "downloading speech model",
            f"First-time setup: fetching the '{model}' Whisper model{f' ({size})' if size else ''}. "
            "Dictation won't work until this finishes — you'll get a notification when it's ready.",
        )
    try:
        whisper = make_transcriber(cfg.transcribe, transcribe_key)
    except Exception as e:  # noqa: BLE001
        log().error("transcription init failed: %s", e)
        _notify_setup_error("transcription setup failed", str(e))
        return 3
    if will_download:
        log().info("whisper model %s downloaded", model)
        _notify("speech model ready", f"The '{model}' model is downloaded. Dictation is ready to use.")
    log().info("transcribe engine=%s", cfg.transcribe.engine)
    cleanup_model = cfg.cleanup.resolved_model()
    # Cleanup styles are gated by which Quill model is loaded — each tier is only
    # trained for the styles it can actually do:
    #   0.8b -> verbatim;  2b -> verbatim, tidy;  4b -> verbatim, tidy, formatted.
    # Clamp a too-ambitious configured style DOWN to the model's cap so the output
    # stays sane (a small model asked for "formatted" produces garbage).
    _STYLE_RANK = {"verbatim": 0, "tidy": 1, "formatted": 2}

    def _model_style_cap(path: str) -> str:
        p = (path or "").lower()
        if "0.8b" in p:
            return "verbatim"
        if "2b" in p:
            return "tidy"
        if "4b" in p:
            return "formatted"
        return "formatted"  # unknown / custom model -> don't restrict

    effective_style = cfg.personalize.style
    if cfg.cleanup.engine == "local":
        cap = _model_style_cap(cfg.cleanup.local_model)
        if _STYLE_RANK.get(effective_style, 0) > _STYLE_RANK[cap]:
            log().info("cleanup style %r exceeds what the loaded model is trained for "
                       "(cap=%s); using %r", effective_style, cap, cap)
            effective_style = cap

    # Local cleanup tier: spin up the bundled llama.cpp server on the
    # fine-tuned GGUF and point the (unchanged) Formatter at localhost.
    llm_server = None
    cleanup_base_url = cfg.cleanup.base_url
    cleanup_api_key = cleanup_key
    if cfg.cleanup.enabled and cfg.cleanup.engine == "local":
        from .local_llm import LocalLLMError, LocalLLMServer, resolve_ngl
        ngl, accel_reason = resolve_ngl(cfg.cleanup.local_accel, cfg.cleanup.local_ngl)
        log().info("cleanup acceleration: %s", accel_reason)
        llm_server = LocalLLMServer(
            binary=cfg.cleanup.local_bin,
            model_path=cfg.cleanup.local_model,
            port=cfg.cleanup.local_port,
            n_gpu_layers=ngl,
            ctx=cfg.cleanup.local_ctx,
            threads=cfg.cleanup.local_threads,
        )
        try:
            llm_server.start()
        except LocalLLMError as e:
            # Don't die — degrade gracefully. Type the RAW Whisper output (no
            # polish) so dictation still works while the cleanup model is missing
            # or downloading. The GUI's first-run setup fetches it; until then,
            # raw text beats a dead daemon.
            log().warning("local cleanup model unavailable (%s); running WITHOUT "
                          "cleanup — raw transcription only", e)
            _notify("cleanup not ready",
                    "Dictation works now (raw text). Download the cleanup model in "
                    "Settings → Cleanup to enable polish.")
            llm_server = None
        else:
            cleanup_base_url = llm_server.base_url
            cleanup_api_key = "local"          # llama-server ignores the key
            cleanup_model = "local"            # it serves whatever GGUF is loaded

    formatter = (
        Formatter(
            api_key=cleanup_api_key,
            model=cleanup_model,
            base_url=cleanup_base_url,
            timeout_sec=cfg.cleanup.timeout_sec,
            max_tokens=cfg.cleanup.max_tokens,
            temperature=cfg.cleanup.temperature,
            style=effective_style,
            local_completion=(llm_server is not None),
            completion_url=(llm_server.completion_url if llm_server else ""),
        )
        # Cleanup is active only if enabled AND actually available: cloud, or a
        # local server that started. If the local cleanup model was missing,
        # llm_server is None -> no Formatter -> raw output (handled above).
        if cfg.cleanup.enabled and (cfg.cleanup.engine != "local" or llm_server is not None)
        else None
    )
    if cfg.cleanup.enabled:
        log().info("cleanup engine=%s tier=%s model=%s", cfg.cleanup.engine,
                   cfg.cleanup.tier, cleanup_model)

    try:
        output = make_backend(
            cfg.output.backend,
            terminal_aware=cfg.output.terminal_paste_aware,
            force_terminal=cfg.output.force_terminal_paste,
        )
    except RuntimeError as e:
        log().error("output backend init failed: %s", e)
        whisper.stop()
        if llm_server:
            llm_server.stop()
        return 3

    indicator = make_indicator(cfg.indicator.mode, cfg.indicator.floating)
    indicator.start()

    history = History(_history_path(), max_entries=cfg.history.max_entries) \
        if cfg.history.enabled \
        else _NullHistory()

    from .audiostore import AudioStore
    audio_store = AudioStore(_audio_dir(), max_files=50) if cfg.history.enabled else None

    pipeline = Pipeline(cfg, whisper, formatter, output, indicator, history, audio_store)

    # Wayland's evdev backend needs to clear the compositor's "key held"
    # state after ungrab — see EvdevHotkeyListener docstring. Pass the
    # output backend's release_key if it has one.
    release_injector = getattr(output, "release_key", None)

    try:
        listener = make_listener(
            cfg.hotkey.key,
            cfg.hotkey.backend,
            pipeline.on_press,
            pipeline.on_release,
            release_injector=release_injector,
        )
    except (ValueError, RuntimeError, ImportError) as e:
        log().error("hotkey listener init failed: %s", e)
        _notify_setup_error("hotkey setup failed", str(e))
        indicator.stop()
        whisper.stop()
        if llm_server:
            llm_server.stop()
        return 4
    pipeline.set_listener_pre_erase(listener.requires_pre_erase())

    stop = threading.Event()

    def _handle_signal(signum, _frame):
        log().info("signal %d -> shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGHUP"):       # Unix-only; absent on Windows
        signal.signal(signal.SIGHUP, _handle_signal)

    try:
        listener.start()
    except (RuntimeError, OSError, ImportError) as e:
        # On Wayland, evdev needs the user in the 'input' group. The error
        # message tells you how to fix it — surface it as a notification
        # so autostart-launched daemons aren't silently dead.
        log().error("hotkey listener start failed: %s", e)
        _notify_setup_error("hotkey setup needed", str(e))
        pipeline.shutdown()
        indicator.stop()
        whisper.stop()
        if llm_server:
            llm_server.stop()
        return 5

    indicator.notify(
        "Quobi",
        f"Ready — {cfg.hotkey.mode} {cfg.hotkey.key} to dictate",
    )
    log().info(
        "ready. hotkey=%s mode=%s output=%s/%s cleanup=%s overlay=%s",
        cfg.hotkey.key, cfg.hotkey.mode, output.name, cfg.output.mode,
        "on" if formatter else "off",
        "on" if cfg.indicator.floating else "off",
    )

    try:
        stop.wait()
    finally:
        listener.stop()
        pipeline.shutdown()
        indicator.stop()
        whisper.stop()
        if llm_server:
            llm_server.stop()
    return 0


class _NullHistory:
    """Used when history is disabled — same shape, no I/O."""

    def append(self, *_a, **_kw) -> None:  # pragma: no cover
        return None


if __name__ == "__main__":
    sys.exit(main())
