"""Config: TOML for behavior, env for secrets. Default config is written on first run."""
from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = """\
# voice-type configuration.
# Secrets (GROQ_API_KEY) live in .env, not here.

[hotkey]
# Any pynput Key attribute (ctrl_r, alt_r, f9, menu, pause, scroll_lock, ...)
# or "char:`" for a character key. Avoid generic ctrl/alt/shift —
# those fire on either side and conflict with normal typing.
key = "ctrl_r"
# "auto" picks evdev on Wayland, pynput on X11.
# "pynput" works on X11; "evdev" works everywhere but needs the user
# in the `input` group (sudo usermod -aG input $USER, then re-login).
backend = "auto"
# "hold" — record while held, release to send.
# "toggle" — tap to start, tap again to stop.
mode = "hold"

[audio]
sample_rate = 16000
channels = 1
# Ignore accidental taps shorter than this.
min_recording_sec = 0.3
# During long utterances, emit chunks of this length to Whisper in parallel.
# Smaller = lower stop-to-text latency on long recordings, but more requests
# and slightly worse seam-quality (the LLM cleanup pass fixes most seams).
chunk_sec = 6.0

[transcribe]
# "local" runs faster-whisper on this machine: free, offline, your audio
# never leaves the device. "cloud" uses an OpenAI-compatible Whisper endpoint.
engine = "local"
# Local model: tiny | base | small | medium | large-v3.
# Bigger = more accurate, slower, more RAM (base ~0.5GB, small ~1GB).
local_model = "small"
local_device = "cpu"          # cpu | cuda | auto
local_compute_type = "int8"   # int8 (fast on CPU) | float16 (GPU) | auto
# --- whisper.cpp Vulkan backend (preferred local STT) ---
# When local_gguf points at a whisper.cpp ggml model, the daemon runs the
# bundled whisper-server instead of faster-whisper. Built with the GGML Vulkan
# backend, it GPU-accelerates on ANY GPU (NVIDIA/AMD/Intel) with NO CUDA — the
# same zero-dependency stack the cleanup model uses. Empty = use faster-whisper.
local_gguf = ""                # path to ggml-large-v3-turbo.bin (whisper.cpp)
local_bin = "whisper-server"   # whisper.cpp server (on PATH, or an absolute path)
local_accel = "auto"           # auto (gpu if present) | gpu | cpu
local_port = 8090              # localhost port for the whisper-server sidecar
local_threads = 0              # CPU threads (0 = whisper.cpp default)
# Voice Activity Detection: drop silence/non-speech before transcription so
# Whisper can't hallucinate "Thank you" / "thanks for watching" on trailing
# silence. Needs a Silero VAD ggml model; auto-detected as ggml-silero-*.bin
# next to local_gguf if vad_model is empty. Falls back to no-VAD if absent.
vad = true
vad_model = ""                 # explicit path, or "" to auto-detect
# --- cloud fallback (only used when engine = "cloud") ---
# OpenAI-compatible base: Groq, Together (https://api.together.xyz/v1),
# Fireworks, DeepInfra (https://api.deepinfra.com/v1/openai), OpenAI.
base_url = "https://api.groq.com/openai/v1"
model = "whisper-large-v3-turbo"
# ISO-639-1 (e.g. "en"). Empty = auto-detect.
language = ""
# Bias Whisper toward your jargon — names, product terms, acronyms.
prompt = ""
timeout_sec = 30
temperature = 0.0

[cleanup]
# LLM polish pass: strips fillers, fixes punctuation, formats lists, repairs
# chunk seams. Off ⇒ raw Whisper output is typed verbatim.
enabled = true
# OpenAI-compatible base URL for the cleanup LLM. Default Groq. Swap to any
# OpenAI-compatible provider without code changes, e.g.:
#   Cerebras  https://api.cerebras.ai/v1
#   Together  https://api.together.xyz/v1
#   Fireworks https://api.fireworks.ai/inference/v1
#   DeepInfra https://api.deepinfra.com/v1/openai
#   OpenRouter https://openrouter.ai/api/v1
# (Set GROQ_API_KEY in .env to the chosen provider's key.)
base_url = "https://api.groq.com/openai/v1"
# Tiered models. `tier` picks which one runs:
#   "free" -> model_free   (cheap; for free-tier users)
#   "paid" -> model_paid   (best instruction-following; for subscribers)
# Flip `tier` to A/B test the cheap model on your own dictation.
tier = "free"
model_free = "llama-3.1-8b-instant"
model_paid = "llama-3.3-70b-versatile"
# Legacy override: set this to force a specific model regardless of tier.
# Leave empty to use the tiered selection above.
model = ""
timeout_sec = 15
max_tokens = 2048
temperature = 0.2
# Cleanup engine:
#   "cloud" — use base_url above (any OpenAI-compatible provider). [default]
#   "local" — run a bundled llama.cpp server on a fine-tuned GGUF, fully
#             offline. base_url/model/key above are ignored. Requires a
#             llama-server binary (>= b9180 for qwen3.5) and a .gguf file.
engine = "cloud"
# Only used when engine = "local":
local_model = ""               # absolute path to the .gguf cleanup model
local_bin = "llama-server"     # llama.cpp server (on PATH, or an absolute path)
local_port = 8080
# Acceleration: "auto" detects a usable GPU and offloads to it, else CPU.
#   "gpu" forces GPU; "cpu" forces CPU. (Needs a GPU-capable llama-server.)
local_accel = "auto"
local_ngl = 0                  # advanced: force N GPU layers (0 = use local_accel)
local_ctx = 4096
local_threads = 0              # 0 = let llama.cpp choose

[personalize]
# style: how much the cleanup model may edit.
#   "verbatim"  — your exact words; remove filler, fix punctuation/caps only
#   "tidy"      — light grammar fixes, merge fragments, keep your meaning
#   "formatted" — tidy + bullet lists / paragraphs when you list things
style = "tidy"
# corrections: deterministic find-replace, one rule per line as
#   "Target: variant1, variant2". Fixes mis-heard names/terms no matter how
#   Whisper spelled them. e.g.  Rabih: Robbie, Robby, Rabia, Rabiah
corrections = ""

[output]
# "paste"     — sets clipboard + Ctrl+V; original clipboard is restored
#               after the paste (reliable in Electron, IME, terminals).
# "clipboard" — sets clipboard only and stops. You paste manually with
#               Ctrl+V when ready. The clipboard is NOT restored.
# "type"      — synthesizes keystrokes one at a time (fallback only).
mode = "paste"
# "auto" picks wtype/wl-copy on Wayland, xdotool/xclip on X11.
backend = "auto"
# Wait this long after hotkey release before typing/pasting, so the
# modifier key has fully come up.
release_delay_sec = 0.08
# When using mode = "paste":
#   true  — leave the dictated text in the clipboard after pasting, so you
#           can paste it again elsewhere (Wispr Flow's behavior).
#   false — restore your previous clipboard contents after the paste, so
#           pasting elsewhere later gives you back what was on your clipboard
#           before you dictated.
preserve_clipboard = true
# How long to keep our text in the clipboard before restoring previous
# contents — only used when preserve_clipboard = false.
clipboard_restore_delay_sec = 1.0
# When the active window is a terminal, use Ctrl+Shift+V (which terminals
# treat as paste) instead of Ctrl+V (which they treat as a literal ^V).
# X11: detects via xprop WM_CLASS.
# Wayland: detects KDE Konsole / Yakuake via their DBus interfaces.
# Cannot see INTO remote-desktop windows — use force_terminal_paste for that.
terminal_paste_aware = true
# When true, ALWAYS use Ctrl+Shift+V regardless of window detection. Useful
# when you're remoting into a session and pasting into the remote terminal —
# the workstation's voice-type can't see what's inside the FreeRDP window, so
# flip this on while remoting in to a terminal-heavy workflow.
force_terminal_paste = false
# Context-aware capitalization. Before pasting, briefly select-and-copy the
# 4 chars before the cursor, decide whether you're at a sentence start
# (after . ! ? or a newline) or mid-sentence, and lowercase the first letter
# of the cleaned text in the mid-sentence case. Pollutes the clipboard for
# ~100ms and adds ~150ms of latency. Disable if it misbehaves in some app.
smart_capitalize = true

[indicator]
# "tray"  — system tray icon with state-colored dot
# "notify" — desktop notifications only (no tray)
# "off"   — silent (stdout/log only)
mode = "tray"
# Independent toggle for the floating cursor-anchored pill (the Wispr-style
# overlay). Combines with whatever `mode` you pick above. Requires tkinter
# (python3-tkinter / python3-tk / python-tk — see README) — falls back
# gracefully if unavailable.
floating = true
notify_on_error = true
notify_on_success = false

[history]
# Append every successful dictation to a JSONL log so you can audit, search,
# or re-type from history later.
enabled = true
max_entries = 1000

[voice_commands]
# Detect phrases like "scratch that" / "undo that" at the start of an
# utterance and erase the previous paste instead of typing.
scratch_enabled = true

[log]
level = "INFO"
# Log file path. Use ~ for $HOME. Empty string = stderr only.
file = "~/.local/state/voice-type/voice-type.log"
"""


