#!/usr/bin/env bash
#
# Build the all-in-one Quobi AppImage from current source — one command.
#
#   ./build-appimage.sh             build the AppImage -> dist/Quobi-x86_64.AppImage
#   ./build-appimage.sh --install   also hot-swap the dev install (~/.local/bin)
#   ./build-appimage.sh --rollback  restore the previous AppImage from the backup
#
# Designed to be safe to run after ANY change, so iterating never bricks you:
#   * clears the stale /tmp/appimage_extracted_* dir that intermittently makes
#     linuxdeploy fail ("failed to run linuxdeploy")
#   * never EXECUTES the AppImage (AppImageLauncher would move/integrate it)
#   * never launches a bare `voice-type` (that starts a stray second daemon)
#   * keeps the last good build at *.prev so you can --rollback in one step
#   * doesn't touch the running daemon (you restart it yourself, on --install)
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DAEMON_DIR="$ROOT/voice-type"
DESKTOP_DIR="$ROOT/voice-type-desktop"
BUNDLE="$DESKTOP_DIR/src-tauri/linuxbundle"
APP_SRC="$DESKTOP_DIR/src-tauri/target/release/bundle/appimage/Quobi_0.1.0_amd64.AppImage"
GUI_BIN="$DESKTOP_DIR/src-tauri/target/release/quobi"
OUT_DIR="$ROOT/dist"
OUT="$OUT_DIR/Quobi-x86_64.AppImage"
UNIT='app-quobi\x2ddaemon@autostart.service'

c() { printf '\033[1;36m== %s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---- rollback ---------------------------------------------------------------
if [ "${1:-}" = "--rollback" ]; then
  [ -f "$OUT.prev" ] || die "no backup at $OUT.prev to roll back to"
  cp -f "$OUT.prev" "$OUT"; chmod +x "$OUT"
  ok "rolled back: $OUT  (restored from .prev)"
  exit 0
fi

INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

# ---- 0. preflight: the bundled Vulkan sidecars (built rarely, gitignored) ----
c "preflight — bundled sidecars"
[ -x "$BUNDLE/daemon/voice-type" ] || mkdir -p "$BUNDLE/daemon"
# STT (Parakeet) runs in-process via sherpa-onnx inside the daemon — no sidecar.
# Only the llama.cpp Vulkan cleanup server is bundled alongside the daemon.
[ -x "$BUNDLE/llama/llama-server" ] \
  || die "missing $BUNDLE/llama/  — provision the llama.cpp b9474 Vulkan build (see BUILD.md §3b)"
ok "  sidecars present"

# ---- 1. daemon (PyInstaller) ------------------------------------------------
c "building daemon"
( cd "$DAEMON_DIR" && make build )
cp -f "$DAEMON_DIR/dist/voice-type" "$BUNDLE/daemon/voice-type"
ok "  daemon staged into linuxbundle"

# ---- 2. desktop + AppImage (reliable incantation) ---------------------------
c "building desktop + AppImage"
rm -rf /tmp/appimage_extracted_* 2>/dev/null || true   # the linuxdeploy gotcha
( cd "$DESKTOP_DIR" && APPIMAGE_EXTRACT_AND_RUN=1 NO_STRIP=1 bun run tauri build )
[ -f "$APP_SRC" ] || die "AppImage was not produced — see the tauri output above"

# ---- 3. publish to dist/ with a rollback backup -----------------------------
c "publishing"
mkdir -p "$OUT_DIR"
[ -f "$OUT" ] && cp -f "$OUT" "$OUT.prev"   # keep the last good build
cp -f "$APP_SRC" "$OUT"; chmod +x "$OUT"
ok "  -> $OUT  ($(du -h "$OUT" | cut -f1))"
[ -f "$OUT.prev" ] && echo "  (previous build saved at $OUT.prev — ./build-appimage.sh --rollback)"

# ---- 4. optional dev hot-swap -----------------------------------------------
if [ "$INSTALL" = 1 ]; then
  c "installing dev binaries"
  install -m755 "$GUI_BIN" "$HOME/.local/bin/quobi"
  install -m755 "$DAEMON_DIR/dist/voice-type" "$HOME/.local/bin/voice-type"
  ok "  installed quobi + voice-type to ~/.local/bin"
  echo "  apply the new daemon with:  systemctl --user restart '$UNIT'"
fi

c "done"
echo "AppImage: $OUT"
printf '\033[0;33m%s\033[0m\n' \
  "Do NOT double-click the AppImage on THIS machine to test it — AppImageLauncher will move it. Ship the file above, or use --install for local dev."
