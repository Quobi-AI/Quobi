#!/usr/bin/env bash
# Drive a Windows Quobi build on a remote Windows machine/VM straight from the
# Linux dev host: snapshot current git HEAD -> sync to the VM -> run
# build-windows.ps1 there. One command to rebuild (and optionally hot-swap) the
# Windows app from whatever you've committed locally.
#
# Config via ENV (so no host/creds live in the repo):
#   VM_HOST   target host/IP            (e.g. 192.168.1.50)
#   VM_USER   ssh user on the VM        (e.g. winuser)
#   SSHPASS   the VM password           (used via sshpass; or set up key auth and drop it)
# Any extra ARGS are passed through to build-windows.ps1, e.g.:
#   VM_HOST=… VM_USER=… SSHPASS=… build-windows-remote.sh -Install
#   …                                  build-windows-remote.sh -Component gui -Install
#
# Prereqs on this host: git, sshpass, ssh/scp. The VM must already have the
# one-time toolchain (see BUILD.md §4): VS BuildTools, Rust, Bun, Python, etc.
set -euo pipefail

: "${VM_HOST:?set VM_HOST (target Windows host/IP)}"
: "${VM_USER:?set VM_USER (ssh user on the VM)}"
: "${SSHPASS:?set SSHPASS (VM password)}"
export SSHPASS

# repo root = scripts/ -> src-tauri -> voice-type-desktop -> <repo>
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SSH=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$VM_USER@$VM_HOST")
SCP=(sshpass -e scp -o StrictHostKeyChecking=accept-new)
# the noisy OpenSSH post-quantum banner goes to stderr on every call
strip_noise() { grep -ivE "post-quantum|store now|upgraded|vulnerable" || true; }

echo "== snapshot current HEAD =="
git -C "$REPO_ROOT" archive --format=tar.gz -o /tmp/quobi-src.tar.gz HEAD
echo "   $(du -h /tmp/quobi-src.tar.gz | cut -f1)"

echo "== sync -> VM C:\\quobi-src =="
"${SCP[@]}" /tmp/quobi-src.tar.gz "$VM_USER@$VM_HOST:C:/Users/$VM_USER/quobi-src.tar.gz" 2>&1 | strip_noise
# Incremental by default: extract OVER the existing tree so the build caches
# (cargo target/, Python .venv/, bun node_modules/, dist/, build/, winbundle/)
# survive -- that's what makes repeat builds minutes instead of ~10 min cold.
# Set CLEAN_SYNC=1 to wipe first for a pristine tree (e.g. after a tracked file
# was deleted/renamed and you want it gone from the VM copy too).
WIPE=""
if [ "${CLEAN_SYNC:-0}" = "1" ]; then WIPE="Remove-Item -Recurse -Force C:\\quobi-src -ErrorAction SilentlyContinue; "; fi
"${SSH[@]}" "powershell -NoProfile -Command \"${WIPE}New-Item -ItemType Directory -Force C:\\quobi-src | Out-Null; tar -xzf C:\\Users\\$VM_USER\\quobi-src.tar.gz -C C:\\quobi-src\"" 2>&1 | strip_noise

echo "== build on VM: build-windows.ps1 $* =="
"${SSH[@]}" "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\quobi-src\\voice-type-desktop\\src-tauri\\scripts\\build-windows.ps1 $*" 2>&1 | strip_noise

echo "== done =="
