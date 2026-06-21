# Building Quobi from source (any Linux distro)

The prebuilt AppImage on the Releases page is built on a current rolling distro,
so its GUI binary needs **glibc 2.39 or newer**. That covers Ubuntu 24.04 LTS+,
Fedora 40+, Debian 13+, Arch, and recent openSUSE. If you're on something older
(**Ubuntu 22.04 LTS, Debian 12, Linux Mint 21, RHEL/Rocky/Alma 8-9**), the
AppImage will fail with a `GLIBC_2.39 not found` error. Building from source on
your own machine links against *your* glibc, so it just works.

(The daemon itself only needs glibc 2.14; the floor comes from the Tauri/Rust GUI.)

This builds the two pieces: the **daemon** (the dictation engine, Python frozen
with PyInstaller) and the **desktop app** (Tauri/Rust GUI), then bundles them into an
AppImage. Speech (Parakeet) is a pip dependency, so there's nothing to compile
for it; only the cleanup model's `llama-server` is a prebuilt binary you fetch.

## 1. Toolchains (all distros)

- **Rust**: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- **Bun**: `curl -fsSL https://bun.sh/install | bash`
- **Python 3.10+** with `venv` and `pip` (usually already present)

## 2. System dependencies (pick your distro)

These are the Tauri GUI build deps + Quobi's runtime tools (clipboard/keystroke
injection + optional Vulkan for GPU cleanup).

**Debian / Ubuntu / Mint:**
```bash
sudo apt update && sudo apt install -y \
  libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev \
  python3-venv ydotool wl-clipboard mesa-vulkan-drivers
```

**Fedora:**
```bash
sudo dnf group install -y c-development
sudo dnf install -y webkit2gtk4.1-devel openssl-devel curl wget file \
  libappindicator-gtk3-devel librsvg2-devel \
  python3-virtualenv ydotool wl-clipboard vulkan-loader
```

**RHEL / Rocky / Alma 8-9** (needs EPEL + CodeReady Builder for webkit):
```bash
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb   # 'powertools' on 8
sudo dnf install -y webkit2gtk4.1-devel openssl-devel curl wget file \
  libappindicator-gtk3-devel librsvg2-devel python3 gcc gcc-c++ make \
  ydotool wl-clipboard vulkan-loader
```

**Arch / Manjaro:**
```bash
sudo pacman -S --needed webkit2gtk-4.1 base-devel curl wget file openssl \
  libappindicator-gtk3 librsvg python ydotool wl-clipboard vulkan-icd-loader
```

> On **X11** instead of Wayland, swap `ydotool wl-clipboard` for `xdotool xclip`.

## 3. Build

```bash
git clone https://github.com/Quobi-AI/Quobi.git
cd Quobi

# (a) the dictation daemon -> voice-type/dist/voice-type
cd voice-type && make build && cd ..

# (b) the cleanup model runner (prebuilt llama.cpp Vulkan, b9474). Download the
#     Linux Vulkan build for your arch from https://github.com/ggml-org/llama.cpp/releases
#     and stage llama-server + its lib*.so into the bundle dir:
mkdir -p voice-type-desktop/src-tauri/linuxbundle/llama
#   ... unzip the release and copy llama-server + libggml*.so / libllama*.so there ...

# (c) the desktop app + AppImage (NO_STRIP keeps the bundled daemon intact)
cd voice-type-desktop
APPIMAGE_EXTRACT_AND_RUN=1 NO_STRIP=1 bun run tauri build
# -> src-tauri/target/release/bundle/appimage/Quobi_0.1.0_amd64.AppImage
```

Don't need the AppImage? `bun run tauri build --no-bundle` just produces the
`quobi` GUI binary, and `voice-type/dist/voice-type --daemon` runs the engine.

## 4. One-time runtime setup

Independent of distro, the hotkey grab and the Wayland keystroke/paste path need
access to the virtual-input device:

```bash
# let your user open /dev/uinput (for the evdev hotkey grab + ydotool paste)
sudo usermod -aG input "$USER"
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
# log out and back in for the group change to take effect
```

On Wayland, also make sure `ydotoold` is running (the app starts it, or
`systemctl --user enable --now ydotool` on distros that ship the unit).

## 5. Notes

- **GPU cleanup is optional.** With a Vulkan driver present, the Quill cleanup
  model offloads to any GPU (NVIDIA/AMD/Intel, no CUDA). Without one it runs on
  CPU automatically, just slower.
- **Models download on first run** from the Settings panel (Parakeet speech model
  + a Quill cleanup model); nothing model-shaped is baked into the build.
- The full maintainer build/release pipeline (Windows included) is in
  [BUILD.md](../BUILD.md).
