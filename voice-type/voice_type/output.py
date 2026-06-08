"""Output backends: type / paste / erase at the focused window, on X11 and Wayland."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable

from .log import log


def _run_checked(cmd: list[str], **kwargs) -> None:
    """subprocess.run(check=True) plus stderr-capture so error messages are
    actually useful in the log instead of just 'returned non-zero exit'."""
    r = subprocess.run(cmd, capture_output=True, **kwargs)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()
        raise subprocess.CalledProcessError(
            r.returncode, cmd, output=r.stdout,
            stderr=(err.encode("utf-8") if err else r.stderr),
        )


def detect_session() -> str:
    if sys.platform == "win32" or os.name == "nt":
        return "windows"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    return "x11"


def detect_de() -> str:
    """Best-effort desktop-environment identifier (kde / gnome / wlroots / xfce / ...)."""
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in de or "plasma" in de:
        return "kde"
    if "gnome" in de:
        return "gnome"
    if "sway" in de or "wlroots" in de or "river" in de or "hyprland" in de:
        return "wlroots"
    if "xfce" in de:
        return "xfce"
    if "cinnamon" in de:
        return "cinnamon"
    if "mate" in de:
        return "mate"
    if "lxqt" in de:
        return "lxqt"
    return de or "unknown"


# Terminal emulators where Ctrl+V is a literal ^V and Ctrl+Shift+V is paste.
_TERMINAL_CLASSES = {
    "konsole", "yakuake", "xterm", "urxvt", "urxvtd", "gnome-terminal",
    "terminator", "alacritty", "kitty", "tilix", "guake", "xfce4-terminal",
    "lxterminal", "qterminal", "wezterm", "rxvt", "st-256color", "st",
    "deepin-terminal", "cool-retro-term", "foot",
}


class OutputBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def paste_text(
        self, text: str, restore_delay_sec: float, *, keep_clipboard: bool = False,
    ) -> None: ...

    @abstractmethod
    def set_clipboard(self, text: str) -> None:
        """Place text in the system clipboard. Does NOT modify the focused
        window. Used by `output.mode = "clipboard"` for "dictate, paste later"
        workflows."""

    @abstractmethod
    def erase(self, n: int) -> None:
        """Send n backspaces to the focused window."""


class _RestoreScheduler:
    """Coordinates clipboard save/restore across rapid-fire pastes.

    Naive `read prev / write ours / Timer(restore prev)` corrupts the clipboard
    when two dictations land within the restore window — the second paste's
    "previous" *is* our first paste. This captures the *original* clipboard
    once at the start of a burst and reuses it across overlapping pastes:
    subsequent pastes cancel the pending restore-timer and re-schedule against
    the same captured value. The user's clipboard survives any burst.
    """

    def __init__(self, read_fn: Callable[[], bytes], write_fn: Callable[[bytes], None]) -> None:
        self._read = read_fn
        self._write = write_fn
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._original: bytes | None = None

    def capture(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
                return  # _original still valid from the previous capture
            try:
                self._original = self._read()
            except Exception as e:  # noqa: BLE001
                log().debug("clipboard read failed: %s", e)
                self._original = b""

    def schedule(self, delay_sec: float) -> None:
        with self._lock:
            t = threading.Timer(delay_sec, self._restore)
            t.daemon = True
            self._timer = t
            t.start()

    def _restore(self) -> None:
        with self._lock:
            prev = self._original
            self._original = None
            self._timer = None
        if prev is None:
            return
        try:
            self._write(prev)
        except Exception as e:  # noqa: BLE001
            log().debug("clipboard restore failed: %s", e)


# ---------------------------------------------------------------------------
# X11
# ---------------------------------------------------------------------------


class _X11Clipboard:
    def __init__(self, tool: str) -> None:
        self._tool = tool

    def read(self) -> bytes:
        cmd = (
            ["xclip", "-selection", "clipboard", "-o"]
            if self._tool == "xclip"
            else ["xsel", "--clipboard", "--output"]
        )
        try:
            return subprocess.run(cmd, capture_output=True, check=True).stdout
        except subprocess.CalledProcessError:
            return b""

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        cmd = (
            ["xclip", "-selection", "clipboard", "-i"]
            if self._tool == "xclip"
            else ["xsel", "--clipboard", "--input"]
        )
        subprocess.run(cmd, input=data, check=False)


def _resolve_x11_clipboard() -> _X11Clipboard:
    for tool in ("xclip", "xsel"):
        if shutil.which(tool):
            return _X11Clipboard(tool)
    raise RuntimeError(
        "xclip or xsel required for clipboard-paste on X11 — "
        "install 'xclip' (or 'xsel'). See README for distro-specific commands."
    )


def _active_window_class_x11() -> str:
    """Returns lowercased WM_CLASS (instance name) of the focused window."""
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, check=True, timeout=0.5,
        ).stdout.strip().decode("ascii", errors="ignore")
        if not wid:
            return ""
        info = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS"],
            capture_output=True, check=True, timeout=0.5,
        ).stdout.decode("utf-8", errors="ignore")
        m = re.search(r'"([^"]+)"', info)
        return m.group(1).lower() if m else ""
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""


class X11Backend(OutputBackend):
    name = "x11"

    def __init__(self, terminal_aware: bool = True) -> None:
        if shutil.which("xdotool") is None:
            raise RuntimeError(
                "xdotool not found — install 'xdotool'. "
                "See README for distro-specific commands."
            )
        self._clipboard = _resolve_x11_clipboard()
        self._restore = _RestoreScheduler(self._clipboard.read, self._clipboard.write)
        self._terminal_aware = terminal_aware and shutil.which("xprop") is not None
        if terminal_aware and not self._terminal_aware:
            log().info("xprop missing; terminal-paste detection off (install 'xprop')")

    def type_text(self, text: str) -> None:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", text],
            check=True,
        )

    def paste_text(
        self, text: str, restore_delay_sec: float, *, keep_clipboard: bool = False,
    ) -> None:
        if not keep_clipboard:
            self._restore.capture()
        self._clipboard.write(text)
        paste_combo = "ctrl+v"
        if self._terminal_aware:
            klass = _active_window_class_x11()
            if klass in _TERMINAL_CLASSES:
                paste_combo = "ctrl+shift+v"
                log().debug("terminal '%s' detected; using ctrl+shift+v", klass)
        try:
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", paste_combo], check=True
            )
        finally:
            if not keep_clipboard:
                self._restore.schedule(restore_delay_sec)

    def erase(self, n: int) -> None:
        if n <= 0:
            return
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "--repeat", str(n),
             "--delay", "5", "BackSpace"],
            check=True,
        )

    def set_clipboard(self, text: str) -> None:
        self._clipboard.write(text)


# ---------------------------------------------------------------------------
# Wayland
# ---------------------------------------------------------------------------


def _wtype_works() -> tuple[bool, str]:
    """Probe whether wtype can actually bind virtual-keyboard. Returns
    (ok, stderr). Compositors that don't implement virtual-keyboard-v1
    (GNOME Mutter, *some* Plasma 6 builds) fail this even with wtype
    installed."""
    try:
        r = subprocess.run(
            ["wtype", ""],
            capture_output=True, timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()


class WaylandBackend(OutputBackend):
    """wtype-based Wayland output. Works on wlroots (sway, hyprland, river)
    and *some* KDE Plasma 6 builds. NOT on GNOME Mutter, and not on the
    Plasma 6 builds that don't expose virtual-keyboard-v1. The constructor
    probes wtype at startup; if it can't bind, make_backend() falls through
    to WaylandYdotoolBackend automatically."""

    name = "wayland-wtype"

    def __init__(self, terminal_aware: bool = True) -> None:
        missing = [t for t in ("wtype", "wl-copy", "wl-paste") if shutil.which(t) is None]
        if missing:
            raise RuntimeError(
                f"missing tools for Wayland: {', '.join(missing)}. "
                f"Install 'wtype' and 'wl-clipboard' "
                f"(or use ydotool — see README)."
            )
        ok, stderr = _wtype_works()
        if not ok:
            raise RuntimeError(
                f"wtype installed but the compositor refused virtual-keyboard "
                f"({stderr or 'unknown error'}). Install ydotool instead "
                f"(works via /dev/uinput, no compositor support needed)."
            )
        self._restore = _RestoreScheduler(self._read_clipboard, self._write_clipboard)
        if terminal_aware:
            # Wayland window-class detection is compositor-specific; not wired up.
            log().debug("terminal-paste awareness not implemented on Wayland")

    def type_text(self, text: str) -> None:
        _run_checked(["wtype", "--", text])

    def paste_text(
        self, text: str, restore_delay_sec: float, *, keep_clipboard: bool = False,
    ) -> None:
        if not keep_clipboard:
            self._restore.capture()
        subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
        try:
            _run_checked(["wtype", "-M", "ctrl", "v", "-m", "ctrl"])
        finally:
            if not keep_clipboard:
                self._restore.schedule(restore_delay_sec)

    def erase(self, n: int) -> None:
        if n <= 0:
            return
        # wtype has no --repeat; batch multiple -k BackSpace flags in one call.
        # Cap per-call to keep argv reasonable; loop in chunks of 500.
        remaining = n
        while remaining > 0:
            batch = min(remaining, 500)
            args = ["wtype"]
            for _ in range(batch):
                args += ["-k", "BackSpace"]
            subprocess.run(args, check=True)
            remaining -= batch

    def set_clipboard(self, text: str) -> None:
        subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)

    @staticmethod
    def _read_clipboard() -> bytes:
        try:
            return subprocess.run(
                ["wl-paste", "--no-newline"], capture_output=True, check=True
            ).stdout
        except subprocess.CalledProcessError:
            return b""

    @staticmethod
    def _write_clipboard(data: bytes) -> None:
        subprocess.run(["wl-copy"], input=data, check=False)


# ---------------------------------------------------------------------------
# Wayland via ydotool (uinput) — works on GNOME Mutter and any other
# compositor that doesn't support virtual-keyboard. Requires `ydotoold` to be
# running (sudo systemctl enable --now ydotoold) and the user to have access
# to the daemon's socket (typical default: /tmp/.ydotool_socket, 0666).
# ---------------------------------------------------------------------------

# Linux input event codes — see /usr/include/linux/input-event-codes.h
_KEY_BACKSPACE = 14
_KEY_LEFTCTRL = 29
_KEY_LEFTSHIFT = 42
_KEY_C = 46
_KEY_V = 47
_KEY_LEFT = 105
_KEY_RIGHT = 106


# Qt-based terminal apps expose `isActiveWindow` on their per-process DBus
# service. This is the only reliable way to do "is a terminal focused" on
# KDE Wayland without privileged access. Add new entries here if a user has
# a Qt terminal we don't cover yet.
_QT_TERMINAL_DBUS = {
    # service-name prefix : object path that responds to isActiveWindow
    "org.kde.konsole-": "/konsole/MainWindow_1",
    "org.kde.yakuake":  "/yakuake/MainWindow_1",
}


def _wayland_terminal_active_via_dbus() -> bool:
    """True if a known Qt-based terminal (Konsole, Yakuake) currently has the
    keyboard focus. ~4ms when a terminal is running, ~5ms otherwise."""
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if qdbus is None:
        return False
    try:
        r = subprocess.run(
            [qdbus], capture_output=True, check=False, timeout=0.3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    services = [s.strip() for s in r.stdout.decode(errors="replace").splitlines() if s.strip()]
    for prefix, path in _QT_TERMINAL_DBUS.items():
        for svc in services:
            if not svc.startswith(prefix):
                continue
            try:
                r2 = subprocess.run(
                    [qdbus, svc, path,
                     "org.freedesktop.DBus.Properties.Get",
                     "org.qtproject.Qt.QWidget", "isActiveWindow"],
                    capture_output=True, check=False, timeout=0.3,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if r2.returncode == 0 and b"true" in r2.stdout.lower():
                log().debug("active Qt terminal detected via %s", svc)
                return True
    return False


def _ydotool_socket_candidates() -> list[str]:
    """Possible ydotoold socket paths, in priority order.

    1. Explicit YDOTOOL_SOCKET override
    2. /tmp/.ydotool_socket (what our systemd unit creates)
    3. $XDG_RUNTIME_DIR/.ydotool_socket (ydotool 1.0.4 client default)
    4. /run/user/<UID>/.ydotool_socket (fallback if XDG_RUNTIME_DIR unset)
    """
    paths = []
    explicit = os.environ.get("YDOTOOL_SOCKET")
    if explicit:
        paths.append(explicit)
    paths.append("/tmp/.ydotool_socket")
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        paths.append(f"{xdg}/.ydotool_socket")
    try:
        uid = os.getuid()
        paths.append(f"/run/user/{uid}/.ydotool_socket")
    except OSError:
        pass
    # de-dup, preserve order
    seen = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _ydotool_find_socket() -> str | None:
    for p in _ydotool_socket_candidates():
        try:
            if os.path.exists(p) and os.access(p, os.R_OK | os.W_OK):
                return p
        except OSError:
            continue
    return None


class WaylandYdotoolBackend(OutputBackend):
    """uinput-based output. Works under any Wayland compositor (and X11), but
    needs ydotoold running. The right choice for GNOME Wayland and for KDE
    Plasma 6 builds that don't expose virtual-keyboard-v1."""

    name = "wayland-ydotool"

    def __init__(
        self, terminal_aware: bool = True, force_terminal: bool = False,
    ) -> None:
        if shutil.which("ydotool") is None:
            raise RuntimeError(
                "ydotool not found — install 'ydotool' (see README)."
            )
        for t in ("wl-copy", "wl-paste"):
            if shutil.which(t) is None:
                raise RuntimeError(
                    f"{t} not found — install 'wl-clipboard' (see README)."
                )
        socket = _ydotool_find_socket()
        if socket is None:
            tried = ", ".join(_ydotool_socket_candidates())
            raise RuntimeError(
                f"ydotoold socket not found (tried: {tried}). "
                f"Set it up with 'make ydotool-setup', or "
                f"'sudo systemctl enable --now ydotoold'."
            )
        self._socket = socket
        log().info("ydotool socket: %s", socket)
        # Subprocesses inherit this env so the ydotool client knows where to
        # connect. Without it, ydotool 1.0.4 looks only at the XDG runtime
        # dir and ignores /tmp even when that's where the socket lives.
        self._env = {**os.environ, "YDOTOOL_SOCKET": socket}
        self._terminal_aware = terminal_aware
        self._force_terminal = force_terminal
        if force_terminal:
            log().info("output: force_terminal_paste = true (always Ctrl+Shift+V)")
        self._restore = _RestoreScheduler(
            WaylandBackend._read_clipboard, WaylandBackend._write_clipboard
        )

    def type_text(self, text: str) -> None:
        _run_checked(["ydotool", "type", "--", text], env=self._env)

    def paste_text(
        self, text: str, restore_delay_sec: float, *, keep_clipboard: bool = False,
    ) -> None:
        if not keep_clipboard:
            self._restore.capture()
        subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
        try:
            use_shift = self._force_terminal or (
                self._terminal_aware and _wayland_terminal_active_via_dbus()
            )
            if use_shift:
                # Ctrl+Shift+V for KDE terminals.
                keys = [
                    f"{_KEY_LEFTCTRL}:1", f"{_KEY_LEFTSHIFT}:1",
                    f"{_KEY_V}:1", f"{_KEY_V}:0",
                    f"{_KEY_LEFTSHIFT}:0", f"{_KEY_LEFTCTRL}:0",
                ]
                log().debug("paste: ctrl+shift+v (terminal detected)")
            else:
                keys = [
                    f"{_KEY_LEFTCTRL}:1", f"{_KEY_V}:1",
                    f"{_KEY_V}:0", f"{_KEY_LEFTCTRL}:0",
                ]
            _run_checked(["ydotool", "key", *keys], env=self._env)
        finally:
            if not keep_clipboard:
                self._restore.schedule(restore_delay_sec)

    def erase(self, n: int) -> None:
        if n <= 0:
            return
        remaining = n
        while remaining > 0:
            batch = min(remaining, 500)
            args = ["ydotool", "key"]
            for _ in range(batch):
                args += [f"{_KEY_BACKSPACE}:1", f"{_KEY_BACKSPACE}:0"]
            _run_checked(args, env=self._env)
            remaining -= batch

    def set_clipboard(self, text: str) -> None:
        subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)

    def peek_context(self, n_chars: int = 4) -> str | None:
        """Read up to n_chars characters before the cursor for context-aware
        decisions (e.g. capitalize-after-period). Uses Shift+Left selection
        + copy, then deselects. Pollutes the clipboard briefly but restores
        it before returning.

        Returns:
          - the peeked text (possibly empty if cursor is at start of input)
          - None if the peek mechanism failed (caller should treat as
            "couldn't determine" and not change behavior)
        """
        if n_chars <= 0:
            return ""
        # Save user's clipboard so the peek's Ctrl+C doesn't trash it.
        saved = WaylandBackend._read_clipboard()
        try:
            # Clear so we can tell empty-from-cursor-at-start apart from
            # leftover content. wl-copy --clear is reliable on KDE/wlroots.
            subprocess.run(["wl-copy", "--clear"], check=False)
            # Select n chars to the left.
            select_keys = [
                f"{_KEY_LEFTSHIFT}:1",
            ]
            for _ in range(n_chars):
                select_keys += [f"{_KEY_LEFT}:1", f"{_KEY_LEFT}:0"]
            select_keys += [f"{_KEY_LEFTSHIFT}:0"]
            _run_checked(["ydotool", "key", *select_keys], env=self._env)

            # Copy. In terminals Ctrl+Shift+C is "copy"; elsewhere Ctrl+C.
            use_shift = self._force_terminal or (
                self._terminal_aware and _wayland_terminal_active_via_dbus()
            )
            if use_shift:
                copy_keys = [
                    f"{_KEY_LEFTCTRL}:1", f"{_KEY_LEFTSHIFT}:1",
                    f"{_KEY_C}:1", f"{_KEY_C}:0",
                    f"{_KEY_LEFTSHIFT}:0", f"{_KEY_LEFTCTRL}:0",
                ]
            else:
                copy_keys = [
                    f"{_KEY_LEFTCTRL}:1",
                    f"{_KEY_C}:1", f"{_KEY_C}:0",
                    f"{_KEY_LEFTCTRL}:0",
                ]
            _run_checked(["ydotool", "key", *copy_keys], env=self._env)

            # Give the compositor + app a moment to put the selection in
            # the clipboard before we read it.
            time.sleep(0.08)
            peeked_bytes = WaylandBackend._read_clipboard()
            peeked = peeked_bytes.decode("utf-8", errors="replace")

            # Deselect: send Right n times. After Shift+Left selection, a
            # plain Right collapses the selection to its end (= original
            # cursor position) and then moves one char forward; we then
            # send Left to come back. Simpler: send Right once to collapse
            # to original position.
            _run_checked(
                ["ydotool", "key", f"{_KEY_RIGHT}:1", f"{_KEY_RIGHT}:0"],
                env=self._env,
            )
            return peeked
        except (subprocess.CalledProcessError, OSError) as e:
            log().debug("peek_context failed: %s", e)
            return None
        finally:
            # Always restore the user's clipboard.
            WaylandBackend._write_clipboard(saved)

    def release_key(self, evdev_keycode: int) -> None:
        """Inject a synthetic key-release event for the compositor's benefit.

        The evdev hotkey listener calls dev.grab() during the hold, which
        routes the real release event *exclusively* to our process. KWin
        never sees the release, so its internal "currently held keys" state
        stays stuck — and every subsequent window-focus event forwards "key
        is held" to that window (e.g. FreeRDP autorepeats `` ` `` into the
        remote session). Injecting a release through uinput fixes the state.
        """
        _run_checked(["ydotool", "key", f"{evdev_keycode}:0"], env=self._env)


