//! System-readiness detection + one-click remediation for the dashboard.
//!
//! Fresh installs on Wayland (KDE especially) fail silently: the hotkey does
//! nothing, or the daemon crash-loops, because the user isn't in the `input`
//! group, ydotoold isn't running, or a runtime package is missing. The daemon
//! records its own startup outcome to `readiness.json`; here we ALSO probe the
//! static prerequisites directly (so we can guide the user before the daemon has
//! ever run) and expose one-click fixes.
//!
//! Everything here is Linux-shaped. On Windows there are no groups / uinput /
//! ydotool, so `get_readiness` reports all-clear and the fixers are no-ops.

use serde::Serialize;
use std::path::PathBuf;
use std::process::Command;

/// A single actionable readiness problem the panel renders as a card.
#[derive(Serialize, Clone)]
pub struct Problem {
    /// stable id: "install" | "input_group" | "relogin" | "uinput" | "ydotoold" | "daemon"
    pub code: String,
    /// "error" (blocks dictation) | "warn" (degraded) | "relogin" (fix applied, needs logout)
    pub severity: String,
    pub title: String,
    pub detail: String,
    /// action id the frontend maps to a button:
    ///   "input_group" | "ydotoold" | "install:<pkg,pkg>" | "relogin" | "copy:<cmd>" | ""
    pub fix: String,
}

/// Full readiness snapshot: raw probe facts + the daemon's last self-report +
/// the derived list of problems the panel shows.
#[derive(Serialize)]
pub struct Readiness {
    pub platform: String, // "linux" | "windows"
    pub session: String,  // wayland | x11 | windows | unknown
    pub de: String,

    // static probes
    pub input_group_configured: bool, // user listed in `getent group input`
    pub input_group_active: bool,     // group live in THIS session (needs relogin otherwise)
    pub uinput_writable: bool,
    pub has_ydotool: bool,
    pub has_wl_clipboard: bool,
    pub ydotoold_running: bool,
    pub package_manager: String, // "pacman" | "apt" | "dnf" | "zypper" | ""

    // daemon's last startup outcome (from readiness.json), if any
    pub daemon_ok: Option<bool>,
    pub daemon_error_code: Option<String>,
    pub daemon_error_message: Option<String>,
    pub daemon_output_backend: Option<String>,

    pub problems: Vec<Problem>,
}

// ---------------------------------------------------------------------------
// probes
// ---------------------------------------------------------------------------

fn session() -> String {
    #[cfg(windows)]
    {
        "windows".into()
    }
    #[cfg(not(windows))]
    {
        if std::env::var("WAYLAND_DISPLAY").map(|s| !s.is_empty()).unwrap_or(false) {
            "wayland".into()
        } else if std::env::var("XDG_SESSION_TYPE").map(|s| s == "wayland").unwrap_or(false) {
            "wayland".into()
        } else {
            "x11".into()
        }
    }
}

fn desktop() -> String {
    let de = std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default().to_lowercase();
    for (needle, label) in [
        ("kde", "kde"), ("plasma", "kde"), ("gnome", "gnome"),
        ("sway", "wlroots"), ("hyprland", "wlroots"), ("wlroots", "wlroots"),
        ("xfce", "xfce"),
    ] {
        if de.contains(needle) {
            return label.to_string();
        }
    }
    if de.is_empty() { "unknown".into() } else { de }
}

