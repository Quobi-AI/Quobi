# Building & releasing Quobi

The single source of truth for how to build Quobi on **Linux** and **Windows**.
Same architecture on both OSes -- only the toolchain and the final installer
format differ.

> TL;DR: a release = **4 components** staged into a bundle dir, then one
> `tauri build` that wraps them. Linux → AppImage, Windows → NSIS installer.

---

## 1. What's in a release

Quobi is one engine + one GUI + two GPU sidecars. A release bundles all four:

| Component | What it is | Built by |
|-----------|-----------|----------|
| **Quobi GUI** | the desktop app (`quobi` / `Quobi.exe`) | Tauri (Rust + React) |
| **daemon** | the dictation engine (`voice-type` / `voice-type.exe`) | PyInstaller (Python) |
| **llama-server** | cleanup model runner (Quill GGUF) | llama.cpp, **Vulkan** build |

Speech-to-text runs **NVIDIA Parakeet TDT 0.6B v3** (multilingual, 25 languages)
in-process via **sherpa-onnx** (ONNX Runtime, CPU): no sidecar, no port, no CUDA.
It's 20x+ faster than real-time even single-threaded, so speech never needs the
GPU on any hardware and the GPU stays free for cleanup. sherpa-onnx is a pip
dependency of the daemon, so there's nothing extra to build for STT; the prebuilt
ONNX bundle is **downloaded on first run** (see `docs/PARAKEET.md`). There is no
Whisper anywhere in the local path.

The llama-server cleanup sidecar uses the **GGML Vulkan** backend for GPU accel
on *any* GPU (NVIDIA/AMD/Intel) with **no CUDA / no driver installs**, CPU fallback.
The models (Quill cleanup GGUF, Parakeet ONNX) are **downloaded on first run**,
not shipped, except where a fat installer chooses to bake them in.

### Pinned versions (keep Linux & Windows in lockstep)
- llama.cpp Vulkan release **`b9474`**
- `sherpa-onnx==1.13.3` (STT; bundles its own onnxruntime, see `voice-type/requirements.txt`)

---

## 2. Repo layout that matters

```
voice-type/                     # the Python daemon (engine)
  Makefile                      #   make build  -> dist/voice-type (PyInstaller)
  voice-type.spec               #   PyInstaller spec
  requirements.txt              #   pinned deps
voice-type-desktop/             # the Tauri GUI
  src-tauri/
    tauri.linux.conf.json       #   AppImage bundle + resource map (linuxbundle/)
    tauri.windows.conf.json     #   NSIS bundle + resource map (winbundle/)
    linuxbundle/                #   GITIGNORED staging: daemon/ llama/
    winbundle/                  #   GITIGNORED staging: daemon/ llama/
```

`linuxbundle/` and `winbundle/` are **gitignored** -- they hold built binaries,
staged fresh at build time, never committed.

---

## 3. Linux pipeline

### One-time prerequisites
```bash
# Vulkan runtime (for the llama-server cleanup sidecar) + Tauri runtime libs
sudo pacman -S --needed vulkan-icd-loader webkit2gtk-4.1 libappindicator-gtk3 librsvg
# toolchains: rustup (cargo), bun, python3 (+ venv)
```
(Debian/Fedora equivalents are in `voice-type-desktop/README.md`.)

### Build the components
```bash
# (a) llama-server (Vulkan): one-time, prebuilt from llama.cpp release b9474.
#     Place llama-server + its libggml*.so into:
#       voice-type-desktop/src-tauri/linuxbundle/llama/
#     (and ~/.local/share/voice-type/llama-vulkan/llama-b9474/ for local dev runs)
#     STT (Parakeet) is an in-process pip dependency, so there's nothing to build.

# (b) daemon (PyInstaller) -> dist/voice-type, then stage it
cd voice-type && make build
cp dist/voice-type ../voice-type-desktop/src-tauri/linuxbundle/daemon/voice-type
cd ..
```

### Build the app
```bash
cd voice-type-desktop
rm -rf /tmp/appimage_extracted_*   # clear stale linuxdeploy extractions (avoids intermittent "failed to run linuxdeploy")
APPIMAGE_EXTRACT_AND_RUN=1 NO_STRIP=1 bun run tauri build      # NO_STRIP=1 + APPIMAGE_EXTRACT_AND_RUN=1 avoid linuxdeploy strip + FUSE-mount failures
# => src-tauri/target/release/quobi                       (the GUI binary; named via tauri.conf.json mainBinaryName)
# => src-tauri/target/release/bundle/appimage/Quobi_0.1.0_amd64.AppImage  (all-in-one)
```

### Two ways to deploy on Linux
- **All-in-one AppImage** (distribution): the `.AppImage` above embeds the
  daemon + llama via `tauri.linux.conf.json` resources. Self-contained.
