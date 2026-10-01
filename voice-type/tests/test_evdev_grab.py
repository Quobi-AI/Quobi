#!/usr/bin/env python3
"""Grab-time key release for the evdev replay listener.

A key physically held when we EVIOCGRAB the keyboard strands in the compositor:
it saw the DOWN on the real device, and the grab steals the UP. The release has
to be written to that same real device before the grab; an UP on our virtual
keyboard is dropped by the kernel because the key was never down there.

Uses fake devices, so it needs no /dev/input access.

Run:  .venv/bin/python tests/test_evdev_grab.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

# make the package importable when run from repo root or tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evdev import ecodes  # noqa: E402

from voice_type.input import EvdevReplayListener  # noqa: E402


class FakeKeyboard:
    """Stands in for evdev.InputDevice; records calls into a shared journal."""

    def __init__(self, journal, held=(), writable=True, name="fake kbd"):
        self.journal = journal
        self.held = set(held)
        self.writable = writable
        self.name = name
        self.path = f"/dev/input/{name.replace(' ', '-')}"

    def active_keys(self):
        return sorted(self.held)

    def capabilities(self):
        return {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_LEFTSHIFT, ecodes.KEY_KPPLUS]}

    # No syn() here on purpose: the evdev the binary bundles (1.7.x) has it on
    # UInput only, so the listener must end the frame with a plain EV_SYN write.
    def write(self, etype, code, value):
        if not self.writable:
            raise OSError("no write access")
        self.journal.append((self.name, "write", etype, code, value))
        if etype == ecodes.EV_KEY and value == 0:
            self.held.discard(code)  # the kernel's key state follows the injected UP

    def grab(self):
        self.journal.append((self.name, "grab"))

    def ungrab(self):
        pass

    def read_loop(self):
        return iter(())

    def close(self):
        pass


class FakeUInput:
    def __init__(self, journal):
        self.journal = journal

    def write(self, etype, code, value):
        self.journal.append(("virtual", "write", etype, code, value))

    def syn(self):
        self.journal.append(("virtual", "syn"))

    def close(self):
        pass


def start_listener(keyboards, journal):
    listener = EvdevReplayListener("kp_plus", lambda: None, lambda: None)
    listener._open_keyboards = lambda evdev, ecodes: keyboards
    listener._wait_for_keys_released = lambda: None  # skip the 4s settle wait
    with mock.patch("evdev.UInput", lambda **kw: FakeUInput(journal)):
        listener.start()
    listener.stop()


class GrabReleaseTest(unittest.TestCase):
    def test_held_key_is_released_on_the_real_device_before_grab(self):
        journal = []
        kbd = FakeKeyboard(journal, held=[ecodes.KEY_LEFTSHIFT])
        start_listener([kbd], journal)

        release = (kbd.name, "write", ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
        frame_end = (kbd.name, "write", ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
        self.assertIn(release, journal)
        self.assertLess(journal.index(release), journal.index(frame_end))
        self.assertLess(journal.index(frame_end), journal.index((kbd.name, "grab")))

    def test_held_hotkey_is_released_too(self):
        # The compositor saw the hotkey go DOWN before the grab as well.
        journal = []
        kbd = FakeKeyboard(journal, held=[ecodes.KEY_KPPLUS])
        start_listener([kbd], journal)

        self.assertIn((kbd.name, "write", ecodes.EV_KEY, ecodes.KEY_KPPLUS, 0), journal)

    def test_only_the_device_holding_the_key_gets_the_release(self):
        journal = []
        holding = FakeKeyboard(journal, held=[ecodes.KEY_LEFTSHIFT], name="holding")
        idle = FakeKeyboard(journal, name="idle")
        start_listener([holding, idle], journal)

        self.assertEqual([e for e in journal if e[0] == "idle"], [("idle", "grab")])

    def test_fake_keyboard_matches_the_bundled_evdev_api(self):
        # Guards the fake itself: anything the listener calls on a real
        # keyboard must exist on the evdev version this venv (and so the
        # built binary) ships.
        import evdev
        for method in ("write", "grab", "ungrab", "active_keys", "capabilities",
                       "read_loop", "close"):
            self.assertTrue(hasattr(evdev.InputDevice, method), method)
            self.assertTrue(hasattr(FakeKeyboard, method), method)

    def test_nothing_held_writes_nothing(self):
        journal = []
        kbd = FakeKeyboard(journal)
        start_listener([kbd], journal)

        self.assertEqual([e for e in journal if e[1] == "write"], [])

    def test_read_only_device_is_still_grabbed(self):
        journal = []
        kbd = FakeKeyboard(journal, held=[ecodes.KEY_LEFTSHIFT], writable=False)
        start_listener([kbd], journal)

        self.assertIn((kbd.name, "grab"), journal)


if __name__ == "__main__":
    unittest.main()
