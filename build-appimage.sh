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
APP_SRC="$DESKTOP_DIR/src-tauri/target/release/bundle/appimage/Quobi_0.2.0_amd64.AppImage"
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

# ---- 3b. inject the pre-launch doctor into AppRun ---------------------------
# Tauri's webview dlopens libwebkit2gtk-4.1.so.0 before our code runs, so a
# missing webkit means the window never paints and nothing in-app can say why.
# We prepend a preflight (apprun-preflight.sh) to the AppImage's AppRun so it
# checks for webkit first and tells the user exactly what to install. Fail-soft:
# if appimagetool can't be found, ship the AppImage as-is rather than break the build.
inject_preflight() {
  local target="$1"
  local preflight="$DESKTOP_DIR/src-tauri/apprun-preflight.sh"
  [ -f "$preflight" ] || { echo "  (no apprun-preflight.sh; skipping doctor)"; return 0; }

  # Locate appimagetool: PATH first, else the copy inside Tauri's cached
  # linuxdeploy-plugin-appimage AppImage.
  local tool; tool="$(command -v appimagetool || true)"
  if [ -z "$tool" ]; then
    local lp="$HOME/.cache/tauri/linuxdeploy-plugin-appimage.AppImage"
    if [ -x "$lp" ]; then
      local ex; ex="$(mktemp -d)"
      ( cd "$ex" && "$lp" --appimage-extract >/dev/null 2>&1 ) || true
      tool="$ex/squashfs-root/usr/bin/appimagetool"
    fi
  fi
  [ -x "$tool" ] || { printf '  \033[0;33m%s\033[0m\n' "appimagetool not found — shipping without the pre-launch doctor"; return 0; }

  local work; work="$(mktemp -d)"
  # --appimage-extract is handled by the AppImage runtime (AppImageLauncher
  # passes it through), so this doesn't trigger desktop integration.
  ( cd "$work" && "$target" --appimage-extract >/dev/null 2>&1 ) \
    || { echo "  extract failed; skipping doctor injection"; rm -rf "$work"; return 0; }
  cp "$preflight" "$work/squashfs-root/.quobi-preflight.sh"
  if ! grep -q 'quobi-preflight.sh' "$work/squashfs-root/AppRun"; then
    sed -i 's|^exec "$this_dir"/AppRun.wrapped "$@"|source "$this_dir"/.quobi-preflight.sh\nexec "$this_dir"/AppRun.wrapped "$@"|' \
      "$work/squashfs-root/AppRun"
  fi
  if ARCH=x86_64 "$tool" --no-appstream "$work/squashfs-root" "$target.new" >/dev/null 2>&1; then
    mv -f "$target.new" "$target"; chmod +x "$target"
    ok "  pre-launch doctor injected"
  else
    echo "  repack failed; shipping original AppImage"
  fi
  rm -rf "$work"
}
c "injecting pre-launch doctor"
inject_preflight "$OUT"

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