/// The login name of the user running the GUI (for `usermod`). Prefers $USER,
/// falls back to `id -un`.
fn current_user() -> String {
    if let Ok(u) = std::env::var("USER") {
        if !u.is_empty() {
            return u;
        }
    }
    Command::new("id")
        .arg("-un")
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Is a program on PATH? (avoids a `which` dependency)
fn has(program: &str) -> bool {
    let Ok(path) = std::env::var("PATH") else { return false };
    std::env::split_paths(&path).any(|dir| {
        let p = dir.join(program);
        p.is_file() && is_executable(&p)
    })
}

#[cfg(not(windows))]
fn is_executable(p: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p).map(|m| m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}
#[cfg(windows)]
fn is_executable(_p: &std::path::Path) -> bool {
    true
}

/// True if `input` (as listed in /etc/group) contains `user` — i.e. membership
/// is configured, even if not yet live in the running session.
fn input_group_configured(user: &str) -> bool {
    let out = Command::new("getent").args(["group", "input"]).output();
    let Ok(out) = out else { return false };
    let line = String::from_utf8_lossy(&out.stdout);
    // format: input:x:992:user1,user2
    line.rsplit(':')
        .next()
        .map(|members| members.split(',').any(|m| m.trim() == user))
        .unwrap_or(false)
}

/// True if `input` is one of THIS process's live supplementary groups — the bit
/// that only becomes true after a logout/login following a group add.
fn input_group_active() -> bool {
    Command::new("id")
        .arg("-nG")
        .output()
        .ok()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .split_whitespace()
                .any(|g| g == "input")
        })
        .unwrap_or(false)
}

/// Can we open /dev/uinput for writing? Opening (without any ioctl) is
/// side-effect-free — a virtual device is only created on UI_DEV_CREATE — so
/// this is a safe capability probe.
fn uinput_writable() -> bool {
    std::fs::OpenOptions::new().write(true).open("/dev/uinput").is_ok()
}

/// ydotoold is up if any of its candidate socket paths exists (mirrors the
/// daemon's `_ydotool_socket_candidates`).
fn ydotoold_running() -> bool {
    let mut candidates: Vec<PathBuf> = vec![PathBuf::from("/tmp/.ydotool_socket")];
    if let Ok(x) = std::env::var("XDG_RUNTIME_DIR") {
        if !x.is_empty() {
            candidates.push(PathBuf::from(x).join(".ydotool_socket"));
        }
    }
    if let Ok(uid) = std::env::var("UID") {
        candidates.push(PathBuf::from(format!("/run/user/{uid}/.ydotool_socket")));
    }
    // $UID is often unset for GUI processes; derive it from `id -u` too.
    if let Ok(o) = Command::new("id").arg("-u").output() {
        let uid = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if !uid.is_empty() {
            candidates.push(PathBuf::from(format!("/run/user/{uid}/.ydotool_socket")));
        }
    }
    candidates.iter().any(|p| p.exists())
}

