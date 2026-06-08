#!/usr/bin/env bash
# Build whisper.cpp with the GGML Vulkan backend on Linux and stage
# whisper-server + its .so's into linuxbundle/whisper/ for the AppImage.
#
# The any-GPU / no-CUDA transcription server. A GPU is NOT needed to build
# (shaders compile to SPIR-V via glslc); only to run/verify acceleration.
#
# Prereqs (Arch): pacman -S --needed cmake gcc git shaderc glslang \
#                            vulkan-headers vulkan-icd-loader
# Usage: build-whisper-linux.sh [ref]   (ref defaults to the pinned commit)
set -euo pipefail

REF="${1:-a8ec021}"   # keep in lockstep with build-whisper-windows.ps1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_TAURI="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$SRC_TAURI/linuxbundle/whisper"
WORK="${TMPDIR:-/tmp}/quobi-whisper-build"

mkdir -p "$WORK"; cd "$WORK"
if [ ! -d whisper.cpp ]; then git clone https://github.com/ggml-org/whisper.cpp.git; fi
cd whisper.cpp
git fetch --all --tags >/dev/null 2>&1 || true
git checkout "$REF"

cmake -B build -DGGML_VULKAN=1 -DCMAKE_BUILD_TYPE=Release \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON
cmake --build build -j --config Release

mkdir -p "$DEST"
rm -f "$DEST"/*
cp -av build/bin/whisper-server "$DEST/"
# co-locate every ggml/whisper .so (mirrors the llama-vulkan layout). vulkan
# loader (libvulkan.so.1) is a system lib — not bundled.
find build \( -name 'libggml*.so*' -o -name 'libwhisper*.so*' \) -exec cp -av {} "$DEST/" \;

echo "Staged into $DEST:"
ls -la "$DEST"
echo "Next: bun run tauri build (NO_STRIP=1) — linuxbundle/whisper/ is now bundled."