# ---------------------------------------------------------------------------


def make_backend(
    prefer: str = "auto",
    terminal_aware: bool = True,
    force_terminal: bool = False,
) -> OutputBackend:
    target = detect_session() if prefer == "auto" else prefer
    if target == "windows":
        from .output_win import WindowsBackend   # lazy: ctypes.windll only on Windows
        log().info("output backend: windows")
        return WindowsBackend(terminal_aware=terminal_aware)
    if target == "wayland":
        de = detect_de()
        log().info("output backend: wayland (de=%s)", de)
        # GNOME's compositor doesn't implement virtual-keyboard, so wtype
        # is a non-starter there — try ydotool first.
        prefer_ydotool = de == "gnome"
        attempts = (
            [
                lambda: WaylandYdotoolBackend(terminal_aware=terminal_aware, force_terminal=force_terminal),
                lambda: WaylandBackend(terminal_aware=terminal_aware),
            ]
            if prefer_ydotool
            else [
                lambda: WaylandBackend(terminal_aware=terminal_aware),
                lambda: WaylandYdotoolBackend(terminal_aware=terminal_aware, force_terminal=force_terminal),
            ]
        )
        last_err: Exception | None = None
        for factory in attempts:
            try:
                backend = factory()
                log().info("wayland output: %s", backend.name)
                return backend
            except RuntimeError as e:
                log().info("backend probe: %s", e)
                last_err = e
        # No backend worked — re-raise the last error with context.
        raise RuntimeError(
            f"no working Wayland output backend on {de}: {last_err}"
        )
    log().info("output backend: x11")
    return X11Backend(terminal_aware=terminal_aware)
