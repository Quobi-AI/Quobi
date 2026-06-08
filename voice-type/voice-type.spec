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

# faster-whisper pulls native libs (ctranslate2), PyAV, onnxruntime, and the
# tokenizers/huggingface stack. collect_all grabs their binaries + data so the
# frozen binary can run on-device transcription. (The whisper MODEL itself is
# downloaded to the user's HF cache at first run, not bundled.)
_fw_datas, _fw_bins, _fw_hidden = [], [], []
for _pkg in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface_hub"):
    try:
        d, b, h = collect_all(_pkg)
        _fw_datas += d; _fw_bins += b; _fw_hidden += h
    except Exception:
        pass

# ctranslate2.dll has sibling DLL dependencies (e.g. libiomp5md.dll on Windows)
# that collect_all can miss -> "Failed to load dynlib ctranslate2.dll" at runtime.
# Force EVERY dll in the ctranslate2 package dir into the bundle's ctranslate2/.
try:
    _ct_dir = get_package_paths("ctranslate2")[1]
    _fw_bins += [(_f, "ctranslate2") for _f in glob.glob(os.path.join(_ct_dir, "*.dll"))]
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