@dataclass
class HotkeyConfig:
    key: str = "ctrl_r"
    backend: str = "auto"
    mode: str = "hold"  # "hold" or "toggle"


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    min_recording_sec: float = 0.3
    chunk_sec: float = 6.0


@dataclass
class TranscribeConfig:
    # "local"  — on-device faster-whisper (free, offline, audio never leaves
    #            the machine). "cloud" — an OpenAI-compatible Whisper endpoint.
    engine: str = "local"
    # Local model: tiny | base | small | medium | large-v3. Bigger = more
    # accurate, slower, more RAM (base ~0.5GB, small ~1GB).
    local_model: str = "small"
    local_device: str = "cpu"        # cpu | cuda | auto
    local_compute_type: str = "int8" # int8 (fast on CPU) | float16 (GPU) | auto
    # whisper.cpp Vulkan backend (preferred local STT). When local_gguf is set,
    # the daemon runs whisper-server (any GPU, no CUDA) instead of faster-whisper.
    local_gguf: str = ""             # path to a whisper.cpp ggml model
    local_bin: str = "whisper-server"
    local_accel: str = "auto"        # auto | gpu | cpu
    local_port: int = 8090
    local_threads: int = 0
    vad: bool = True                 # drop silence so Whisper can't hallucinate on it
    vad_model: str = ""              # Silero VAD ggml path; "" = auto-detect
    # Cloud fallback (engine="cloud"): OpenAI-compatible base + model.
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "whisper-large-v3-turbo"
    language: str = ""
    prompt: str = ""
    timeout_sec: int = 30
    temperature: float = 0.0


