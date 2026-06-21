"""Local cleanup model: a llama.cpp `llama-server` sidecar.

The daemon spawns llama-server bound to localhost, which exposes an
OpenAI-compatible /v1/chat/completions endpoint — so the existing Formatter
talks to it with zero changes, just a different base_url. This is how the
"free / fully-offline" tier runs the fine-tuned Qwen3.5 GGUF on-device.

Lifecycle is owned by the daemon: start() on boot, stop() on shutdown. The
server is a managed child process, killed when the daemon exits.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .log import log

# Windows: spawn console-subsystem helpers (nvidia-smi, vulkaninfo, llama-server)
# WITHOUT popping a conhost window. Critical because the daemon is built windowed
# on Windows (see voice-type.spec), so any unsuppressed console child flashes its
# own window. On Linux/macOS the attr is absent → 0, i.e. Popen's default — no-op.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def branded_exec_path(binary: str, brand: str) -> str:
    """Return a path that execs `binary` under the name `brand`, so it shows as
    `brand` (not "llama-server") in ps / top / System Monitor.
    Linux sets a process's `comm` from the basename of the exec'd path, so we
    exec a sibling SYMLINK named `brand` (verified: comm follows the symlink
    name). Co-located ggml libs still resolve (same dir + LD_LIBRARY_PATH).
    Falls back to the real binary on Windows (branded via the PE version
    resource) or on any filesystem error."""
    if sys.platform == "win32":
        return binary
    try:
        real = Path(binary).resolve()
        link = real.parent / brand
        if not (link.is_symlink() and os.readlink(link) == real.name):
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(real.name)  # relative symlink, within the binary's dir
        return str(link)
    except OSError:
        return binary


class LocalLLMError(Exception):
    pass


def detect_gpu(min_free_mb: int = 2000) -> tuple[bool, str]:
    """Best-effort 'is there a GPU worth offloading to' check, for accel=auto.
    Prefers NVIDIA's exact free-VRAM number; falls back to any Vulkan GPU."""
    import shutil
    import subprocess
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
            )
            frees = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
            if frees and max(frees) >= min_free_mb:
                return True, f"nvidia gpu, {max(frees)} MiB free"
            if frees:
                return False, f"nvidia gpu but only {max(frees)} MiB free (< {min_free_mb})"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if shutil.which("vulkaninfo"):
        try:
            out = subprocess.run(["vulkaninfo"], capture_output=True, text=True, timeout=8,
                                 creationflags=_NO_WINDOW)
            txt = (out.stdout or "") + (out.stderr or "")
            if "DISCRETE_GPU" in txt or "INTEGRATED_GPU" in txt:
                return True, "vulkan gpu detected"
        except (OSError, subprocess.SubprocessError):
            pass
    return False, "no usable gpu found"


def resolve_ngl(accel: str, explicit_ngl: int = 0) -> tuple[int, str]:
    """Turn the user-facing accel selector into a concrete GPU-layer count.
    Returns (n_gpu_layers, human-readable reason)."""
    accel = (accel or "auto").lower()
    full = explicit_ngl if explicit_ngl > 0 else 99   # 99 = offload everything
    if accel == "cpu":
        return 0, "cpu (forced)"
    if accel == "gpu":
        return full, "gpu (forced)"
    use, why = detect_gpu()
    return (full, f"auto → gpu ({why})") if use else (0, f"auto → cpu ({why})")


class LocalLLMServer:
    def __init__(
        self,
        binary: str,
        model_path: str,
        port: int = 8080,
        n_gpu_layers: int = 0,
        ctx: int = 4096,
        threads: int = 0,
        startup_timeout: int = 120,
    ) -> None:
        self._bin = binary
        self._model = model_path
        self._port = port
        self._ngl = n_gpu_layers
        self._ctx = ctx
        self._threads = threads
        self._timeout = startup_timeout
        self._proc: subprocess.Popen | None = None
        self._log_path = Path.home() / ".local/state/voice-type/llama-server.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    @property
    def completion_url(self) -> str:
        # Raw completion endpoint — we render ChatML ourselves (thinking off)
        # rather than use the chat/jinja path, which re-enables reasoning.
        return f"http://127.0.0.1:{self._port}/completion"

    def start(self) -> None:
        if not Path(self._model).is_file():
            raise LocalLLMError(
                f"local cleanup model not found: {self._model}. "
                "Set [cleanup].local_model to a .gguf file (download one from "
                "Settings -> Cleanup)."
            )
        import shutil
        if not (Path(self._bin).is_file() or shutil.which(self._bin)):
            raise LocalLLMError(
                f"llama-server binary not found: {self._bin}. Install llama.cpp "
                "(>= b9180 for qwen3.5) or set [cleanup].local_bin."
            )

        # NB: NO --jinja. We POST pre-rendered ChatML to /completion (thinking
        # pre-closed). llama.cpp's jinja/chat path re-enables reasoning for
        # qwen3_5, which would leak chain-of-thought into the user's text.
        cmd = [
            branded_exec_path(self._bin, "quobi-cleanup"), "-m", self._model,
            "--host", "127.0.0.1", "--port", str(self._port),
            "-c", str(self._ctx), "-ngl", str(self._ngl),
        ]
        if self._threads > 0:
            cmd += ["--threads", str(self._threads)]

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = self._log_path.open("w")
        log().info("starting local cleanup model: %s (port %d, ngl %d)",
                   Path(self._model).name, self._port, self._ngl)
        try:
            self._proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                          creationflags=_NO_WINDOW)
        except OSError as e:
            raise LocalLLMError(f"could not launch llama-server: {e}") from e

        self._wait_ready()

    def _wait_ready(self) -> None:
        health = f"http://127.0.0.1:{self._port}/health"
        for _ in range(self._timeout):
            if self._proc is not None and self._proc.poll() is not None:
                raise LocalLLMError(
                    f"llama-server exited early (code {self._proc.returncode}); "
                    f"see {self._log_path}"
                )
            try:
                with urllib.request.urlopen(health, timeout=2) as r:
                    if r.status == 200:
                        log().info("local cleanup model ready on %s", self.base_url)
                        return
            except Exception:
                pass
            time.sleep(1)
        self.stop()
        raise LocalLLMError(
            f"llama-server did not become ready within {self._timeout}s; "
            f"see {self._log_path}"
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        log().info("stopping local cleanup model")
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
