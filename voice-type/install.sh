#!/usr/bin/env bash
# voice-type one-shot installer.
# Right-click this file in your file manager → "Run in Terminal/Konsole",
# or run from a terminal. Builds the self-contained binary, installs under
# ~/.local, and enables desktop autostart.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# ─── package-manager detection ─────────────────────────────────────────────
PM=""
INSTALL=""
if   command -v dnf     >/dev/null 2>&1; then PM=dnf;    INSTALL="sudo dnf install -y"
elif command -v apt-get >/dev/null 2>&1; then PM=apt;    INSTALL="sudo apt install -y"
elif command -v pacman  >/dev/null 2>&1; then PM=pacman; INSTALL="sudo pacman -S --needed --noconfirm"
elif command -v zypper  >/dev/null 2>&1; then PM=zypper; INSTALL="sudo zypper install -y"
elif command -v apk     >/dev/null 2>&1; then PM=apk;    INSTALL="sudo apk add"
fi

case "$PM" in
    dnf)
        BUILD_DEPS="python3 python3-tkinter make"
        X11_DEPS="xdotool xclip xorg-x11-utils libnotify"
        WAYLAND_DEPS="wtype wl-clipboard libnotify"
        GNOME_WL_DEPS="ydotool wl-clipboard libnotify"
        ;;
    apt)
        BUILD_DEPS="python3 python3-tk python3-venv make"
        X11_DEPS="xdotool xclip x11-utils libnotify-bin"
        WAYLAND_DEPS="wtype wl-clipboard libnotify-bin"
        GNOME_WL_DEPS="ydotool wl-clipboard libnotify-bin"
        ;;
    pacman)
        BUILD_DEPS="python tk make"
        X11_DEPS="xdotool xclip xorg-xprop libnotify"
        WAYLAND_DEPS="wtype wl-clipboard libnotify"
        GNOME_WL_DEPS="ydotool wl-clipboard libnotify"
        ;;
    zypper)
        BUILD_DEPS="python3 python3-tk make"
        X11_DEPS="xdotool xclip xorg-x11-utils libnotify-tools"
        WAYLAND_DEPS="wtype wl-clipboard libnotify-tools"
        GNOME_WL_DEPS="ydotool wl-clipboard libnotify-tools"
        ;;
    apk)
        BUILD_DEPS="python3 python3-tkinter make"
        X11_DEPS="xdotool xclip xprop libnotify"
        WAYLAND_DEPS="wtype wl-clipboard libnotify"
        GNOME_WL_DEPS="ydotool wl-clipboard libnotify"
        ;;
    *)
        BUILD_DEPS="python3, python3-tkinter (or equivalent), make"
        X11_DEPS="xdotool, xclip, xprop, libnotify"
        WAYLAND_DEPS="wtype, wl-clipboard, libnotify"
        GNOME_WL_DEPS="ydotool, wl-clipboard, libnotify"
        ;;
esac

# ─── session & desktop detection ───────────────────────────────────────────
SESSION="${XDG_SESSION_TYPE:-x11}"
DE="${XDG_CURRENT_DESKTOP:-unknown}"
DE_LOWER="${DE,,}"

echo "voice-type installer"
echo "  package manager: ${PM:-unknown}"
echo "  session:         $SESSION"
echo "  desktop:         $DE"
echo

# ─── prereqs check ─────────────────────────────────────────────────────────
fatal=0

if ! command -v make >/dev/null 2>&1; then
    echo "  ✘ make missing — install with: $INSTALL make"
    fatal=1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "  ✘ python3 missing — install with: $INSTALL $BUILD_DEPS"
    fatal=1
fi
if (( fatal )); then exit 1; fi

# Tkinter — non-fatal, just disables the overlay
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "  ⚠ tkinter missing — the floating overlay will be disabled."
    echo "    Install with: $INSTALL $BUILD_DEPS"
    echo
fi

# On Wayland we need /dev/input access for the global hotkey listener.
if [ "$SESSION" = "wayland" ]; then
    if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
        echo "  ⚠ you are not in the 'input' group — Wayland needs this so voice-type"
        echo "    can read /dev/input/event* for the global hotkey listener."
        echo "    Fix with:"
        echo "      sudo usermod -aG input $USER"
        echo "    Then log out and back in (or run \`newgrp input\` for this shell only)."
        echo
    fi
fi

# Runtime deps for the active session
missing=()
hint=""
if [ "$SESSION" = "wayland" ]; then
    if [[ "$DE_LOWER" == *gnome* ]]; then
        # GNOME Wayland: wtype doesn't work — we need ydotool.
        command -v ydotool >/dev/null 2>&1 || missing+=("ydotool")
        if ! pgrep -x ydotoold >/dev/null 2>&1; then
            echo "  ⚠ ydotoold daemon not running. After install, run:"
            echo "      sudo systemctl enable --now ydotoold"
            echo "    and ensure /tmp/.ydotool_socket is world-writable (mode 0666)."
            echo
        fi
        command -v wl-copy  >/dev/null 2>&1 || missing+=("wl-clipboard")
        hint="$GNOME_WL_DEPS"
    else
        command -v wtype   >/dev/null 2>&1 || missing+=("wtype")
        command -v wl-copy >/dev/null 2>&1 || missing+=("wl-clipboard")
        hint="$WAYLAND_DEPS"
    fi
else
    command -v xdotool >/dev/null 2>&1 || missing+=("xdotool")
    if ! command -v xclip >/dev/null 2>&1 && ! command -v xsel >/dev/null 2>&1; then
        missing+=("xclip (or xsel)")
    fi
    command -v xprop >/dev/null 2>&1 || true  # optional, only for terminal detection
    hint="$X11_DEPS"
fi
command -v notify-send >/dev/null 2>&1 || missing+=("libnotify (notify-send)")

if [ "${#missing[@]}" -gt 0 ]; then
    echo "  ⚠ runtime dependencies missing: ${missing[*]}"
    echo "    install with: $INSTALL $hint"
    echo "    (the app will still build, but won't work until these are present)"
    echo
fi

# GNOME-tray nag
if [[ "$DE_LOWER" == *gnome* ]]; then
    echo "  ℹ GNOME Shell needs the AppIndicator extension for the tray icon to appear:"
    echo "      https://extensions.gnome.org/extension/615/appindicator-support/"
    echo "    Without it, you'll still get the floating overlay + desktop notifications."
    echo
fi

echo "→ building & installing (this builds a ~35MB binary on first run; ~1 min)"
exec make install