@dataclass
class CleanupConfig:
    enabled: bool = True
    # OpenAI-compatible base URL for the cleanup LLM. Default Groq; swap to
    # Cerebras / Together / Fireworks / DeepInfra / OpenRouter (or any
    # OpenAI-compatible endpoint) without code changes.
    base_url: str = "https://api.groq.com/openai/v1"
    # Tiered models. `tier` selects which one is used: "free" or "paid".
    # When the subscription layer exists it will set tier per-user; for now
    # it's a manual switch so you can A/B the cheap model yourself.
    tier: str = "free"
    model_free: str = "llama-3.1-8b-instant"
    model_paid: str = "llama-3.3-70b-versatile"
    # Legacy single-model field. If set to something other than the sentinel
    # it overrides the tiered selection (back-compat with old configs).
    model: str = ""
    timeout_sec: int = 15
    max_tokens: int = 2048
    temperature: float = 0.2
    # Cleanup engine: "cloud" hits base_url (any OpenAI-compatible provider);
    # "local" runs a bundled llama.cpp server on a fine-tuned GGUF — the
    # free/offline tier. When "local", base_url/model/key are ignored.
    engine: str = "cloud"
    local_model: str = ""            # path to the .gguf
    local_bin: str = "llama-server"  # llama.cpp server binary (PATH or absolute)
    local_port: int = 8080
    # Acceleration selector: "auto" (detect a usable GPU, else CPU) | "gpu" |
    # "cpu". Maps to GPU layers at startup. `local_ngl` is an advanced numeric
    # override (0 = let local_accel decide; >0 = force that many GPU layers).
    local_accel: str = "auto"
    local_ngl: int = 0
    local_ctx: int = 4096
    local_threads: int = 0           # 0 = let llama.cpp decide

    def resolved_model(self) -> str:
        # An explicit legacy `model` wins, so existing configs keep working.
        if self.model:
            return self.model
        if self.tier == "paid":
            return self.model_paid
        return self.model_free


@dataclass
class PersonalizeConfig:
    # Cleanup style — selects which editing-latitude block composes the prompt:
    #   "verbatim"  — your exact words, filler + punctuation only
    #   "tidy"      — light grammar fixes, merge fragments, keep your meaning
    #   "formatted" — tidy + bullet lists / paragraphs when you list things
    style: str = "tidy"
    # Deterministic find-replace rules, one per line: "Target: var1, var2".
    # Fixes mis-heard names/terms regardless of what Whisper produced.
    corrections: str = ""