- **Local dev install** (what this machine runs day-to-day): loose binaries.
  ```bash
  install -m755 voice-type/dist/voice-type ~/.local/bin/voice-type
  install -m755 voice-type-desktop/src-tauri/target/release/quobi ~/.local/bin/quobi
  cd voice-type && make install        # installs the autostart entry (quobi-daemon.desktop)
  ```
  The autostart entry creates the systemd user unit
  **`app-quobi\x2ddaemon@autostart.service`** (this exact name is what the GUI's
  start/stop buttons target -- see `DAEMON_UNIT` in `daemonctl.rs`; if you rename
  the `.desktop`, update that constant in lockstep). Restart the daemon with:
  ```bash
  systemctl --user restart 'app-quobi\x2ddaemon@autostart.service'
  ```
  The Vulkan llama-server lives in `~/.local/share/voice-type/llama-vulkan/`;
  models (Quill GGUF, Parakeet ONNX) download to
  `~/.local/share/voice-type/models/` via the Settings UI.

---

## 4. Windows pipeline

Same shape as Linux -- **still Tauri**, just MSVC + an NSIS installer instead of
an AppImage. Done on a Windows build machine (or VM); no GPU needed to *build*
(Vulkan shaders compile to SPIR-V via glslc; GPU only matters at runtime).

### The easy path -- scripted, incremental

Two scripts automate this. Both are **incremental** (reuse the cargo `target/`,
bun `node_modules/`, and Python `.venv` -- only reinstalling pip deps when
`requirements.txt` changes), so the *first* build is ~15-20 min cold but every
build after is a couple of minutes.

- **On the Windows box** -- `src-tauri/scripts/build-windows.ps1` builds the
  daemon + GUI and (with `-Install`) hot-swaps them into the installed app,
  making `.bak` backups:
  ```powershell
  powershell -ExecutionPolicy Bypass -File build-windows.ps1 -Install
  # -Component gui|daemon|both   -Installer (full NSIS instead of a bare exe)
  ```
- **From the Linux dev host** -- `src-tauri/scripts/build-windows-remote.sh`
  snapshots current `git HEAD`, syncs it to the VM, and runs the above over SSH:
  ```bash
  VM_HOST=<ip> VM_USER=<user> SSHPASS=<pw> \
    voice-type-desktop/src-tauri/scripts/build-windows-remote.sh -Install
  ```
  (Set up key auth and drop `SSHPASS` if you prefer.)

The manual steps below are what those scripts run, for reference / first-time setup.

### One-time prerequisites (winget)
```powershell
winget install -e --id Microsoft.VisualStudio.2022.BuildTools   # + "Desktop development with C++" (Rust MSVC linker)
winget install -e --id Git.Git
# plus: Rust (rustup), Bun, Python 3.12  (Tauri uses WebView2 -- built into Windows)
```

### Build the components
```powershell
# (a) llama-server.exe (Vulkan): prebuilt from llama.cpp b9474 win-vulkan release
#     -> winbundle/llama/. STT (Parakeet) is an in-process pip dependency, so
#     there's nothing to build for it.

# (b) daemon (voice-type.exe) via PyInstaller:
cd voice-type
py -m venv .venv ; .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm voice-type.spec     # needs ..\shared on disk
copy dist\voice-type.exe ..\voice-type-desktop\src-tauri\winbundle\daemon\voice-type.exe
```

### Build the installer
```powershell
cd voice-type-desktop
bun install
bun run tauri build      # NSIS target via tauri.windows.conf.json (embeds winbundle/)
# => src-tauri\target\release\bundle\nsis\Quobi_0.1.0_x64-setup.exe
```

> **Legacy note:** an older Inno Setup script (`quobi.iss`, ~2.87 GB fat
> installer with models baked in) exists from an earlier experiment. It is **not**
> the pipeline going forward -- Windows ships via the Tauri/NSIS path above, same
> as Linux ships via Tauri/AppImage. Ignore `quobi.iss` unless deliberately
> producing a model-bundled offline installer.

See `voice-type/WINDOWS.md` for Windows-specific daemon bring-up notes
(VC++ redist, pynput hotkey, spec hidden-imports).

---

## 5. Release checklist

1. Bump the pinned refs in §1 if updating llama.cpp / sherpa-onnx.
2. Rebuild **all four** components for the target OS (don't ship a stale daemon --
   a frozen binary won't have new Python code until rebuilt).
3. Stage them into `linuxbundle/` or `winbundle/`.
4. `bun run tauri build` (Linux: `NO_STRIP=1`).
5. Smoke-test: launch the GUI, confirm the daemon autostarts, dictate once
   (first transcription compiles Vulkan shaders ~5 s, then sub-second).
6. Keep the daemon's systemd unit name and `DAEMON_UNIT` in `daemonctl.rs` in
   sync if the autostart `.desktop` filename ever changes.
