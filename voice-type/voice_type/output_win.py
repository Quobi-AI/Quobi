"""Windows output backend — clipboard + Ctrl+V paste, Unicode typing, and
backspace erase via the Win32 clipboard / SendInput APIs through ctypes.

No extra dependencies (no pywin32) so it bundles cleanly with PyInstaller. Mirrors
the X11/Wayland OutputBackend interface, so it's a drop-in on Windows.

IMPORTANT: every `ctypes.windll` reference lives INSIDE a function/method, never
at module top level, so importing this module on Linux/macOS (e.g. for a syntax
check) does not blow up. In practice make_backend() imports it lazily only when
detect_session() == "windows".
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Callable

from .log import log
from .output import OutputBackend, _RestoreScheduler

# --- Win32 constants -------------------------------------------------------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56
VK_BACK = 0x08


# --- SendInput structs (union sized by the largest member, MOUSEINPUT) -----
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


# Lazily-built, signature-correct Win32 handles. Setting argtypes/restype is NOT
# optional: on 64-bit Windows an unset restype defaults to c_int (32-bit), which
# TRUNCATES pointer/handle returns (GetClipboardData, GlobalAlloc, SetClipboardData)
# → silent corruption or crashes. use_last_error=True makes get_last_error() valid.
# Referenced only at call time (Windows), so importing this module on Linux is safe.
_WIN = None
def _win():
    global _WIN
    if _WIN is not None:
        return _WIN
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    H = wintypes.HANDLE
    u.OpenClipboard.argtypes = [wintypes.HWND]; u.OpenClipboard.restype = wintypes.BOOL
    u.CloseClipboard.argtypes = []; u.CloseClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.argtypes = []; u.EmptyClipboard.restype = wintypes.BOOL
    u.GetClipboardData.argtypes = [wintypes.UINT]; u.GetClipboardData.restype = H
    u.SetClipboardData.argtypes = [wintypes.UINT, H]; u.SetClipboardData.restype = H
    u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    u.SendInput.restype = wintypes.UINT
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]; k.GlobalAlloc.restype = H
    k.GlobalLock.argtypes = [H]; k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [H]; k.GlobalUnlock.restype = wintypes.BOOL
    _WIN = (u, k)
    return _WIN


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(events: list[_INPUT]) -> None:
    if not events:
        return
    u, _ = _win()
    n = len(events)
    arr = (_INPUT * n)(*events)
    sent = u.SendInput(n, arr, ctypes.sizeof(_INPUT))
    if sent != n:
        log().debug("SendInput sent %d/%d (err=%s)", sent, n, ctypes.get_last_error())


# --- clipboard (CF_UNICODETEXT) --------------------------------------------
class _WinClipboard:
    """read() -> bytes (utf-8); write(bytes|str). Matches _X11Clipboard's shape
    so _RestoreScheduler can drive it unchanged."""

    def _open(self) -> bool:
        u, _ = _win()
        for _ in range(10):                     # clipboard is a shared lock; retry
            if u.OpenClipboard(None):
                return True
            time.sleep(0.01)
        return False

    def read(self) -> bytes:
        u, k = _win()
        if not self._open():
            return b""
        try:
            h = u.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return b""
            ptr = k.GlobalLock(h)
            if not ptr:
                return b""
            try:
                return ctypes.wstring_at(ptr).encode("utf-8")
            finally:
                k.GlobalUnlock(h)
        finally:
            u.CloseClipboard()

    def write(self, data) -> None:
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        u, k = _win()
        if not self._open():
            raise RuntimeError("could not open the Windows clipboard")
        try:
            u.EmptyClipboard()
            buf = text.encode("utf-16-le") + b"\x00\x00"   # NUL-terminated wide string
            h = k.GlobalAlloc(GMEM_MOVEABLE, len(buf))
            if not h:
                raise RuntimeError("GlobalAlloc failed")
            ptr = k.GlobalLock(h)
            ctypes.memmove(ptr, buf, len(buf))
            k.GlobalUnlock(h)
            if not u.SetClipboardData(CF_UNICODETEXT, h):
                raise RuntimeError("SetClipboardData failed")
            # ownership of `h` transfers to the system on success — don't free it.
        finally:
            u.CloseClipboard()


class WindowsBackend(OutputBackend):
    """Clipboard + Ctrl+V paste (works in virtually every Windows app, including
    Windows Terminal and modern conhost). type_text falls back to Unicode
    keystrokes; erase sends backspaces. terminal_aware is accepted for interface
    parity but Ctrl+V is the correct paste chord across Windows targets, so it's
    effectively a no-op here."""

    name = "windows"

    def __init__(self, terminal_aware: bool = True) -> None:
        # Build + signature-check the Win32 handles once so a non-Windows or
        # misconfigured environment fails fast at init.
        try:
            _win()
        except (AttributeError, OSError, FileNotFoundError) as e:   # not Windows / no DLLs
            raise RuntimeError("WindowsBackend requires Windows (user32/kernel32)") from e
        self._clipboard = _WinClipboard()
        self._restore = _RestoreScheduler(self._clipboard.read, self._clipboard.write)
        self._terminal_aware = terminal_aware

    def type_text(self, text: str) -> None:
        events: list[_INPUT] = []
        for ch in text:
            code = ord(ch)
            # UTF-16 surrogate pairs (emoji etc.) need two scan events each.
            units = [code] if code <= 0xFFFF else [
                0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
            for u in units:
                events.append(_key_event(scan=u, flags=KEYEVENTF_UNICODE))
                events.append(_key_event(scan=u, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        _send(events)

    def paste_text(
        self, text: str, restore_delay_sec: float, *, keep_clipboard: bool = False,
    ) -> None:
        if not keep_clipboard:
            self._restore.capture()
        self._clipboard.write(text)
        try:
            _send([
                _key_event(vk=VK_CONTROL),
                _key_event(vk=VK_V),
                _key_event(vk=VK_V, flags=KEYEVENTF_KEYUP),
                _key_event(vk=VK_CONTROL, flags=KEYEVENTF_KEYUP),
            ])
        finally:
            if not keep_clipboard:
                self._restore.schedule(restore_delay_sec)

    def erase(self, n: int) -> None:
        if n <= 0:
            return
        events: list[_INPUT] = []
        for _ in range(n):
            events.append(_key_event(vk=VK_BACK))
            events.append(_key_event(vk=VK_BACK, flags=KEYEVENTF_KEYUP))
        _send(events)

    def set_clipboard(self, text: str) -> None:
        self._clipboard.write(text)