fn detect_package_manager() -> String {
    for pm in ["pacman", "apt", "dnf", "zypper"] {
        if has(pm) {
            return pm.to_string();
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// daemon self-report
// ---------------------------------------------------------------------------

struct DaemonReport {
    ok: Option<bool>,
    error_code: Option<String>,
    error_message: Option<String>,
    output_backend: Option<String>,
}

fn read_daemon_report() -> DaemonReport {
    let text = std::fs::read_to_string(crate::paths::readiness_json()).unwrap_or_default();
    let v: serde_json::Value = serde_json::from_str(&text).unwrap_or(serde_json::Value::Null);
    let s = |val: &serde_json::Value| val.as_str().map(|x| x.to_string());
    DaemonReport {
        ok: v.get("ok").and_then(|x| x.as_bool()),
        error_code: v.get("error").and_then(|e| e.get("code")).and_then(s),
        error_message: v.get("error").and_then(|e| e.get("message")).and_then(s),
        output_backend: v.get("output_backend").and_then(s),
    }
}

// ---------------------------------------------------------------------------
// problem derivation
// ---------------------------------------------------------------------------

/// Per-distro install command for a set of packages, or empty if unknown PM.
fn install_command(pm: &str, pkgs: &[&str]) -> String {
    let list = pkgs.join(" ");
    match pm {
        "pacman" => format!("sudo pacman -S --needed {list}"),
        "apt" => format!("sudo apt install {list}"),
        "dnf" => format!("sudo dnf install {list}"),
        "zypper" => format!("sudo zypper install {list}"),
        _ => String::new(),
    }
}

/// Map our internal package ids to the concrete package name on this distro
/// (only where they differ). ydotool / wl-clipboard are consistent across the
/// big distros; webkit differs but is handled by the pre-launch doctor, not here.
fn distro_pkg(pm: &str, id: &str) -> String {
    match (pm, id) {
        ("apt", "wl-clipboard") => "wl-clipboard".into(),
        _ => id.to_string(),
    }
}

fn derive_problems(r: &Readiness) -> Vec<Problem> {
    let mut problems = Vec::new();
    if r.platform != "linux" || r.session == "x11" {
        // On Windows nothing here applies. On X11 the pynput/xdotool path needs
        // no groups or ydotool; leave those to the existing daemon errors.
        return problems;
    }

    // 1. Missing runtime packages (ydotool / wl-clipboard) — the KDE-Wayland
    //    typing path needs both. Highest priority: without these there's no
    //    output backend at all.
    let mut missing: Vec<&str> = Vec::new();
    if !r.has_ydotool {
        missing.push("ydotool");
    }
    if !r.has_wl_clipboard {
        missing.push("wl-clipboard");
    }
    if !missing.is_empty() {
        let pm = &r.package_manager;
        let mapped: Vec<String> = missing.iter().map(|p| distro_pkg(pm, p)).collect();
        let mapped_refs: Vec<&str> = mapped.iter().map(|s| s.as_str()).collect();
        // pacman → one-click pkexec install; other PMs → copy-paste block.
        let fix = if pm == "pacman" {
            format!("install:{}", mapped.join(","))
        } else if !pm.is_empty() {
            format!("copy:{}", install_command(pm, &mapped_refs))
        } else {
            String::new()
        };
        problems.push(Problem {
            code: "install".into(),
            severity: "error".into(),
            title: format!("Missing {}", missing.join(" + ")),
            detail: format!(
                "Quobi types via {} on Wayland. Install {} to enable output.",
                missing.join(" and "),
                mapped.join(" + "),
            ),
            fix,
        });
    }

    // 2. input group. The master fix. Configured-but-not-active means the add
    //    succeeded and only a relogin is left.
    let user = current_user();
    if !r.input_group_configured {
        problems.push(Problem {
            code: "input_group".into(),
            severity: "error".into(),
            title: "Not in the 'input' group".into(),
            detail: format!(
                "Quobi needs {} in the 'input' group to read the keyboard (hotkey) \
                 and type (uinput). This is the usual cause of a hotkey that does \
                 nothing on Wayland.",
                if user.is_empty() { "your user" } else { user.as_str() }
            ),
            fix: "input_group".into(),
        });
    } else if !r.input_group_active {
        problems.push(Problem {
            code: "relogin".into(),
            severity: "relogin".into(),
            title: "Log out and back in to finish".into(),
            detail: "You've been added to the 'input' group, but group membership \
                     only takes effect on a fresh login. Log out and back in, then \
                     Quobi's hotkey will work."
                .into(),
            fix: "relogin".into(),
        });
    } else if !r.uinput_writable {
        // In the group and logged in fresh, but uinput still isn't writable —
        // an older distro without the systemd default rule. Our helper installs
        // a udev rule.
        problems.push(Problem {
            code: "uinput".into(),
            severity: "error".into(),
            title: "/dev/uinput not writable".into(),
            detail: "You're in the 'input' group but /dev/uinput isn't group-writable. \
                     Quobi can install a udev rule to fix this."
                .into(),
            fix: "input_group".into(), // same helper installs the rule
        });
    }

    // 3. ydotoold not running (only relevant once ydotool is installed). Warn,
    //    not error — it's a one-click user-service start, no root.
    if r.has_ydotool && !r.ydotoold_running {
        problems.push(Problem {
            code: "ydotoold".into(),
            severity: "warn".into(),
            title: "ydotool daemon not running".into(),
            detail: "The ydotoold background service isn't running, so Quobi can't \
                     type. Start it now (no password needed)."
                .into(),
            fix: "ydotoold".into(),
        });
    }

    // 4. Daemon reported a fatal error the probes didn't already explain — show
    //    it verbatim so nothing is silently swallowed.
    if r.daemon_ok == Some(false) {
        let already = |c: &str| problems.iter().any(|p| p.code == c);
        let code = r.daemon_error_code.as_deref().unwrap_or("unknown");
        let covered = matches!(
            code,
            "no_input_group" | "uinput_denied"
        ) && (already("input_group") || already("relogin") || already("uinput"))
            || (code == "no_output_backend" && already("install"))
            || (code == "ydotool_socket_missing" && already("ydotoold"))
            || code == "wtype_refused"; // auto-handled by the daemon fallback
        if !covered {
            problems.push(Problem {
                code: "daemon".into(),
                severity: "error".into(),
                title: "Dictation engine couldn't start".into(),
                detail: r
                    .daemon_error_message
                    .clone()
                    .unwrap_or_else(|| "See the daemon log for details.".into()),
                fix: String::new(),
            });
        }
    }

    problems
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_readiness() -> Readiness {
    let platform = if cfg!(windows) { "windows" } else { "linux" }.to_string();
    let user = current_user();
    let report = read_daemon_report();

    let mut r = Readiness {
        platform,
        session: session(),
        de: desktop(),
        input_group_configured: cfg!(windows) || input_group_configured(&user),
        input_group_active: cfg!(windows) || input_group_active(),
        uinput_writable: cfg!(windows) || uinput_writable(),
        has_ydotool: cfg!(windows) || has("ydotool"),
        has_wl_clipboard: cfg!(windows) || (has("wl-copy") && has("wl-paste")),
        ydotoold_running: cfg!(windows) || ydotoold_running(),
        package_manager: if cfg!(windows) { String::new() } else { detect_package_manager() },
        daemon_ok: report.ok,
        daemon_error_code: report.error_code,
        daemon_error_message: report.error_message,
        daemon_output_backend: report.output_backend,
        problems: Vec::new(),
    };
    r.problems = derive_problems(&r);
    r
}

/// Add the current user to the `input` group (and install the uinput udev rule
/// if needed) via a single pkexec prompt. Membership needs a relogin to apply —
/// the panel surfaces that afterward. No-op on Windows.
#[tauri::command]
pub fn fix_input_group(app: tauri::AppHandle) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = app;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let user = current_user();
        if user.is_empty() {
            return Err("could not determine current user".into());
        }
        let helper = resolve_setup_helper(&app).ok_or("setup helper not found")?;
        let status = Command::new("pkexec")
            .arg(&helper)
            .arg("--add-input-group")
            .arg(&user)
            .status()
            .map_err(|e| format!("could not launch pkexec: {e}"))?;
        if status.success() {
            Ok(())
        } else {
            // 126 = polkit auth dismissed/denied; surface a friendly message.
            match status.code() {
                Some(126) | Some(127) => Err("authorization was cancelled".into()),
                Some(c) => Err(format!("setup helper failed (exit {c})")),
                None => Err("setup helper was terminated".into()),
            }
        }
    }
}

/// Preferred socket path to create when spawning ydotoold ourselves: under
/// XDG_RUNTIME_DIR (a daemon candidate path), falling back to /tmp.
#[cfg(not(windows))]
fn ydotool_socket_target() -> PathBuf {
    if let Ok(x) = std::env::var("XDG_RUNTIME_DIR") {
        if !x.is_empty() {
            return PathBuf::from(x).join(".ydotool_socket");
        }
    }
    PathBuf::from("/tmp/.ydotool_socket")
}

/// Start ydotoold so dictation can type. No root needed. No-op on Windows.
///
/// The obvious `systemctl --user enable --now ydotool` is tried first (it also
/// gives reboot persistence), but on real boxes the *user service* context can
/// lack `/dev/uinput` access and fail even when interactive processes in the
/// session have it — so we fall back to spawning ydotoold directly with an
/// explicit socket path, which runs in the GUI's (input-group) context. This is
/// the same mechanism `reset_keyboard` relies on.
#[tauri::command]
pub fn start_ydotoold() -> Result<(), String> {
    #[cfg(windows)]
    {
        Ok(())
    }
    #[cfg(not(windows))]
    {
        use std::process::Stdio;
        use std::time::Duration;

        if !has("ydotool") {
            return Err("ydotool isn't installed — install it first".into());
        }
        // 1. Best-effort systemd unit: works (and persists across reboots) on
        //    well-configured systems; harmless noise where it can't open uinput.
        let _ = Command::new("systemctl")
            .args(["--user", "enable", "--now", "ydotool"])
            .status();
        if wait_for_socket(Duration::from_millis(1200)) {
            return Ok(());
        }
        // 2. Reliable fallback: spawn ydotoold ourselves, detached, with an
        //    explicit socket path the daemon will find.
        let sock = ydotool_socket_target();
        let _ = std::fs::remove_file(&sock); // stale socket from a dead ydotoold
        Command::new("ydotoold")
            .arg("-p")
            .arg(&sock)
            .args(["-P", "0600"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("could not launch ydotoold: {e}"))?;
        if wait_for_socket(Duration::from_secs(3)) {
            Ok(())
        } else {
            Err("ydotoold started but no socket appeared — check `journalctl --user -u ydotool`".into())
        }
    }
}

/// Poll for a ydotoold socket to appear, up to `budget`.
#[cfg(not(windows))]
fn wait_for_socket(budget: std::time::Duration) -> bool {
    use std::time::Instant;
    let start = Instant::now();
    while start.elapsed() < budget {
        if ydotoold_running() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(150));
    }
    ydotoold_running()
}

/// Install packages via the distro package manager (Arch/pacman one-click only;
/// other distros are guided with a copy-paste block, never auto-driven as root).
#[tauri::command]
pub fn install_packages(packages: Vec<String>) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = packages;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        if !has("pacman") {
            return Err("one-click install is only supported on Arch; run the shown command".into());
        }
        if packages.is_empty() {
            return Err("no packages to install".into());
        }
        // Guard the package names — they go to root pacman.
        for p in &packages {
            if !p.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '+')) {
                return Err(format!("refusing suspicious package name: {p}"));
            }
        }
        let mut args = vec![
            "pacman".to_string(),
            "-S".to_string(),
            "--needed".to_string(),
            "--noconfirm".to_string(),
        ];
        args.extend(packages);
        let status = Command::new("pkexec")
            .args(&args)
            .status()
            .map_err(|e| format!("could not launch pkexec: {e}"))?;
        if status.success() {
            Ok(())
        } else {
            match status.code() {
                Some(126) | Some(127) => Err("authorization was cancelled".into()),
                Some(c) => Err(format!("pacman failed (exit {c})")),
                None => Err("install was terminated".into()),
            }
        }
    }
}

/// Headless dump of the readiness snapshot as pretty JSON. Backs the
/// `quobi --readiness` support/debug command so the detection layer can be
/// inspected without launching the GUI.
pub fn readiness_report_json() -> String {
    serde_json::to_string_pretty(&get_readiness())
        .unwrap_or_else(|e| format!("{{\"error\":\"serialize failed: {e}\"}}"))
}

/// Resolve the setup helper: the stable copy first, else the in-bundle resource.
#[cfg(not(windows))]
fn resolve_setup_helper(app: &tauri::AppHandle) -> Option<PathBuf> {
    let stable = crate::paths::setup_helper();
    if stable.exists() {
        return Some(stable);
    }
    use tauri::Manager;
    let res = app.path().resource_dir().ok()?;
    let bundled = res.join("quobi-setup");
    bundled.exists().then_some(bundled)
}
