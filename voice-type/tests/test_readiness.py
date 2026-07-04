#!/usr/bin/env python3
"""Unit tests for readiness classification + JSON writing.

Dependency-free (no pytest required):  python tests/test_readiness.py

Pins the message->code mapping to the ACTUAL error strings the daemon raises
today, so a reworded RuntimeError that breaks classification is caught here
rather than by a confused user staring at the GUI.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_type import readiness as R  # noqa: E402

# (verbatim message from the code path, expected code). Keep these copied from
# the real raise sites so they fail loudly if the wording drifts.
CASES = [
    # input.py:487
    ("no readable keyboard exposes the configured key. Add yourself to the "
     "'input' group: `sudo usermod -aG input $USER` then log out and back in.",
     R.CODE_NO_INPUT_GROUP),
    # input.py:343
    ("cannot create uinput device: [Errno 13] Permission denied. Run "
     "'make uinput-setup' once to install the udev rule", R.CODE_UINPUT_DENIED),
    # output.py:475
    ("ydotoold socket not found (tried: /tmp/.ydotool_socket). Set it up with "
     "'make ydotool-setup', or 'sudo systemctl enable --now ydotoold'.",
     R.CODE_YDOTOOL_SOCKET_MISSING),
    # output.py:294
    ("wtype installed but the compositor refused virtual-keyboard (Compositor "
     "does not support the virtual keyboard protocol). Install ydotool instead.",
     R.CODE_WTYPE_REFUSED),
    # output.py:287
    ("missing tools for Wayland: wtype, wl-copy, wl-paste. Install 'wtype' and "
     "'wl-clipboard' (or use ydotool - see README).", R.CODE_MISSING_WL_CLIPBOARD),
    # transcribe_parakeet.py
    ("Parakeet model file not found in .../parakeet/english (looked for "
     "encoder.int8.onnx, encoder.onnx). Download the Parakeet ONNX bundle.",
     R.CODE_PARAKEET_MISSING),
    # output.py:663 - generic funnel
    ("no working Wayland output backend on kde: ydotool not found - install "
     "'ydotool' (see README).", R.CODE_NO_OUTPUT_BACKEND),
    # unknown
    ("something totally unexpected blew up", R.CODE_UNKNOWN),
]


def test_classify() -> None:
    for msg, expected in CASES:
        got = R.classify(msg)
        assert got == expected, f"classify({msg[:40]!r}...) = {got!r}, want {expected!r}"
    print(f"  classify: {len(CASES)} cases OK")


def test_write_readiness_shape() -> None:
    with tempfile.TemporaryDirectory() as d:
        os.environ["XDG_STATE_HOME"] = d
        # fatal
        R.write_readiness(
            ok=False, session="wayland", de="kde",
            output_backend="wayland-ydotool", hotkey_backend="evdev",
            error_code=R.CODE_NO_INPUT_GROUP, error_backend="hotkey",
            error_message="no readable keyboard ...",
        )
        data = json.loads(R.readiness_path().read_text())
        assert data["ok"] is False
        assert data["session"] == "wayland" and data["de"] == "kde"
        assert data["error"]["code"] == R.CODE_NO_INPUT_GROUP
        assert data["error"]["backend"] == "hotkey"
        assert isinstance(data["ts"], int)
        # success clears the error object
        R.write_readiness(
            ok=True, session="wayland", de="kde",
            output_backend="wayland-ydotool", hotkey_backend="evdev",
        )
        data = json.loads(R.readiness_path().read_text())
        assert data["ok"] is True and data["error"] is None
    print("  write_readiness: shape OK")


if __name__ == "__main__":
    test_classify()
    test_write_readiness_shape()
    print("all readiness tests passed")
