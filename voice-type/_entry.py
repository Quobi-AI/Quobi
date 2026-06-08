"""PyInstaller entry shim.

PyInstaller runs its target file as a top-level script, not as a module of
its package — so `from .config import load` style imports in voice_type/__main__.py
explode with `attempted relative import with no known parent package`.

This wrapper sits outside the package, imports the package proper, and
invokes main() the normal way. `python -m voice_type` from source still
works through voice_type/__main__.py directly.
"""
# CRITICAL: must run before anything that uses multiprocessing. faster-whisper
# / ctranslate2 / onnxruntime spawn worker processes; in a PyInstaller-frozen
# binary, each spawned worker re-executes this entry from the top. Without
# freeze_support() that means every worker launches a *new daemon* — an
# unbounded cascade. freeze_support() makes spawned workers behave as workers
# and return immediately instead of re-running main().
import multiprocessing
multiprocessing.freeze_support()

import sys

from voice_type.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
