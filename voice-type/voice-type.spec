# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec — produces a single self-contained `voice-type` binary.
import sys, os, glob
from PyInstaller.utils.hooks import collect_all, get_package_paths

# Platform-specific backends for pynput/pystray (PyInstaller's static analysis
# misses the lazily-selected OS backend). On Windows we need the _win32 variants
# + the Windows output backend; on Linux the _xorg variants + evdev.
if sys.platform == "win32":
    _platform_hidden = [
        "pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32",
        "voice_type.output_win",
    ]
else:
    _platform_hidden = [
        "pynput.keyboard._xorg", "pynput.mouse._xorg", "pystray._xorg", "evdev",
    ]

# On Windows the daemon is a background service — build it as a windowed (no-
# console) binary so launching it never flashes a conhost window. (The GUI reads
# download progress from a JSON file, not stdout; logs go to the log file.) On
# Linux/macOS console has no window concept; keep it for terminal debugging.
_console = sys.platform != "win32"

# Windows: embed a version resource so Task Manager / Properties show
# "Quobi Dictation Service" instead of a bare "voice-type.exe". Ignored on
# non-Windows (no PE version resource concept there).
_version_file = "packaging/version_win.txt" if sys.platform == "win32" else None

# sherpa_onnx (the Parakeet STT backend) ships its own native libs
# (libsherpa-onnx-*, its bundled onnxruntime) that collect_all must pull in, or
# the frozen binary can't `import sherpa_onnx`. The STT MODEL itself is downloaded
# to the user's models dir on first run, not bundled.
_fw_datas, _fw_bins, _fw_hidden = [], [], []
for _pkg in ("sherpa_onnx",):
    try:
        d, b, h = collect_all(_pkg)
        _fw_datas += d; _fw_bins += b; _fw_hidden += h
    except Exception:
        pass

a = Analysis(
    ["_entry.py"],
    pathex=["."],
    binaries=_fw_bins,
    # Bundle WhisperFlowClone/shared/ into the binary so the daemon can load
    # the cleanup prompt + voice-command data from a stable path at runtime
    # (`sys._MEIPASS/shared/`). Same files the Android app reads.
    datas=[("../shared", "shared")] + _fw_datas,
    hiddenimports=[
        # Backends loaded lazily by their respective libraries; PyInstaller's
        # static analysis misses these, so list them explicitly. The OS-specific
        # pynput/pystray backends + evdev/output_win come from _platform_hidden.
        "PIL.Image",
        "PIL.ImageDraw",
        "_sounddevice",
    ] + _platform_hidden + _fw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tkinter is intentionally NOT excluded — the floating overlay needs it.
    excludes=[
        "test",
        "unittest",
        "pydoc_data",
        "IPython",
        "matplotlib",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="voice-type",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=_console,
    version=_version_file,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
