# System Readiness & Auto-Remediation — Design

Date: 2026-07-04
Status: approved (brainstorming) — ready for implementation plan

## Problem

Fresh installs of Quobi on Wayland (KDE especially) silently fail: F9 does
nothing, or the daemon won't stay running, with no in-app explanation. The
daemon already *detects* every cause and emits excellent `RuntimeError` /
log messages, but those live in `~/.local/state/voice-type/voice-type.log`,
which users never open.

The five real-world failures observed on a fresh KDE Wayland Arch box:

1. **Missing GUI runtime lib** — `libwebkit2gtk-4.1.so.0` (and
   `libayatana-appindicator`) absent → the Tauri app never launches. The
   dynamic linker fails *before* Rust `main()` runs, so nothing inside the app
   can catch it.
2. **Model download stalled** — already handled by the existing `SetupBanner`
   (resumable-ish; self-heals). Out of scope here except to not regress it.
3. **wtype rejected by the compositor** — KWin refuses `virtual-keyboard-v1`.
   Already auto-handled: `output.make_backend()` falls through wtype → ydotool.
4. **ydotool not installed / no daemon** — needs a package install + the
   ydotoold user service running.
5. **`input` group membership (the big one)** — the user must be in `input`
   for evdev to read the keyboard (hotkey) *and* for uinput writes (typing).
   Requires root to add, and a **logout/login** to take effect.

### Ground truth captured from the repro box (2026-07-04)

- Distro: Arch Linux, KDE Plasma Wayland, `pkexec` present.
- `kdialog` / `zenity` / `notify-send` **not** present — a pre-launch dialog
  cannot assume any graphical dialog tool exists.
- `/usr/lib/udev/rules.d/80-uinput.rules` ships by default on modern systemd
  and sets `GROUP="input", MODE="0660"`. So **`input`-group membership is the
  single master fix**; installing a custom udev rule is a legacy fallback, not
  needed on modern distros. We probe uinput *writability* rather than assume.
- ydotoold is a **user** service (`/usr/lib/systemd/user/ydotool.service`);
  starting it needs no root — just `systemctl --user enable --now ydotool`.
- The daemon log confirms the exact failure signatures and that a failed
  hotkey listener **takes the whole daemon down** (crash-loop), which today
  surfaces only as `StatusPanel` showing `daemon_running: false` with no cause.

## Goals

- Turn every detectable readiness problem into an in-app, actionable card.
- Auto-fix everything that is *safely* automatable (max automation the user
  asked for), via one consolidated `pkexec` prompt where root is needed.
- Never fake the un-automatable: a group-add relogin and a missing system
  library are surfaced honestly with the exact command.
- Catch the one fatal, pre-launch case (#1 webkit) before the window paints.

## Non-goals

- Auto-driving an unknown distro's package manager as root (Arch `pacman`
  offered as one-click; other distros get a copy-paste block).
- Changing dictation behavior or the existing model-download flow.
- Bundling webkit into the AppImage (too large; guidance instead).

## Architecture — two detection surfaces, split by *when they can run*

### Surface 1 — Pre-launch doctor (bash, in AppRun)

The only place that can catch **#1** (missing shared libs), because the dynamic
linker fails before Rust `main()`. A post-build step in `build-appimage.sh`
extracts the Tauri-produced AppImage, injects a preflight snippet into the
linuxdeploy `AppRun` (before `exec "$this_dir"/AppRun.wrapped "$@"`), and
repacks with `appimagetool`.

Preflight logic:

- Resolve the critical libs with `ldconfig -p` / a dlopen probe:
  `libwebkit2gtk-4.1.so.0`, `libayatana-appindicator3.so.1`.
- If a **fatal** lib (webkit) is missing: write
  `~/.local/state/quobi/preflight.log`, then surface the message + exact
  per-distro install command via the **first available channel**:
  1. terminal (if stdout is a tty) — plain print,
  2. `kdialog` / `zenity` / `xmessage` if present,
  3. `notify-send` if present,
  4. always leave the report file as the last resort.
  On Arch, offer to run `pkexec pacman -S --needed webkit2gtk-4.1
  libayatana-appindicator` (only when a terminal/dialog can collect consent).
  Then exit non-zero.
- If all fatal libs resolve: `exec` the wrapped GUI unchanged (zero behavior
  change on healthy systems).

This surface is **separable** (implementation phase 4) so the in-app core can
ship first.

### Surface 2 — In-GUI readiness panel

Everything catchable once the window is up. Two data sources merged:

- **Rust probes** (static prerequisites, checkable anytime, drive the fix
  buttons): `input`-group membership, `/dev/uinput` writability, presence of
  `ydotool` / `wl-copy` / `wl-paste` on PATH, ydotoold socket present, ydotoold
  user-service state.
- **Daemon self-report** `readiness.json` (dynamic facts only the daemon
  knows): which output/hotkey backend it actually selected, and the
  machine-readable code of its last fatal error (e.g. `wtype_refused`,
  `no_input_group`).

## Section C — Daemon self-report: `readiness.json`

`voice_type/__main__.py` writes `~/.local/state/voice-type/readiness.json` at
the end of every startup attempt (success or fatal). Additive only — no
behavior change to dictation.

```json
{
  "ts": 1720104544,
  "session": "wayland",
  "de": "kde",
  "output_backend": "wayland-ydotool",
  "hotkey_backend": "evdev",
  "ok": false,
  "error": { "code": "no_input_group", "backend": "hotkey", "message": "..." }
}
```

