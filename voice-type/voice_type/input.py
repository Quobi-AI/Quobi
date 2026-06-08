"""Global hotkey listener.

Two backends:

  * **pynput** — X11 only. Hooks XGrabKey at the X server level. Sees the
    initial press but doesn't grab the device, so autorepeat events still
    reach the focused window. Fine on X11; useless on Wayland.

  * **evdev with grab+replay** — Wayland (and X11 if you prefer). Opens a
    *virtual* keyboard via uinput, then *permanently grabs* every real
    keyboard device, reading all events exclusively. Every non-hotkey event
    is replayed through the virtual device so the compositor sees a normal
    keyboard. The hotkey itself is consumed and never leaks to any window —
    not your local apps, not your VM, not your remote desktop. No stuck
    state because we control the entire event stream.

The replay backend needs write access to /dev/uinput. On most distros that
means the user must be in the `input` group AND the device must be mode 0660.
Run `make uinput-setup` once to install the udev rule.
"""
from __future__ import annotations

import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable

from .log import log
from .output import detect_session


class HotkeyListener(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    def requires_pre_erase(self) -> bool:
        """Returns True if the hotkey's press event leaks to the focused
        window despite us — pipeline pre-erases one char as a workaround."""
        return True


def make_listener(
    key_name: str,
    backend: str,
    on_press: Callable[[], None],
    on_release: Callable[[], None],
    release_injector: Callable[[int], None] | None = None,  # unused under replay
) -> HotkeyListener:
    target = backend
    if target == "auto":
        target = "evdev" if detect_session() == "wayland" else "pynput"
    log().info("hotkey backend=%s key=%s", target, key_name)
    if target == "evdev":
        return EvdevReplayListener(key_name, on_press, on_release)
    return PynputHotkeyListener(key_name, on_press, on_release)


# ---------------------------------------------------------------------------
# pynput (X11)
# ---------------------------------------------------------------------------


# Character/punctuation key names (shared with the config + the evdev backend)
# mapped to the literal character. pynput has no Key.grave, so these have to be
# KeyCode char keys. This keeps a config portable: "grave" works on Linux (evdev)
# AND Windows (pynput) instead of crashing the listener on Windows.
_CHAR_KEY_TO_CHAR = {
    "grave": "`", "backtick": "`", "minus": "-", "equals": "=",
    "bracket_left": "[", "bracket_right": "]", "backslash": "\\",
    "semicolon": ";", "apostrophe": "'", "comma": ",", "period": ".",
    "slash": "/",
}


def _pynput_key_from_name(name: str):
    from pynput import keyboard

    if name.startswith("char:"):
        return keyboard.KeyCode.from_char(name[len("char:") :])
    if name in _CHAR_KEY_TO_CHAR:
        return keyboard.KeyCode.from_char(_CHAR_KEY_TO_CHAR[name])
    try:
        return getattr(keyboard.Key, name)
    except AttributeError as e:
        raise ValueError(
            f"unknown pynput key {name!r}; choose from pynput.keyboard.Key "
            "(ctrl_r, alt_r, f9, menu, pause, ...) or use 'char:X'"
        ) from e


class PynputHotkeyListener(HotkeyListener):
    def __init__(self, key_name, on_press, on_release) -> None:
        self._key = _pynput_key_from_name(key_name)
        self._on_press = on_press
        self._on_release = on_release
        self._listener = None
        self._pressed = False
        self._self_types = key_name.startswith("char:") or key_name in (
            SELF_TYPING_HOTKEYS  # name set below; kept for legacy callers
        )

    def _press(self, key) -> None:
        if key == self._key and not self._pressed:
            self._pressed = True
            self._on_press()

    def _release(self, key) -> None:
        if key == self._key and self._pressed:
            self._pressed = False
            self._on_release()

    def _hotkey_vk(self):
        """The Windows virtual-key code for the configured hotkey, or None.
        Special keys (f9, space) carry it on the enum value; char keys (backtick)
        don't, so map the character to its vk via VkKeyScan."""
        k = self._key
        vk = getattr(k, "vk", None)
        if vk is not None:
            return vk
        val = getattr(k, "value", None)
        if val is not None and getattr(val, "vk", None) is not None:
            return val.vk
        ch = getattr(k, "char", None) or (getattr(val, "char", None) if val else None)
        if ch:
            try:
                import ctypes
                r = ctypes.windll.user32.VkKeyScanW(ord(ch))
                if r != -1:
                    return r & 0xFF
            except Exception:  # noqa: BLE001
                pass
        return None

    def start(self) -> None:
        from pynput import keyboard

        kwargs = {"on_press": self._press, "on_release": self._release}
        # Windows: pynput CAN suppress an event (X11 cannot), so the hotkey never
        # types a character. The catch: suppress_event() works by RAISING a
        # SuppressException, which unwinds pynput's hook BEFORE it dispatches the
        # event to on_press/on_release - so suppressing also kills our callbacks.
        # (And catching that exception here would un-suppress it, leaking the
        # char.) So we fire press/release OURSELVES from inside the filter, keyed
        # off the Windows message, THEN suppress. Result: dictation triggers AND
        # no char leaks.
        if sys.platform == "win32":
            target_vk = self._hotkey_vk()
            _WM_PRESS = {0x0100, 0x0104}    # WM_KEYDOWN, WM_SYSKEYDOWN
            _WM_RELEASE = {0x0101, 0x0105}  # WM_KEYUP, WM_SYSKEYUP

            def _win32_filter(msg, data):  # noqa: ANN001
                if target_vk is None or data.vkCode != target_vk:
                    return  # not our hotkey: let pynput dispatch normally
                # _press/_release dedupe via self._pressed, so autorepeat
                # (repeated WM_KEYDOWN while held) only fires on_press once.
                if msg in _WM_PRESS:
                    self._press(self._key)
                elif msg in _WM_RELEASE:
                    self._release(self._key)
                self._listener.suppress_event()  # raises -> swallow the keystroke

            kwargs["win32_event_filter"] = _win32_filter

        self._listener = keyboard.Listener(**kwargs)
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def requires_pre_erase(self) -> bool:
        # Windows: we suppress the hotkey event, so nothing leaks -> no erase.
        # X11 pynput can't grab, so self-typing keys still leak one char.
        if sys.platform == "win32":
            return False
        return self._self_types


# ---------------------------------------------------------------------------
# evdev with grab+replay (the right answer on Wayland)
# ---------------------------------------------------------------------------


# Mirror pynput's key names so config is portable. Also includes
# character keys for users who want a never-used punctuation key as
# push-to-talk. The replay backend handles these without any leak.
_EVDEV_KEY_ALIASES = {
    "ctrl_r": "KEY_RIGHTCTRL",
    "ctrl_l": "KEY_LEFTCTRL",
    "alt_r": "KEY_RIGHTALT",
    "alt_l": "KEY_LEFTALT",
    "shift_r": "KEY_RIGHTSHIFT",
    "shift_l": "KEY_LEFTSHIFT",
    "cmd": "KEY_LEFTMETA",
    "cmd_l": "KEY_LEFTMETA",
    "cmd_r": "KEY_RIGHTMETA",
    "menu": "KEY_COMPOSE",
    "pause": "KEY_PAUSE",
    "scroll_lock": "KEY_SCROLLLOCK",
    "caps_lock": "KEY_CAPSLOCK",
    "esc": "KEY_ESC",
    "tab": "KEY_TAB",
    "space": "KEY_SPACE",
    "enter": "KEY_ENTER",
    "backspace": "KEY_BACKSPACE",
    "insert": "KEY_INSERT",
    "delete": "KEY_DELETE",
    "home": "KEY_HOME",
    "end": "KEY_END",
    "page_up": "KEY_PAGEUP",
    "page_down": "KEY_PAGEDOWN",
    "grave": "KEY_GRAVE",
    "backtick": "KEY_GRAVE",
    "minus": "KEY_MINUS",
    "equals": "KEY_EQUAL",
    "bracket_left": "KEY_LEFTBRACE",
    "bracket_right": "KEY_RIGHTBRACE",
    "backslash": "KEY_BACKSLASH",
    "semicolon": "KEY_SEMICOLON",
    "apostrophe": "KEY_APOSTROPHE",
    "comma": "KEY_COMMA",
    "period": "KEY_DOT",
    "slash": "KEY_SLASH",
    **{f"f{i}": f"KEY_F{i}" for i in range(1, 25)},
}


# Retained for the pynput backend (X11) which doesn't grab.
SELF_TYPING_HOTKEYS = frozenset({
    "grave", "backtick", "minus", "equals", "bracket_left", "bracket_right",
    "backslash", "semicolon", "apostrophe", "comma", "period", "slash",
})


def hotkey_self_types(key_name: str) -> bool:
    """Used by callers that haven't migrated to listener.requires_pre_erase."""
    return key_name in SELF_TYPING_HOTKEYS or key_name.startswith("char:")


class EvdevReplayListener(HotkeyListener):
    """Grab+replay listener: virtual keyboard via uinput, real keyboards
    grabbed exclusively, everything except the hotkey is forwarded through
    the virtual device. The hotkey never reaches the compositor."""

    def __init__(self, key_name, on_press, on_release) -> None:
        try:
            import evdev  # noqa: F401
            from evdev import ecodes
        except ImportError as e:
            raise RuntimeError(
                "evdev backend requires python-evdev (pip install evdev)"
            ) from e

        if key_name.startswith("char:"):
            raise ValueError(
                "evdev backend does not support char keys; pick a named key"
            )
        ev_name = _EVDEV_KEY_ALIASES.get(key_name)
        if not ev_name or not hasattr(ecodes, ev_name):
            raise ValueError(
                f"no evdev mapping for {key_name!r}; "
                f"extend _EVDEV_KEY_ALIASES in input.py"
            )

        self._keycode = getattr(ecodes, ev_name)
        self._key_name = key_name
        self._on_press = on_press
        self._on_release = on_release

        self._stop = threading.Event()
        self._pressed = False
        self._pressed_lock = threading.Lock()
        self._devices: list = []
        self._threads: list[threading.Thread] = []
        self._ui = None  # UInput virtual device; created in start()
        # Key codes we've forwarded as "down" but not yet "up". On stop() we
        # release these so a key held at shutdown (e.g. during the auto-restart
        # on save) doesn't stay stuck-pressed in the compositor and autorepeat
        # forever — the recurring "a key got grabbed and is spamming" bug.
        self._down: set[int] = set()

    # ---- HotkeyListener API -------------------------------------------

    def requires_pre_erase(self) -> bool:
        # Grab+replay never lets the hotkey reach the compositor. Even
        # self-typing keys like backtick are clean.
        return False

    def start(self) -> None:
        import evdev
        from evdev import UInput, ecodes

        self._devices = self._open_keyboards(evdev, ecodes)
        # Build the virtual keyboard from the union of capabilities across
        # all grabbed devices, so anything the user can type on their real
        # keyboards we can also type on the virtual one.
        caps = self._merged_capabilities(ecodes)
        try:
            self._ui = UInput(
                events=caps,
                name="voice-type virtual keyboard",
                vendor=0x1234,
                product=0x5678,
                version=1,
            )
        except OSError as e:
            self._close_devices()
            raise RuntimeError(
                f"cannot create uinput device: {e}. "
                f"Run 'make uinput-setup' once to install the udev rule "
                f"(/etc/udev/rules.d/60-voice-type-uinput.rules), then re-login."
            ) from e
        log().info("uinput virtual keyboard created (replay mode)")
        # Give libinput a beat to notice the new virtual device before we
        # grab the real ones — otherwise there's a sub-millisecond window
        # where the compositor sees no keyboard.
        time.sleep(0.1)
        # Don't grab while a key is physically held — grabbing mid-keypress steals
        # the key-up and strands it in the compositor (the recurring stuck-key bug).
        # Wait for a clean moment (all keys up) before grabbing.
        self._wait_for_keys_released()
        for d in self._devices:
            try:
                d.grab()
            except OSError as e:
                log().warning("could not grab %s (%s); replay may leak from it",
                              d.path, e)
        log().info("evdev: grabbed %d keyboard(s) for replay", len(self._devices))
        self._neutralize_keys_held_at_grab(ecodes)
        for d in self._devices:
            t = threading.Thread(target=self._watch, args=(d,),
                                 daemon=True, name=f"evdev-{d.path}")
            t.start()
            self._threads.append(t)

    def _wait_for_keys_released(self, timeout: float = 4.0) -> None:
        """Block until no key is held on any target keyboard (or `timeout` secs).
        Grabbing mid-keypress is what strands keys, so we wait for a clean moment.
        After the timeout we grab anyway (the anti-strand release is the backstop)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            held = False
            for d in self._devices:
                try:
                    if d.active_keys():
                        held = True
                        break
                except OSError:
                    pass
            if not held:
                return
            time.sleep(0.05)
        log().info("grabbing with key(s) still held after %.1fs wait", timeout)

    def _neutralize_keys_held_at_grab(self, ecodes) -> None:
        """Release any key physically held at the instant we grabbed.

        EVIOCGRAB mid-keystroke is the root of the recurring "a key got stolen
        and is spamming after a restart" bug: the compositor saw the key DOWN,
        then the grab steals the UP, so it stays logically pressed forever. We
        can't replay an UP we never received, so right after grabbing we read
        each device's currently-pressed keys (EVIOCGKEY) and emit an UP for each
        through the virtual device, leaving the compositor's key state clean.
        Harmless when nothing is held (active_keys() is empty)."""
        if self._ui is None:
            return
        held: set[int] = set()
        for d in self._devices:
            try:
                held.update(d.active_keys())
            except OSError as e:
                log().debug("active_keys(%s) failed: %s", getattr(d, "path", "?"), e)
        # The hotkey itself is consumed-not-forwarded, so never inject it.
        held.discard(self._keycode)
        if not held:
            return
        for code in held:
            try:
                self._ui.write(ecodes.EV_KEY, code, 0)
            except OSError as e:
                log().debug("anti-strand release of %d failed: %s", code, e)
        try:
            self._ui.syn()
        except OSError:
            pass
        log().info("anti-strand: released %d key(s) held during grab", len(held))

    def stop(self) -> None:
        self._stop.set()
        # Release any keys still held on the virtual device BEFORE closing it,
        # otherwise the compositor keeps them pressed (stuck-key autorepeat).
        if self._ui is not None:
            try:
                from evdev import ecodes
                for code in list(self._down):
                    self._ui.write(ecodes.EV_KEY, code, 0)
                self._ui.syn()
            except OSError as e:
                log().debug("uinput release-on-stop failed: %s", e)
        self._down.clear()
        for d in self._devices:
            try:
                d.ungrab()
            except OSError:
                pass
        self._close_devices()
        if self._ui is not None:
            try:
                self._ui.close()
            except OSError:
                pass
            self._ui = None

    # ---- internals ----------------------------------------------------

    def _open_keyboards(self, evdev, ecodes) -> list:
        keyboards = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except (OSError, PermissionError) as e:
                log().debug("skip %s: %s", path, e)
                continue
            # Skip virtual devices that exist for *output* — grabbing them
            # would intercept our own synthesized Ctrl+V paste events and
            # send them through a useless extra hop. Match on name.
            try:
                name = (dev.name or "").lower()
            except OSError:
                name = ""
            if any(skip in name for skip in (
                "voice-type virtual",  # us (if a previous instance lingered)
                "ydotool",             # ydotoold's output device
            )):
                try:
                    dev.close()
                except OSError:
                    pass
                continue
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            # Match: any device that has our hotkey AND some letter keys
            # (filters out e.g. power buttons that have a few keys but no
            # real keyboard surface).
            if self._keycode in caps and ecodes.KEY_A in caps:
                keyboards.append(dev)
            else:
                try:
                    dev.close()
                except OSError:
                    pass
        if not keyboards:
            raise RuntimeError(
                "no readable keyboard exposes the configured key. "
                "Add yourself to the 'input' group: "
                "`sudo usermod -aG input $USER` then log out and back in."
            )
        log().info("evdev found %d keyboard(s): %s",
                   len(keyboards), [d.name for d in keyboards])
        return keyboards

    def _merged_capabilities(self, ecodes) -> dict:
        """Union of EV_KEY codes across all real keyboards. UInput rejects
        EV_SYN being declared (it adds it automatically)."""
        all_keys = set()
        for d in self._devices:
            caps = d.capabilities().get(ecodes.EV_KEY, [])
            all_keys.update(caps)
        return {ecodes.EV_KEY: sorted(all_keys)}

    def _close_devices(self) -> None:
        for d in self._devices:
            try:
                d.close()
            except OSError:
                pass
        self._devices = []

    def _watch(self, dev) -> None:
        from evdev import ecodes
        try:
            for ev in dev.read_loop():
                if self._stop.is_set():
                    return
                # Hotkey: consume, never forward
                if ev.type == ecodes.EV_KEY and ev.code == self._keycode:
                    with self._pressed_lock:
                        if ev.value == 1 and not self._pressed:
                            self._pressed = True
                            fire = self._on_press
                        elif ev.value == 0 and self._pressed:
                            self._pressed = False
                            fire = self._on_release
                        else:
                            fire = None  # autorepeat (value=2), or dedup
                    if fire:
                        try:
                            fire()
                        except Exception as e:  # noqa: BLE001
                            log().exception("hotkey callback: %s", e)
                    continue
                # Everything else: forward to virtual keyboard verbatim.
                # ev.type may be EV_KEY (regular key), EV_SYN (frame end),
                # EV_MSC (scan codes), EV_LED (caps-lock light), etc.
                # Forward them all unmodified so libinput sees a normal
                # keyboard.
                if self._ui is None:
                    continue
                # Track held keys so stop() can release anything still down.
                if ev.type == ecodes.EV_KEY:
                    if ev.value == 1:
                        self._down.add(ev.code)
                    elif ev.value == 0:
                        self._down.discard(ev.code)
                try:
                    self._ui.write(ev.type, ev.code, ev.value)
                except OSError as e:
                    log().debug("uinput write failed: %s", e)
        except OSError as e:
            log().warning("evdev %s closed: %s", dev.path, e)


# Backwards-compatible alias for any external code that imports the old name.
EvdevHotkeyListener = EvdevReplayListener
