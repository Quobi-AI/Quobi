# voice-type

Hold-to-talk dictation for Linux, packaged as a real desktop app. Hold a
hotkey, speak, release — your words land at the cursor, polished by an LLM,
in well under a second for typical utterances. A Wispr Flow-style local
daemon.

Works on **any Linux distro** (Fedora, Ubuntu/Debian, Arch, openSUSE, Alpine)
and **every major desktop** (KDE Plasma, GNOME, XFCE, Cinnamon, MATE, LXQt,
sway / wlroots, hyprland) — under both **X11 and Wayland**, with the right
backend chosen automatically.

- **Hold-to-talk or toggle** — `[hotkey] mode = "hold"` or `"toggle"`
- **Transcription**: Groq Whisper (`whisper-large-v3-turbo`)
- **Cleanup**: Groq Llama (`llama-3.3-70b-versatile`) — strips "um/uh", fixes
  punctuation, repairs chunk seams, honors voice commands, multi-language
- **Voice editing**: "scratch that", "undo that", "never mind" erase the
  last paste and optionally replace it with the remainder of your utterance
- **Output**: clipboard-paste by default (works in Electron / IME), with
  automatic Ctrl+Shift+V in terminals, and the previous clipboard contents
  restored automatically
- **Sessions**: X11 (`xdotool` + `xclip`) **and** Wayland (`wtype` + `wl-clipboard`),
  auto-detected
- **Streaming**: long utterances chunk-emit during recording and transcribe in
  parallel; stop-to-text latency stays flat regardless of recording length
- **UI**: cursor-anchored floating pill (recording state + live mic level)
  **plus** a system-tray status dot, plus desktop notifications on errors
- **Mic-mute detection**: notifies if you spoke but no audio was captured
- **History**: every dictation logged to `~/.local/state/voice-type/history.jsonl`

Shipped as a **single self-contained binary** — no Python, no venv, no
`requirements.txt` to think about on the user side. Build once, install with
one command, overwrite to update.

---

## Quick install

```bash
cd voice-type
./install.sh
```

`install.sh` detects your distro and session, checks dependencies, prints the
exact install command if anything is missing, then builds the binary and
installs it. You don't have to know which packages to grab — it tells you.

### 1. System dependencies (one-time, by distro)

Pick the row that matches your distro. Build-time deps (`python3`,
`python3-tkinter`, `make`) plus the runtime deps for your session.

| Distro             | X11 session                                                                            | Wayland (KDE / wlroots / sway / hyprland)                                              | Wayland (GNOME)                                                                       |
| ---                | ---                                                                                    | ---                                                                                    | ---                                                                                   |
| Fedora             | `sudo dnf install xdotool xclip xorg-x11-utils libnotify make python3 python3-tkinter` | `sudo dnf install wtype wl-clipboard libnotify make python3 python3-tkinter`           | `sudo dnf install ydotool wl-clipboard libnotify make python3 python3-tkinter`        |
| Ubuntu / Debian    | `sudo apt install xdotool xclip x11-utils libnotify-bin make python3 python3-tk`       | `sudo apt install wtype wl-clipboard libnotify-bin make python3 python3-tk`            | `sudo apt install ydotool wl-clipboard libnotify-bin make python3 python3-tk`         |
| Arch / Manjaro     | `sudo pacman -S xdotool xclip xorg-xprop libnotify make python tk`                     | `sudo pacman -S wtype wl-clipboard libnotify make python tk`                           | `sudo pacman -S ydotool wl-clipboard libnotify make python tk`                        |
| openSUSE           | `sudo zypper install xdotool xclip xorg-x11-utils libnotify-tools make python3 python3-tk` | `sudo zypper install wtype wl-clipboard libnotify-tools make python3 python3-tk`   | `sudo zypper install ydotool wl-clipboard libnotify-tools make python3 python3-tk`    |
| Alpine             | `sudo apk add xdotool xclip xprop libnotify make python3 python3-tkinter`              | `sudo apk add wtype wl-clipboard libnotify make python3 python3-tkinter`               | `sudo apk add ydotool wl-clipboard libnotify make python3 python3-tkinter`            |

- `xprop` (`xorg-x11-utils` / `x11-utils` / `xorg-xprop`) is **optional** — it
  enables auto-switching to Ctrl+Shift+V in terminal windows. Skip if you
  don't care.
- `python3-tk[inter]` is **optional but recommended** — it powers the
  floating overlay. Without it the daemon still works, just without the pill.

### Why three Wayland columns?

GNOME's compositor (Mutter) doesn't implement the
`virtual-keyboard-unstable-v1` Wayland protocol that `wtype` uses, so
keystroke synthesis via `wtype` is a non-starter on GNOME Wayland. The
fallback is **`ydotool`**, which goes through `/dev/uinput` and works on every
compositor. voice-type auto-picks `ydotool` when it detects
`XDG_CURRENT_DESKTOP=GNOME` + Wayland, falling back to `wtype` if that fails.

