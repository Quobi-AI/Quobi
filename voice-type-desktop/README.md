# voice-type-desktop

The cross-platform GUI for voice-type (Linux / Windows / macOS) — a small
native window showing daemon status and dictation history, with copy + retry.

Built with **Tauri 2** (Rust core) + **React + TypeScript + Tailwind v4**.

## Why this architecture

The Python daemon does the real dictation work (hotkey, audio, Groq, typing).
This app is a **viewer/control panel** that reads the same files the daemon
writes (`~/.config/voice-type/config.toml`, `~/.local/state/voice-type/history.jsonl`).

Security model — **all privileged work is in Rust**:

- File access, reading the Groq API key, the retry network calls, spawning
  the daemon → all in `src-tauri/src/` (memory-safe Rust, small crates.io
  dependency tree).
- The React/TypeScript layer is **pure presentation**, sandboxed in the
  system webview. It can only call the handful of allowlisted Rust commands
  (`get_status`, `get_history`, `retry`, `start_daemon`) — see
  `src-tauri/capabilities/default.json`. Even a compromised npm dependency
  cannot read your API key or files.

## Prerequisites

- **Rust** (rustc/cargo)
- **Node + bun** (or npm)
- **Linux only**: the system webview + tray libs:
  ```bash
  # Arch
  sudo pacman -S --needed webkit2gtk-4.1 libappindicator-gtk3 librsvg
  # Debian/Ubuntu
  sudo apt install libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev
  # Fedora
  sudo dnf install webkit2gtk4.1-devel libappindicator-gtk3-devel librsvg2-devel
  ```
  (Windows uses WebView2, macOS uses WKWebView — both built in.)

## Develop

```bash
cd voice-type-desktop
bun install
bun run tauri dev      # hot-reloading dev window
```

## Build a release binary

```bash
bun run tauri build
# Linux:   src-tauri/target/release/voice-type-desktop  (+ .deb/.rpm/.AppImage)
# Windows: .msi / .exe ;  macOS: .app / .dmg
```

## How it talks to the daemon

| Action | Implementation |
| --- | --- |
| Status panel | Rust reads `config.toml` + `pgrep voice-type` |
| History list | Rust reads `history.jsonl` (newest first) |
| Copy | Tauri clipboard plugin |
| Retry | Rust loads saved WAV, re-runs Groq Whisper + cleanup, rewrites the entry |
| Start daemon | Rust spawns `~/.local/bin/voice-type --daemon` |

Cleanup prompt is loaded from the repo's `shared/cleanup-prompt.txt` (same
file the Python daemon and Android app use). For a shipped build, copy
`shared/` next to the binary.

## Design

"Late-night writing studio" — warm near-black, signal-red accent that pulses
when the daemon is live, bone-white paper text. Instrument Serif display,
Hanken Grotesk UI, JetBrains Mono for technical readouts. Fonts bundled via
`@fontsource` so the app works fully offline.