@dataclass
class OutputConfig:
    mode: str = "paste"
    backend: str = "auto"
    release_delay_sec: float = 0.08
    preserve_clipboard: bool = True
    clipboard_restore_delay_sec: float = 1.0
    terminal_paste_aware: bool = True
    force_terminal_paste: bool = False
    smart_capitalize: bool = False  # disabled by default — keystroke peek is too disruptive in arbitrary apps


@dataclass
class IndicatorConfig:
    mode: str = "tray"
    floating: bool = True
    notify_on_error: bool = True
    notify_on_success: bool = False


@dataclass
class HistoryConfig:
    enabled: bool = True
    max_entries: int = 1000


@dataclass
class VoiceCommandsConfig:
    scratch_enabled: bool = True


@dataclass
class LogConfig:
    level: str = "INFO"
    file: str = "~/.local/state/voice-type/voice-type.log"


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    personalize: PersonalizeConfig = field(default_factory=PersonalizeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    indicator: IndicatorConfig = field(default_factory=IndicatorConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    voice_commands: VoiceCommandsConfig = field(default_factory=VoiceCommandsConfig)
    log: LogConfig = field(default_factory=LogConfig)
    groq_api_key: str = ""
    config_path: Path = field(default_factory=lambda: Path("."))


def user_config_dir() -> Path:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return xdg / "voice-type"


def user_data_dir() -> Path:
    xdg = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return xdg / "voice-type"


def models_dir() -> Path:
    """Where cleanup GGUFs live. Drop a .gguf here and it becomes selectable."""
    return user_data_dir() / "models"


def discover_local_models() -> list[Path]:
    """Every .gguf under the models directory (recursive), newest first. Restart
    the app/daemon after dropping a model in and it shows up in the picker."""
    d = models_dir()
    if not d.is_dir():
        return []
    return sorted((p for p in d.rglob("*.gguf") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def set_cleanup_keys(updates: dict) -> None:
    """Persist key=value pairs into the [cleanup] section of config.toml in place,
    preserving comments and every other section. Used by the GUI model picker."""
    cfg_file = user_config_dir() / "config.toml"
    if not cfg_file.exists():
        return
    lines = cfg_file.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_cleanup = False
    seen: set = set()

    def flush_remaining():
        for k, v in updates.items():
            if k not in seen:
                out.append(f'{k} = "{v}"'); seen.add(k)

    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_cleanup:                       # leaving [cleanup]: write leftovers
                flush_remaining()
            in_cleanup = (s == "[cleanup]")
            out.append(line)
            continue
        if in_cleanup:
            m = re.match(r"\s*([A-Za-z0-9_]+)\s*=", line)
            if m and m.group(1) in updates and m.group(1) not in seen:
                k = m.group(1)
                out.append(f'{k} = "{updates[k]}"'); seen.add(k)
                continue
        out.append(line)
    if in_cleanup:                               # [cleanup] was the last section
        flush_remaining()
    cfg_file.write_text("\n".join(out) + "\n", encoding="utf-8")


def _candidate_paths() -> list[Path]:
    user_cfg = user_config_dir() / "config.toml"
    # Frozen binary: the package lives in a PyInstaller temp dir, so there's
    # no useful project-local fallback — XDG is the only sensible home.
    if getattr(sys, "frozen", False):
        return [user_cfg]
    pkg_root = Path(__file__).resolve().parent.parent
    return [pkg_root / "config.toml", user_cfg]


def _find_or_seed() -> Path:
    candidates = _candidate_paths()
    for p in candidates:
        if p.is_file():
            return p
    seed = candidates[0]
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return seed


def _apply(target, section: dict) -> None:
    for fname in target.__dataclass_fields__:
        if fname in section:
            setattr(target, fname, section[fname])


def load() -> Config:
    path = _find_or_seed()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = Config(config_path=path)
    _apply(cfg.hotkey, raw.get("hotkey", {}) or {})
    _apply(cfg.audio, raw.get("audio", {}) or {})
    _apply(cfg.transcribe, raw.get("transcribe", {}) or {})
    _apply(cfg.cleanup, raw.get("cleanup", {}) or {})
    _apply(cfg.personalize, raw.get("personalize", {}) or {})
    _apply(cfg.output, raw.get("output", {}) or {})
    _apply(cfg.indicator, raw.get("indicator", {}) or {})
    _apply(cfg.history, raw.get("history", {}) or {})
    _apply(cfg.voice_commands, raw.get("voice_commands", {}) or {})
    _apply(cfg.log, raw.get("log", {}) or {})
    cfg.groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
    return cfg
