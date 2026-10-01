#!/usr/bin/env python3
"""Decision logic of the post-install check (`voice-type --check`).

Covers the pure parts: reading the daemon's startup state out of its log, and
deciding from compositor + kernel key state whether something is stuck. The
probes themselves (evdev, KWin) are exercised by running the check for real.

Run:  .venv/bin/python tests/test_selfcheck.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# make the package importable when run from repo root or tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_type import selfcheck  # noqa: E402

START = "13:59:43 [INFO] voice-type 0.2.0 starting (session=wayland config=/x/config.toml)"
READY = "13:59:52 [INFO] ready. hotkey=kp_plus mode=toggle output=wayland-ydotool/paste cleanup=on overlay=on"
STOP = "14:12:57 [INFO] signal 15 -> shutting down"
ERROR = "13:47:25 [ERROR] hotkey listener init failed: no evdev mapping for 'kp_plus'"

LSHIFT, RSHIFT, LCTRL, KEY_A = 42, 54, 29, 30


class LogStateTest(unittest.TestCase):
    def test_current_run_starts_at_the_last_start_line(self):
        lines = [START, READY, STOP, START, "loading"]
        self.assertEqual(selfcheck.current_run(lines), [START, "loading"])

    def test_no_start_line_means_no_run(self):
        self.assertEqual(selfcheck.current_run(["something", READY]), [])

    def test_states(self):
        self.assertEqual(selfcheck.log_state([]), "none")
        self.assertEqual(selfcheck.log_state([START]), "starting")
        self.assertEqual(selfcheck.log_state([START, READY]), "ready")
        self.assertEqual(selfcheck.log_state([START, READY, STOP]), "stopped")
        self.assertEqual(selfcheck.log_state([START, ERROR]), "error")

    def test_error_after_ready_does_not_undo_ready(self):
        late = "15:00:00 [ERROR] transcription failed: boom"
        self.assertEqual(selfcheck.log_state([START, READY, late]), "ready")


class StrandedModifierTest(unittest.TestCase):
    def test_pressed_in_compositor_but_held_nowhere_is_stranded(self):
        pressed = {"Shift": True, "Control": False, "Alt": False, "Meta": False}
        self.assertEqual(selfcheck.stranded_modifiers(pressed, set()), ["Shift"])

    def test_modifier_the_user_is_really_holding_is_not_stranded(self):
        pressed = {"Shift": True, "Control": True}
        self.assertEqual(selfcheck.stranded_modifiers(pressed, {RSHIFT, LCTRL}), [])

    def test_unrelated_held_key_does_not_excuse_a_stranded_modifier(self):
        self.assertEqual(selfcheck.stranded_modifiers({"Shift": True}, {KEY_A}), ["Shift"])

    def test_nothing_pressed_nothing_stranded(self):
        self.assertEqual(selfcheck.stranded_modifiers({"Shift": False}, {LSHIFT}), [])


class StuckVirtualKeyTest(unittest.TestCase):
    def test_key_down_only_on_the_virtual_keyboard_is_stuck(self):
        held = {"voice-type virtual keyboard": {KEY_A}, "Cherry": set()}
        self.assertEqual(selfcheck.stuck_virtual_keys(held),
                         {"voice-type virtual keyboard": {KEY_A}})

    def test_key_the_real_keyboard_also_holds_is_just_being_replayed(self):
        held = {"voice-type virtual keyboard": {KEY_A}, "Cherry": {KEY_A}}
        self.assertEqual(selfcheck.stuck_virtual_keys(held), {})

    def test_ydotool_device_counts_as_virtual(self):
        held = {"ydotoold virtual device": {LCTRL}, "Cherry": set()}
        self.assertEqual(selfcheck.stuck_virtual_keys(held),
                         {"ydotoold virtual device": {LCTRL}})

    def test_key_held_on_a_real_keyboard_is_not_our_problem(self):
        self.assertEqual(selfcheck.stuck_virtual_keys({"Cherry": {LSHIFT}}), {})


class ModstateParseTest(unittest.TestCase):
    def test_parses_the_probe_line(self):
        out = "qml: MODSTATE Shift=true Control=false Alt=false Meta=false\n"
        self.assertEqual(selfcheck.parse_modstate(out),
                         {"Shift": True, "Control": False, "Alt": False, "Meta": False})

    def test_no_probe_line_means_unavailable(self):
        self.assertIsNone(selfcheck.parse_modstate("module not installed\n"))


if __name__ == "__main__":
    unittest.main()