**One extra step for `ydotool`:** the daemon needs to be running with a
socket reachable to your user. Arch's `ydotool` package (and some others)
ships only the binaries, not a systemd unit. voice-type ships its own:

```bash
make ydotool-setup
```

That installs `/etc/systemd/system/ydotoold.service` (runs `ydotoold` as
root with socket `/tmp/.ydotool_socket` mode `0666`), enables it, and
verifies the socket exists. After that:

```bash
make restart
```

If your distro *does* ship a unit (Fedora, Ubuntu's newer packages), the
existing one is fine — just enable it instead:

```bash
sudo systemctl enable --now ydotoold
ls -la /tmp/.ydotool_socket   # confirm the socket exists and is accessible
```

### 2. KDE / GNOME / XFCE / etc. — what to expect

| DE / compositor          | Tray icon                | Floating overlay   | Hotkey | Notifications |
| ---                      | ---                      | ---                | ---    | ---           |
| **KDE Plasma** (X11/Wayland)   | ✓ native SNI            | ✓                  | ✓      | ✓             |
| **GNOME** (X11/Wayland)        | needs [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/) | ✓ (via XWayland on Wayland) | ✓ (evdev on Wayland) | ✓ |
| **XFCE / MATE / Cinnamon** | ✓ native                | ✓                  | ✓      | ✓             |
| **LXQt / LXDE**          | ✓ native                | ✓                  | ✓      | ✓             |
| **sway / hyprland / wlroots** | depends on bar (waybar SNI: ✓) | ✓ via XWayland | ✓ evdev | ✓        |

If your tray icon doesn't appear (most likely on GNOME without the
extension), the **floating overlay** and **desktop notifications** still
give you complete state visibility — you just lose the persistent menu.



### 2. Build & install

```bash
cd voice-type
./install.sh        # or: make install
```

That's it. The script:

- creates a build venv at `.venv/` (only used to run PyInstaller)
- bundles a single `voice-type` binary (~35MB, no Python required at runtime)
- copies it to `~/.local/bin/voice-type`
- installs a KDE menu entry, icon, and **autostart** entry so it launches on login
- migrates your project-root `.env` to `~/.config/voice-type/.env` if present

After this, `voice-type` is a normal app: it appears in the KDE menu, it
autostarts on login, and the tray icon is your control surface.

### 3. Set the API key (one-time, if not already)

```bash
echo 'GROQ_API_KEY=gsk_...' > ~/.config/voice-type/.env
chmod 600 ~/.config/voice-type/.env
```

### 4. Launch

`voice-type` from anywhere, or pick it from the KDE menu, or just log out
and back in for autostart.

---

## Updating

After you edit code:

```bash
make install      # rebuilds the binary and overwrites the installed copy
make restart      # kills the running daemon and relaunches the new one
```

`make install` is idempotent — re-running it overwrites everything in place.
`make uninstall` removes the binary and entries but leaves your config and
`.env` alone at `~/.config/voice-type/`.

---

## Files this app touches

| Path                                                      | Purpose                                            |
| ---                                                       | ---                                                |
| `~/.local/bin/voice-type`                                 | the binary                                         |
| `~/.local/share/applications/voice-type.desktop`          | KDE app entry                                      |
| `~/.local/share/icons/hicolor/scalable/apps/voice-type.svg` | app icon                                         |
| `~/.config/autostart/voice-type.desktop`                  | KDE autostart entry                                |
| `~/.config/voice-type/config.toml`                        | behavior config (seeded on first run)              |
| `~/.config/voice-type/.env`                               | `GROQ_API_KEY=...` (chmod 600)                     |
| `~/.local/state/voice-type/voice-type.log`                | rotating log (512KB × 3)                           |

No system paths touched. Nothing under `/usr`. `make uninstall` removes
everything except `~/.config/voice-type/` and `~/.local/state/voice-type/`.

---

## Configuration (`~/.config/voice-type/config.toml`)

Created on first run. Tweak in place, then `make restart` (or just log
out/in). Highlights:

