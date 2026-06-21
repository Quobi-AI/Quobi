# Running voice-type / Quobi on Windows

Status: **VALIDATED on a Windows 11 VM (CPU-only, no GPU/mic).** The `.exe` builds
and the daemon runs end-to-end: Windows output backend (clipboard+Ctrl+V paste,
Unicode type, erase) proven interactively; pynput hotkey works; **local Whisper STT
loads in the frozen exe** (`device=cpu compute=int8`). Remaining: a `llama-server.exe`
+ GGUF for local *cleanup*, and a real mic for full audio e2e.

## Bugs found + fixed during VM bring-up
1. **`signal.SIGHUP`** crashed startup on Windows (Unix-only) — now guarded with
   `hasattr(signal, "SIGHUP")` in `__main__.py`.
2. **ctranslate2 / local STT wouldn't load.** The REAL fix:
   - Missing **Microsoft Visual C++ Redistributable** — `ctranslate2.dll` is
     MSVC-compiled (`vcruntime140*.dll`). SYSTEM prereq:
     `winget install -e --id Microsoft.VCRedist.2015+.x64`
   - The ct2 VERSION was a red herring: **`ctranslate2==4.7.2` is CPU-safe**
     (lazy/dlopen CUDA) — pinned in requirements.txt. (4.4.0 is now YANKED from
     PyPI — do not pin it.) `setuptools<81` kept for `pkg_resources`.
   - Spec force-bundles every DLL in the ctranslate2 package dir (sibling deps
     like `libiomp5md.dll` that `collect_all` missed).
   - NOTE: the VM's installed daemon was built with the old 4.4.0 — rebuild with
     4.7.2 to match requirements.txt.
3. **`requirements.txt`** had bare `evdev` (Linux-only) → added `; sys_platform == "linux"`.
4. **Spec** hidden-imports were `_xorg`-only → now platform-aware (`_win32` on Windows).

## Build environment recipe (Windows, CPU)
Python 3.12 + `pip install -r requirements.txt` (now Windows-clean) + the VC++
Redistributable (above) + `pyinstaller>=6.15,<7`. Build: `pyinstaller --clean
--noconfirm voice-type.spec` from the `voice-type/` dir (needs `../shared`). NOTE:
verify the built `.exe` bundles `vcruntime140*.dll` so end users without the VC++
redist can run it; if not, ship the redist alongside or document it as a prereq.

---

## What was done (in source)
- **`voice_type/output_win.py`** — new Windows output backend. Clipboard (CF_UNICODETEXT)
  + **Ctrl+V** paste, Unicode `type_text`, backspace `erase`, all via Win32
  `SendInput`/clipboard through **ctypes — no pywin32 dependency** (clean PyInstaller
  bundle). Reuses the shared `_RestoreScheduler` for clipboard save/restore.
- **`output.py::detect_session()`** now returns `"windows"` on `sys.platform == "win32"`.
- **`output.py::make_backend()`** branches to `WindowsBackend` for `"windows"` (lazy import,
  so `ctypes.windll` never runs on Linux/macOS).
- **Hotkey**: no code change needed — `make_listener(..., "auto")` already picks **pynput**
  for any non-Wayland session, so Windows gets pynput for free (evdev/uinput is Linux-only).

Validated on Linux: both files compile, `output_win` imports without touching `windll`,
`WindowsBackend` fully satisfies the `OutputBackend` interface, Linux detection unchanged.
The Win32 runtime calls (clipboard, SendInput) can only be exercised on Windows.

## On the Windows 11 VM — setup
1. **Python 3.12+** (64-bit). `py -m venv .venv && .venv\Scripts\activate`
2. **Deps**: `pip install -r requirements.txt` (sounddevice, faster-whisper, pynput, numpy,
   tomli/tomllib, etc.). `tkinter` ships with the python.org installer (needed for the overlay).
3. **llama.cpp**: grab a Windows `llama-server.exe` build (CPU/Vulkan is fine on a VM with no
   GPU passthrough; CUDA if the VM has a GPU). Put it on PATH or set `[cleanup] local_bin` to
   its absolute path (e.g. `C:\\tools\\llama\\llama-server.exe`).
4. **A cleanup GGUF**: copy a `qwen35-*-cleanup-Q4_K_M.gguf` over and point `[cleanup] local_model`
   at it. (On a CPU-only VM, prefer the 0.8B/2B for speed.)
5. **Whisper**: faster-whisper downloads its model on first run; CPU `int8` is fine.

## Recommended config for the FIRST boot (isolate the core pipeline)
In `C:\Users\<you>\.config\voice-type\config.toml`:
```toml
[hotkey]
key = "f9"          # use an F-key first — char keys like `grave` may leak one char on Windows
backend = "auto"     # -> pynput on Windows

[cleanup]
engine = "local"
local_bin = "C:\\path\\to\\llama-server.exe"
local_model = "C:\\path\\to\\qwen35-2b-cleanup-Q4_K_M.gguf"
local_accel = "auto"

[output]
mode = "paste"       # clipboard + Ctrl+V (WindowsBackend)
backend = "auto"     # -> windows

[indicator]
mode = "off"         # tray is unverified on Windows; turn off first, enable later
floating = false     # turn the tk overlay on once the core works
```

## What to test on the VM (in order)
1. **Backend selects correctly**: launch `voice-type --daemon`, confirm log says
   `output backend: windows` and `hotkey backend=pynput`.
2. **Paste path**: dictate into Notepad — does clean text land via Ctrl+V? Does the previous
   clipboard get restored (preserve_clipboard=false) / preserved (true)?
3. **Hotkey**: does the F9 hold-to-talk fire press/release? Then try `key = "grave"` and check
   whether a backtick leaks to the focused window (if so, keep F-keys or add Windows pre-erase).
4. **Unicode typing fallback**: set `mode = "type"` and dictate text with an emoji / accent to
   exercise the surrogate-pair path in `type_text`.
5. **erase / scratch-that**: trigger "scratch that" and confirm backspaces erase the prior paste.
6. **Local model**: confirm `llama-server.exe` spawns and `local cleanup model ready` logs.
7. **Overlay**: set `floating = true`, confirm the tk pill renders (or note tk issues).
8. **Tray**: set `indicator.mode = "tray"` last; if it errors, that's the one remaining port item.

## Build the distributable (.exe) — must run ON Windows
PyInstaller can't cross-compile from Linux. On the VM:
```
pip install -r requirements-build.txt
pyinstaller --clean --noconfirm voice-type.spec
```
Then check `voice-type.spec`:
- the `datas=[("../shared", "shared")]` path resolves from the build CWD,
- no Linux-only hidden imports are *required* (evdev import is already guarded behind the
  evdev backend, which Windows never selects),
- add `output_win` to hiddenimports if PyInstaller's static analysis misses the lazy import.

## Known open items / risks (verify on VM, not blockers)
- **Tray indicator** on Windows (pystray vs the current impl) — unverified; `mode=off`/`notify` work.
- **Hotkey key-leak** for character keys (`grave`) under pynput on Windows — F-keys avoid it.
- **Config/log location** is `~/.config` / `~/.local/state` style (works, non-idiomatic; could
  switch to `%APPDATA%` later).
- **GPU**: CTranslate2 + llama.cpp CUDA need the CUDA runtime; CPU/Vulkan path is the safe default
  on a VM.
