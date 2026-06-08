"""On-device transcription via a whisper.cpp `whisper-server` sidecar.

This is the STT twin of local_llm.py. The daemon spawns whisper-server bound to
localhost, which exposes a `/inference` endpoint that takes a WAV upload and
returns the transcript. Built with the GGML **Vulkan** backend, so it GPU-
accelerates on ANY GPU (NVIDIA / AMD / Intel) with NO CUDA runtime — the exact
same zero-dependency stack the cleanup model already runs on. Falls back to CPU
when no GPU is present (or accel='cpu').

Why a server instead of in-process bindings: it mirrors the cleanup sidecar
(one provisioning/bundling story for both), keeps the heavy native backend out
of the Python process, and lets us ship a single prebuilt Vulkan binary + its
co-located ggml .so's rather than a pip wheel with a CUDA toolchain.

Lifecycle is owned by the daemon: start() on boot, stop() on shutdown.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from .log import log
# Reuse the cleanup sidecar's GPU detection so both stacks agree on "is there a
# GPU worth using" for accel='auto'. _NO_WINDOW suppresses the Windows console
# window when spawning the console-subsystem whisper-server.exe (no-op on Linux).
from .local_llm import detect_gpu, _NO_WINDOW, branded_exec_path


class LocalWhisperServerError(Exception):
    pass


class LocalWhisperServer:
    def __init__(
        self,
        binary: str,
        model_path: str,
        port: int = 8090,
        accel: str = "auto",
        language: str = "",
        threads: int = 0,
        startup_timeout: int = 120,
        vad: bool = True,
        vad_model: str = "",
    ) -> None:
        self._bin = binary
        self._model = model_path
        self._port = port
        self._accel = (accel or "auto").lower()
        self._language = language or "auto"
        self._threads = threads
        self._timeout = startup_timeout
        self._vad = vad
        self._vad_model = vad_model
        self._proc: subprocess.Popen | None = None
        self._log_path = Path.home() / ".local/state/voice-type/whisper-server.log"

    def _resolve_vad_model(self) -> str | None:
        """Path to the Silero VAD ggml model, or None. Honors an explicit
        vad_model; else auto-detects a ggml-silero-*.bin sitting next to the
        whisper model (the bundled/downloaded layout)."""
        if self._vad_model and Path(self._vad_model).is_file():
            return self._vad_model
        # Look next to the whisper model (downloaded layout) AND next to the
        # binary (bundled layout), so it's found in dev, AppImage, and installer.
        for base in (Path(self._model).resolve().parent, Path(self._bin).resolve().parent):
            for cand in sorted(base.glob("ggml-silero-*.bin")):
                return str(cand)
        return None

    @property
    def inference_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/inference"

    def _use_gpu(self) -> tuple[bool, str]:
        if self._accel == "cpu":
            return False, "cpu (forced)"
        if self._accel == "gpu":
            return True, "gpu (forced)"
        use, why = detect_gpu()
        return use, f"auto → {'gpu' if use else 'cpu'} ({why})"

    def start(self) -> None:
        if not Path(self._model).is_file():
            raise LocalWhisperServerError(
                f"local whisper model not found: {self._model}. "
                "Set [transcribe].local_gguf to a whisper.cpp ggml model, or "
                "switch [transcribe].engine to 'cloud'."
            )
        import shutil
        if not (Path(self._bin).is_file() or shutil.which(self._bin)):
            raise LocalWhisperServerError(
                f"whisper-server binary not found: {self._bin}. Install the "
                "bundled Vulkan build or set [transcribe].local_bin."
            )

        use_gpu, reason = self._use_gpu()
        log().info("transcribe acceleration: %s", reason)
        cmd = [
            branded_exec_path(self._bin, "quobi-speech"), "-m", self._model,
            "--host", "127.0.0.1", "--port", str(self._port),
            "--inference-path", "/inference",
            "-l", self._language,
        ]
        if not use_gpu:
            cmd += ["-ng"]                 # disable GPU → CPU path
        if self._threads > 0:
            cmd += ["-t", str(self._threads)]

        # Voice Activity Detection: drop non-speech/silence before the model
        # ever sees it. This is the real fix for Whisper hallucinating
        # "Thank you" / "thanks for watching" on trailing silence (it was
        # trained on YouTube tails). -sns also suppresses [music]/[applause]-
        # style non-speech tokens. Degrades gracefully if the VAD model is
        # absent (logs and runs without it).
        if self._vad:
            vad_path = self._resolve_vad_model()
            if vad_path:
                cmd += ["--vad", "--vad-model", vad_path, "-sns"]
                log().info("transcribe VAD: on (%s)", Path(vad_path).name)
            else:
                log().warning(
                    "transcribe VAD requested but no ggml-silero-*.bin found "
                    "next to %s — running WITHOUT VAD (silence hallucinations "
                    "may appear)", Path(self._model).name,
                )

        # Co-located ggml .so's (the Vulkan/CPU backends) live next to the
        # binary in the bundled layout — make sure the loader finds them
        # regardless of the binary's build-time RUNPATH.
        env = os.environ.copy()
        bin_dir = str(Path(self._bin).resolve().parent)
        env["LD_LIBRARY_PATH"] = bin_dir + (
            os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
        )

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = self._log_path.open("w")
        log().info("starting local whisper model: %s (port %d, %s)",
                   Path(self._model).name, self._port, "gpu" if use_gpu else "cpu")
        try:
            self._proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env,
                                          creationflags=_NO_WINDOW)
        except OSError as e:
            raise LocalWhisperServerError(f"could not launch whisper-server: {e}") from e

        self._wait_ready()

    def _wait_ready(self) -> None:
        # whisper-server loads the model BEFORE it binds the listening socket,
        # so a successful TCP connect means the model is loaded and ready.
        for _ in range(self._timeout):
            if self._proc is not None and self._proc.poll() is not None:
                raise LocalWhisperServerError(
                    f"whisper-server exited early (code {self._proc.returncode}); "
                    f"see {self._log_path}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=2):
                    log().info("local whisper model ready on %s", self.inference_url)
                    return
            except OSError:
                pass
            time.sleep(1)
        self.stop()
        raise LocalWhisperServerError(
            f"whisper-server did not become ready within {self._timeout}s; "
            f"see {self._log_path}"
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        log().info("stopping local whisper model")
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
