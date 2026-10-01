# Quobi pre-launch doctor — injected into the AppImage's AppRun by
# build-appimage.sh, sourced *before* the wrapped GUI is exec'd.
#
# Tauri's webview dlopens libwebkit2gtk-4.1.so.0 at process start, so a missing
# webkit means the window never paints and nothing inside the app can report
# why. This runs first, before that load, and tells the user exactly what to
# install — through whatever channel is available (terminal, dialog, notify,
# logfile), since a double-clicked AppImage has no terminal and this box may
# have no kdialog/zenity either.
#
# Healthy systems pay only two `ldconfig -p | grep` calls and continue silently.

_quobi_have_lib() {
  # True if the given soname resolves. ldconfig is the fast path; fall back to a
  # scan of the usual lib dirs if ldconfig is absent (rare, but musl/containers).
  if command -v ldconfig >/dev/null 2>&1; then
    ldconfig -p 2>/dev/null | grep -q "$1" && return 0
  fi
  for d in /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu /lib /lib64; do
    [ -e "$d/$1" ] && return 0
  done
  return 1
}

_quobi_pkg_hint() {
  # Per-distro install command for the missing webkit (+ tray) packages.
  if command -v pacman >/dev/null 2>&1; then
    echo "sudo pacman -S --needed webkit2gtk-4.1 libayatana-appindicator"
  elif command -v apt >/dev/null 2>&1; then
    echo "sudo apt install libwebkit2gtk-4.1-0 libayatana-appindicator3-1"
  elif command -v dnf >/dev/null 2>&1; then
    echo "sudo dnf install webkit2gtk4.1 libappindicator-gtk3"
  elif command -v zypper >/dev/null 2>&1; then
    echo "sudo zypper install libwebkit2gtk-4_1-0 libayatana-appindicator3-1"
  else
    echo "install the webkit2gtk-4.1 runtime for your distribution"
  fi
}

_quobi_report() {
  # Surface a fatal message through the first channel that works.
  local title="$1" body="$2" logf="$HOME/.local/state/quobi/preflight.log"
  mkdir -p "$(dirname "$logf")" 2>/dev/null || true
  printf '%s\n\n%s\n' "$title" "$body" > "$logf" 2>/dev/null || true

  if [ -t 2 ]; then
    printf '\n\033[1;31m%s\033[0m\n\n%s\n\n' "$title" "$body" >&2
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title "$title" --error "$body" >/dev/null 2>&1 || true
  elif command -v zenity >/dev/null 2>&1; then
    zenity --error --title="$title" --text="$body" >/dev/null 2>&1 || true
  elif command -v xmessage >/dev/null 2>&1; then
    printf '%s\n\n%s\n' "$title" "$body" | xmessage -center -file - >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -u critical "$title" "$body" >/dev/null 2>&1 || true
  fi
  # The logfile is always written as the last resort.
}

_quobi_preflight() {
  # webkit is fatal (no webview = no app). appindicator is optional (tray only),
  # so we don't block on it.
  if _quobi_have_lib "libwebkit2gtk-4.1.so.0"; then
    return 0
  fi
  local cmd
  cmd="$(_quobi_pkg_hint)"
  _quobi_report "Quobi can't start — missing WebKitGTK" \
"Quobi needs the WebKitGTK runtime (libwebkit2gtk-4.1.so.0), which isn't installed.

Install it, then launch Quobi again:

    $cmd

(Details written to ~/.local/state/quobi/preflight.log)"
  exit 1
}

_quobi_preflight