Error codes (enum), mapped from the existing `RuntimeError` call-sites:

| code | source |
|---|---|
| `no_input_group` | `input.py` "no readable keyboard exposes the configured key" |
| `uinput_denied` | `input.py` "cannot create uinput device" |
| `no_output_backend` | `output.py` "no working Wayland output backend" |
| `ydotool_socket_missing` | `output.py` "ydotoold socket not found" |
| `wtype_refused` | `output.py` "compositor refused virtual-keyboard" (informational; not fatal once ydotool works) |
| `missing_wl_clipboard` | `output.py` missing wl-copy/wl-paste |
| `parakeet_missing` | `transcribe_parakeet.py` model file not found |

Implementation: a small helper `voice_type/readiness.py` with a
`write_readiness(...)` function and a `ReadinessError(code, backend, message)`
exception (or a mapping from message → code) so `__main__.py` can record the
outcome in one place around the existing startup try/except.

## Section B — Auto-fix flow

The panel computes remediations, each classified by privilege. One
**"Fix everything"** button plus per-item buttons.

| Fix | Mechanism | Privilege | Residual |
|---|---|---|---|
| Start ydotoold | `systemctl --user enable --now ydotool` | none | — |
| Add to `input` group | `pkexec quobi-setup --add-input-group` | 1 polkit prompt | **relogin** |
| uinput not writable (old distro only) | same `pkexec quobi-setup` installs `60-quobi-uinput.rules` + `udevadm trigger`, gated on the writability probe | 1 polkit prompt | maybe relogin |
| Missing packages | Arch → `pkexec pacman -S --needed <pkgs>`; else copy-paste block with correct names | 1 polkit prompt (Arch) | — |

Rules:

- **One consolidated `pkexec`**: a bundled `quobi-setup` helper script does all
  root work (group add + optional udev rule) so the user sees a single polkit
  prompt. Shipped in the Tauri resource dir and copied to a stable path by
  `ensure_install` (same pattern as the daemon binary).
- **The relogin is never faked.** After a group add, the item flips to a
  persistent "Log out and back in to finish" state, with a "Log out now" button
  when `qdbus`/`loginctl terminate-user` is available (else instructions). The
  panel re-checks on next launch and clears it once membership is live.
- **`pacman` auto-install is Arch-only.** Detect the package manager; other
  distros get a copy-paste block. Never drive an unknown PM as root.

## Section D — Panel UI

New `src/components/ReadinessPanel.tsx` (sibling of `SetupBanner`), rendered
above `StatusPanel` in `App.tsx`.

- All green → renders nothing (no nagging). Optionally a one-line dismissible
  "System ready ✓".
- Any problem → one card per issue: fail/warn icon, one-line plain-language
  explanation, and the right action (Fix button, "relogin" note, or copy
  block).
- Data via new Tauri commands in a new `src-tauri/src/readiness.rs`:
  - `get_readiness() -> Readiness` (merges probes + `readiness.json`)
  - `fix_input_group() -> Result<(), String>` (runs `pkexec quobi-setup ...`)
  - `start_ydotoold() -> Result<(), String>`
  - `install_packages(pkgs) -> Result<(), String>` (Arch pkexec pacman)
- Reuses `StatusPanel`'s visual language (borders, `text-fg-soft`, mono sizes)
  and the existing GUI polling cadence.

## Files touched

New:
- `voice-type/voice_type/readiness.py` — dataclass + `write_readiness()` + code mapping
- `voice-type-desktop/src-tauri/src/readiness.rs` — probes + fixers + Tauri commands
- `voice-type-desktop/src/components/ReadinessPanel.tsx` — the panel
- `voice-type-desktop/src-tauri/linuxbundle/quobi-setup` — pkexec helper script
- (phase 4) an AppRun preflight snippet + `build-appimage.sh` post-processing

Modified:
- `voice-type/voice_type/__main__.py` — record startup outcome to `readiness.json`
- `voice-type-desktop/src-tauri/src/lib.rs` — register readiness commands
- `voice-type-desktop/src-tauri/src/daemonctl.rs` — copy `quobi-setup` in `ensure_install`
- `voice-type-desktop/src/App.tsx` — render `ReadinessPanel`
- `voice-type-desktop/src/lib/api.ts` — TS bindings for the new commands
- `build-appimage.sh` — (phase 4) inject the AppRun preflight

## Implementation phases

1. **Daemon self-report** — `readiness.py` + `__main__.py` writes
   `readiness.json` with error codes. (Testable in isolation, no GUI.)
2. **Rust probes + fixers + `quobi-setup`** — `readiness.rs`, register in
   `lib.rs`, copy helper in `ensure_install`.
3. **Panel** — `ReadinessPanel.tsx` + `api.ts` bindings + `App.tsx` wiring.
4. **Pre-launch doctor** — AppRun preflight + `build-appimage.sh` repack.
   Separable; ship 1–3 first if desired.

## Testing

- **Phase 1**: unit-test the message→code mapping and `write_readiness` against
  each known `RuntimeError` string; assert the JSON shape.
- **Phase 2**: unit-test the probe pure-logic (parsing `id`/group output,
  package-manager detection) with fixtures; the `pkexec` calls are thin shells
  tested by inspection + live-box run.
- **Phase 3**: manual/visual — the panel renders the right cards for each state.
- **End-to-end**: on the live KDE Wayland Arch box (100.90.150.69), reproduce a
  degraded state (e.g. temporarily drop from `input` group) and confirm the
  panel detects it, the fix runs, and the relogin note appears.