| Section            | Key                                | What it does                                                    |
| ---                | ---                                | ---                                                             |
| `[hotkey]`         | `key`                              | `ctrl_r`, `alt_r`, `f9`, `menu`, `pause`, `scroll_lock`, `char:\`` |
|                    | `backend`                          | `auto` / `pynput` (X11) / `evdev` (Wayland or both)             |
|                    | `mode`                             | `hold` (default) or `toggle`                                    |
| `[audio]`          | `chunk_sec`                        | parallel-chunk size during long utterances (default 6s)         |
| `[transcribe]`     | `model`, `language`, `prompt`      | Whisper config; `prompt` biases vocab for jargon/names          |
| `[cleanup]`        | `enabled`, `model`                 | LLM polish pass; off ⇒ type raw Whisper output                  |
| `[output]`         | `mode`                             | `paste` (default) or `type`                                     |
|                    | `backend`                          | `auto` / `x11` / `wayland`                                      |
|                    | `terminal_paste_aware`             | auto-switch to Ctrl+Shift+V in terminals (X11 only)             |
| `[indicator]`      | `mode`                             | `tray` / `notify` / `off`                                       |
|                    | `floating`                         | show the cursor-anchored Wispr-style overlay                    |
| `[history]`        | `enabled`, `max_entries`           | append-only JSONL log of every dictation                        |
| `[voice_commands]` | `scratch_enabled`                  | recognize "scratch that" / "undo that" / "never mind"           |
| `[log]`            | `file`                             | log path (`""` ⇒ stderr only)                                   |

---

## Make targets

| Command             | What it does                                                 |
| ---                 | ---                                                          |
| `make` / `make help` | print this summary                                          |
| `make build`        | build the binary → `dist/voice-type`                         |
| `make install`      | build + install user-local + enable autostart                |
| `make reinstall`    | `uninstall && install`                                       |
| `make restart`      | kill running daemon and relaunch the installed binary        |
| `make uninstall`    | remove the installation (config kept)                        |
| `make dev`          | run from source via venv (fast iteration, no PyInstaller)    |
| `make clean`        | remove `build/`, `dist/`, `__pycache__`                      |
| `make distclean`    | also remove `.venv/`                                         |

---

## Wayland hotkey backend

`pynput` only sees XWayland-hosted windows on Wayland, so on a Wayland
session voice-type auto-switches to the `evdev` backend, which reads
keypresses from `/dev/input/event*`. This needs the user in the `input`
group:

```bash
sudo usermod -aG input $USER
# Log out and back in (or `newgrp input`) for the group change to apply.
```

Force a backend by setting `[hotkey] backend = "pynput"` or `"evdev"` in
`config.toml`.

---

## Voice commands

**Punctuation / formatting** (handled by the LLM cleanup, unless used as
ordinary words):

- "new line" / "newline" — literal newline
- "new paragraph" — two newlines
- "comma" / "period" / "question mark" / "exclamation point" / "colon" / "semicolon"
- "open paren" / "close paren", "open quote" / "close quote"
- email/URL spellouts ("john dot doe at gmail dot com" → `john.doe@gmail.com`)

**Editing commands** (handled by the daemon — sends backspaces, not text):

- "scratch that", "delete that", "undo that", "remove that", "forget that",
  "ignore that", "never mind"

Said at the **start** of an utterance, these erase the last paste. Used
mid-sentence (e.g. "send it to Bob, scratch that, send it to Alice") they're
treated as ordinary speech and the LLM cleans them up normally.

Append a replacement: *"scratch that, send it to Alice instead"* will erase
the previous paste and type the part after the comma.

Domain-specific terms (product names, acronyms) belong in `[transcribe]
prompt` so Whisper recognizes them in the first place.

## History

Every successful dictation is appended to
`~/.local/state/voice-type/history.jsonl`. One JSON object per line:

```json
{"ts":"2026-05-19T12:34:56+0000","kind":"dictation","duration":3.45,"raw":"send a quick note","cleaned":"Send a quick note."}
```

Use `jq` to query it:

```bash
# Today's dictations
jq -r 'select(.ts | startswith("2026-05-19")) | .cleaned' \
   ~/.local/state/voice-type/history.jsonl

# Total words typed
jq -r '.cleaned' ~/.local/state/voice-type/history.jsonl | wc -w
```

File is capped at `[history] max_entries` (default 1000) — oldest entries
are rolled off automatically.

---

## Known limitations

- **Tray icon on GNOME** requires the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
  Without it you still get the floating overlay + desktop notifications.
- **GNOME Wayland keystroke synthesis** requires `ydotool` + `ydotoold`
  daemon (Mutter doesn't speak `wtype`'s protocol). The daemon needs
  enabling once: `sudo systemctl enable --now ydotoold`.
- **Floating overlay on a pure-Wayland session without XWayland** won't
  render (tkinter goes via XWayland). Falls back silently to tray +
  notifications.
- **Wayland terminal-paste detection** isn't wired up (compositor-specific
  window-class lookup). X11 detects via `xprop` and auto-uses Ctrl+Shift+V
  in known terminal emulators.
- **No VAD** silence-based auto-stop yet (in toggle mode, tap again to stop).
- **No local Whisper fallback** — Groq-only. `transcribe.timeout_sec` bounds
  the wait.
- **"Scratch that"** erases what *this app* last pasted. If you switch focus
  to a different window between the paste and the "scratch that," the
  backspaces go to the new window — there's no way around that without
  window-focus tracking we don't do.
- The build needs `python3`, `python3-tk[inter]`, and `make` at build time
  only. The installed binary has no Python dependency.
