"""Daemon entry point: `python -m voice_type` (dev) or the bundled `voice-type` binary."""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import load
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


def main() -> int:
    # The daemon has NO GUI of its own — Quobi (the Tauri desktop app) is the
    # single front end. This binary only runs the engine + its download/util
    # subcommands below.
    args = sys.argv[1:]
    _set_proc_name("quobi-daemon")  # ps/top/System Monitor shows "quobi-daemon"

    # Cleanup-model (Quill GGUF) download with progress + SHA-256 verify. The
    # GUI calls this for the first-run / Settings model download.
    if "--download-cleanup" in args:
        from .download import download_cleanup_model
        i = args.index("--download-cleanup")
        if i + 1 >= len(args):
            print("usage: voice-type --download-cleanup <0.8b|2b|4b>", file=sys.stderr)
            return 2
        return download_cleanup_model(args[i + 1])

    # Parakeet STT (sherpa-onnx ONNX bundle) download with progress + SHA-256
    # verify. Variant: "english" (default) or "multilingual".
    if "--download-parakeet" in args:
        from .download import DEFAULT_PARAKEET_VARIANT, download_parakeet_model
        i = args.index("--download-parakeet")
        variant = args[i + 1] if i + 1 < len(args) else DEFAULT_PARAKEET_VARIANT
        return download_parakeet_model(variant)

    cfg = load()
    configure(cfg.log.level, cfg.log.file)
    log().info(
        "voice-type %s starting (session=%s config=%s)",
        __version__, detect_session(), cfg.config_path,
    )

    # Everything is on-device: Parakeet STT + a local llama.cpp cleanup server.
    # No API key, no network in the dictation path.

    # Local STT (Parakeet) loads a pre-provisioned ONNX bundle that the GUI
    # downloads on first run, so there's no surprise synchronous model fetch here.
    try:
        whisper = make_transcriber(cfg.transcribe)
    except Exception as e:  # noqa: BLE001
        log().error("transcription init failed: %s", e)
        _notify_setup_error("transcription setup failed", str(e))
        return 3
    # Cleanup styles are gated by which Quill model is loaded — each size is only
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
    cap = _model_style_cap(cfg.cleanup.local_model)
    if _STYLE_RANK.get(effective_style, 0) > _STYLE_RANK[cap]:
        log().info("cleanup style %r exceeds what the loaded model is trained for "
                   "(cap=%s); using %r", effective_style, cap, cap)
        effective_style = cap

    # Cleanup: spin up the bundled llama.cpp server on the fine-tuned Quill GGUF
    # and point the Formatter at its on-device /completion endpoint.
    llm_server = None
    if cfg.cleanup.enabled:
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
            # Don't die — degrade gracefully. Type the RAW transcription (no
            # polish) so dictation still works while the cleanup model is missing
            # or downloading. The GUI's first-run setup fetches it; until then,
            # raw text beats a dead daemon.
            log().warning("local cleanup model unavailable (%s); running WITHOUT "
                          "cleanup — raw transcription only", e)
            _notify("cleanup not ready",
                    "Dictation works now (raw text). Download the cleanup model in "
                    "Settings → Cleanup to enable polish.")
            llm_server = None

    # Cleanup is active only if enabled AND the local server actually started.
    # If the model was missing, llm_server is None -> no Formatter -> raw output.
    formatter = (
        Formatter(
            completion_url=llm_server.completion_url,
            timeout_sec=cfg.cleanup.timeout_sec,
            max_tokens=cfg.cleanup.max_tokens,
            temperature=cfg.cleanup.temperature,
            style=effective_style,
        )
        if llm_server is not None
        else None
    )
    if cfg.cleanup.enabled:
        log().info("cleanup model=%s", Path(cfg.cleanup.local_model).name or "(none)")

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
