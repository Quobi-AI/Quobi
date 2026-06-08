"""Tray + desktop notifications + floating overlay. Graceful fallback chain."""
from __future__ import annotations

import shutil
import subprocess
import threading
from enum import Enum

from .log import log


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    FORMATTING = "formatting"
    ERROR = "error"


_STATE_COLOR = {
    State.IDLE: (110, 110, 115),
    State.RECORDING: (220, 50, 60),
    State.TRANSCRIBING: (245, 165, 35),
    State.FORMATTING: (90, 140, 235),
    State.ERROR: (200, 30, 30),
}


class Indicator:
    """Base interface — every method has a no-op default so children only
    override what they actually render."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_state(self, state: State, detail: str = "") -> None: ...
    def update_detail(self, state: State, detail: str) -> None: ...
    def set_mic_level(self, level: float) -> None: ...
    def notify(self, title: str, body: str, urgent: bool = False) -> None: ...


class NullIndicator(Indicator):
    pass


class NotifyIndicator(Indicator):
    def __init__(self) -> None:
        self._available = shutil.which("notify-send") is not None
        if not self._available:
            log().warning("notify-send not found; notifications disabled")

    def notify(self, title: str, body: str, urgent: bool = False) -> None:
        if not self._available:
            return
        args = ["notify-send", "-a", "voice-type"]
        if urgent:
            args += ["-u", "critical"]
        args += [title, body]
        try:
            subprocess.run(args, check=False)
        except OSError as e:
            log().debug("notify-send: %s", e)


def _open_dashboard() -> None:
    """Open the Quobi desktop app — the single front end. The daemon has no GUI
    of its own; the tray just launches Quobi as a detached process."""
    import shutil
    import subprocess
    from pathlib import Path
    exe = shutil.which("quobi")
    if not exe:
        for cand in (
            Path.home() / ".local/bin/quobi",
            Path.home() / "WhisperFlowClone/Quobi-desktop-x86_64.AppImage",
        ):
            if cand.exists():
                exe = str(cand)
                break
    if not exe:
        log().warning("Quobi app not found; install it or add 'quobi' to PATH")
        return
    try:
        subprocess.Popen([exe], start_new_session=True)
    except OSError as e:
        log().warning("could not open Quobi: %s", e)


class TrayIndicator(NotifyIndicator):
    """System tray icon with state-colored dot."""

    def __init__(self) -> None:
        super().__init__()
        from PIL import Image, ImageDraw  # noqa: F401
        import pystray

        self._Image = Image
        self._ImageDraw = ImageDraw
        self._pystray = pystray
        self._state = State.IDLE
        self._detail = ""
        self._icon = None
        self._thread: threading.Thread | None = None

    def _icon_image(self, state: State):
        size = 64
        img = self._Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = self._ImageDraw.Draw(img)
        d.ellipse((6, 6, size - 6, size - 6), fill=(*_STATE_COLOR[state], 255))
        return img

    def _title(self) -> str:
        # ASCII-only on purpose: pystray's X11 backend uses set_wm_name, which
        # is latin-1. Any character outside that range (em-dash, etc.) crashes
        # the tray thread.
        s = f"Quobi - {self._state.value}"
        if self._detail:
            s += f" ({self._detail})"
        return s

    def start(self) -> None:
        def _run():
            menu = self._pystray.Menu(
                self._pystray.MenuItem("Quobi", None, enabled=False),
                self._pystray.MenuItem(
                    lambda _i: f"state: {self._state.value}"
                    + (f" ({self._detail})" if self._detail else ""),
                    None, enabled=False,
                ),
                self._pystray.MenuItem("Open Quobi", lambda _i, _it: _open_dashboard()),
                self._pystray.Menu.SEPARATOR,
                self._pystray.MenuItem("Quit", lambda _i, _it: self._quit()),
            )
            self._icon = self._pystray.Icon(
                "quobi", self._icon_image(State.IDLE),
                "Quobi - idle", menu,
            )
            try:
                self._icon.run()
            except Exception as e:  # noqa: BLE001
                log().warning("tray loop ended: %s", e)

        self._thread = threading.Thread(target=_run, daemon=True, name="tray")
        self._thread.start()

    def _quit(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._quit()

    def set_state(self, state: State, detail: str = "") -> None:
        self._state = state
        self._detail = detail
        if not self._icon:
            return
        try:
            self._icon.icon = self._icon_image(state)
            self._icon.title = self._title()
        except Exception as e:  # noqa: BLE001
            log().debug("tray update failed: %s", e)

    def update_detail(self, state: State, detail: str) -> None:
        # Tooltip-only update — skip the icon regen.
        if state != self._state:
            return self.set_state(state, detail)
        self._detail = detail
        if not self._icon:
            return
        try:
            self._icon.title = self._title()
        except Exception as e:  # noqa: BLE001
            log().debug("tray title update: %s", e)


class CombinedIndicator(Indicator):
    """Fan-out: every call hits every child, swallowing per-child errors."""

    def __init__(self, *children: Indicator) -> None:
        self._children = list(children)

    def _fanout(self, name: str, *args, **kwargs) -> None:
        for c in self._children:
            try:
                getattr(c, name)(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                log().debug("%s.%s failed: %s", type(c).__name__, name, e)

    def start(self) -> None:
        self._fanout("start")

    def stop(self) -> None:
        self._fanout("stop")

    def set_state(self, state: State, detail: str = "") -> None:
        self._fanout("set_state", state, detail)

    def update_detail(self, state: State, detail: str) -> None:
        self._fanout("update_detail", state, detail)

    def set_mic_level(self, level: float) -> None:
        self._fanout("set_mic_level", level)

    def notify(self, title: str, body: str, urgent: bool = False) -> None:
        self._fanout("notify", title, body, urgent)


def _make_base(mode: str) -> Indicator:
    if mode == "off":
        return NullIndicator()
    if mode == "notify":
        return NotifyIndicator()
    try:
        return TrayIndicator()
    except Exception as e:  # noqa: BLE001
        log().warning("tray unavailable (%s); falling back to notifications", e)
        return NotifyIndicator()


def make_indicator(mode: str, floating: bool) -> Indicator:
    children: list[Indicator] = [_make_base(mode)]
    if floating:
        try:
            from .overlay import FloatingOverlay
            children.append(FloatingOverlay())
        except Exception as e:  # noqa: BLE001
            log().warning("floating overlay disabled: %s", e)
    if len(children) == 1:
        return children[0]
    return CombinedIndicator(*children)
