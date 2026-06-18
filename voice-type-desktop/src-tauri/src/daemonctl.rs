//! Cross-platform daemon process control.
//!
//! Linux/macOS behavior is preserved EXACTLY (`#[cfg(not(windows))]` — pgrep /
//! pkill / ~/.local/bin, unchanged). Windows (`#[cfg(windows)]`) launches the
//! daemon bundled in the Tauri resource dir, seeds an offline config on first
//! launch, and uses tasklist/taskkill.
use std::process::Command;

/// A `Command` that never flashes a console window on Windows
/// (CREATE_NO_WINDOW). No-op on Linux/macOS, so callers stay cross-platform.
/// Use this for EVERY Windows-reachable spawn — a status poll fires every few
/// seconds, so an unsuppressed console program spams a flashing window.
pub(crate) fn hidden_command<S: AsRef<std::ffi::OsStr>>(program: S) -> Command {
    #[allow(unused_mut)]
    let mut c = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    c
}

/// Resolve the daemon executable for a one-shot subcommand (e.g. a model
/// download) — a dev install on `~/.local/bin`, else the copy bundled in the
/// Tauri resource dir. NOT for the systemd-managed daemon launch (that's
/// `spawn`). `None` if neither exists.
pub fn daemon_binary(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    #[cfg(windows)]
    let name = "voice-type.exe";
    #[cfg(not(windows))]
    let name = "voice-type";

    let local = dirs::home_dir()
        .unwrap_or_default()
        .join(".local")
        .join("bin")
        .join(name);
    if local.exists() {
        return Some(local);
    }
    use tauri::Manager;
    let res = app.path().resource_dir().ok()?;
    let bundled = res.join("daemon").join(name);
    bundled.exists().then_some(bundled)
}

/// Is the dictation daemon currently running?
pub fn is_running() -> bool {
    #[cfg(windows)]
    {
        // hidden_command: this runs on the GUI's ~4s status poll — a bare
        // Command::new would flash a console window every few seconds.
        hidden_command("tasklist")
            .args(["/FI", "IMAGENAME eq voice-type.exe", "/NH"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_lowercase().contains("voice-type.exe"))
            .unwrap_or(false)
    }
    #[cfg(not(windows))]
    {
        Command::new("pgrep")
            .args(["-x", "voice-type"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }
}

/// The systemd user unit (generated from the autostart .desktop) — the single
/// source of truth for the daemon under a systemd login session. Derived from
/// the autostart file `quobi-daemon.desktop` (each '-' is systemd-escaped \x2d).
#[cfg(not(windows))]
const DAEMON_UNIT: &str = "app-quobi\\x2ddaemon@autostart.service";

/// Stop the running daemon (best-effort).
pub fn kill() {
    #[cfg(windows)]
    {
        // Kill the daemon AND its cleanup sidecar. On Windows a child is not
        // reaped with its parent, so killing only voice-type.exe orphans
        // llama-server (it keeps holding its port). /T kills the whole tree.
        // (STT/Parakeet runs in-process in the daemon, so there's no STT sidecar.)
        for img in ["voice-type.exe", "llama-server.exe"] {
            let _ = hidden_command("taskkill").args(["/F", "/T", "/IM", img]).status();
        }
    }
    #[cfg(not(windows))]
    {
        // Prefer the systemd service so state stays consistent; fall back to pkill.
        let ok = Command::new("systemctl")
            .args(["--user", "stop", DAEMON_UNIT])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !ok {
            let _ = Command::new("pkill").args(["-x", "voice-type"]).status();
        }
    }
}

/// Emergency unstick. A stuck key comes from the daemon's evdev GRAB, so just
/// sending key-ups isn't enough — the grab itself has to be released. So we:
///   1. stop the daemon (release the exclusive keyboard grab),
///   2. send key-up for every keycode via ydotool (clear the compositor),
///   3. re-grab by respawning the daemon if it had been running (keys are now
///      released, so the fresh grab is clean).
/// No-op on Windows (no evdev grab there).
#[tauri::command]
pub fn reset_keyboard(app: tauri::AppHandle) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = app;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        use std::time::Duration;
        let was_running = is_running();
        kill(); // release the exclusive evdev grab
        std::thread::sleep(Duration::from_millis(400));

        let runtime =
            std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/run/user/1000".to_string());
        let sock = format!("{runtime}/.ydotool_socket");
        if !std::path::Path::new(&sock).exists() {
            let _ = Command::new("ydotoold").spawn();
            std::thread::sleep(Duration::from_millis(1500));
        }
        let mut args: Vec<String> = vec!["key".to_string()];
        for code in 1..=127 {
            args.push(format!("{code}:0")); // key-up (releasing an un-pressed key is a no-op)
        }
        let _ = Command::new("ydotool")
            .env("YDOTOOL_SOCKET", &sock)
            .args(&args)
            .status();

        if was_running {
            std::thread::sleep(Duration::from_millis(300));
            let _ = spawn(&app); // re-grab cleanly now that keys are released
        }
        Ok(())
    }
}

/// Start the daemon. Prefer the systemd user service (it gets the full graphical
/// session environment and stays the single source of truth); fall back to a raw
/// detached launch if systemd/the unit isn't available (e.g. a portable install).
/// `app` is only used on Windows.
#[cfg(not(windows))]
pub fn spawn(app: &tauri::AppHandle) -> Result<(), String> {
    // 1. Dev box: the systemd user service is the source of truth.
    let ok = Command::new("systemctl")
        .args(["--user", "start", DAEMON_UNIT])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if ok {
        return Ok(());
    }
    // 2. Dev / PATH install: a raw binary on ~/.local/bin.
    let local = dirs::home_dir()
        .unwrap_or_default()
        .join(".local")
        .join("bin")
        .join("voice-type");
    if local.exists() {
        return Command::new(local)
            .arg("--daemon")
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("could not start daemon: {e}"));
    }
    // 3. Shipped AppImage: launch the daemon bundled in the read-only resource
    //    dir, seeding an offline config (pointing at the bundled Vulkan
    //    llama-server) on first run. Mirrors the Windows path.
    use tauri::Manager;
    let res = app.path().resource_dir().map_err(|e| format!("resource dir: {e}"))?;
    seed_offline_config(&res);
    let daemon = res.join("daemon").join("voice-type");
    Command::new(&daemon)
        .arg("--daemon")
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start daemon at {}: {e}", daemon.display()))
}

/// First-launch (shipped AppImage): write an OFFLINE config pointing the daemon
/// at the bundled Vulkan llama-server. The cleanup model is NOT bundled — it
/// downloads to the writable user models dir on first run — so `local_model`
/// points there, NOT into the read-only resource dir. Never clobbers an
/// existing user config.
#[cfg(not(windows))]
fn seed_offline_config(res: &std::path::Path) {
    let cfg = crate::paths::config_toml();
    if cfg.exists() {
        return;
    }
    if let Some(parent) = cfg.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let bin = res.join("llama").join("llama-server").to_string_lossy().to_string();
    let model = crate::paths::models_dir()
        .join("quill-2b-Q4_K_M.gguf")
        .to_string_lossy()
        .to_string();
    // STT runs NVIDIA Parakeet (multilingual parakeet-tdt-0.6b-v3) in-process via
    // sherpa-onnx on the CPU: 20x+ faster than real-time even single-threaded, so
    // speech never needs the GPU and the GPU stays free for cleanup. The ONNX
    // bundle is NOT bundled (too big); it downloads on first run, so parakeet_dir
    // points at the writable models dir.
    let pdir = crate::paths::models_dir()
        .join("parakeet")
        .to_string_lossy()
        .to_string();
    let content = format!(
        "[transcribe]\nengine = \"local\"\nparakeet_dir = \"{pdir}\"\n\n\
         [cleanup]\nenabled = true\nengine = \"local\"\n\
         local_bin = \"{bin}\"\nlocal_model = \"{model}\"\nlocal_accel = \"auto\"\n\n\
         [hotkey]\nkey = \"f9\"\nbackend = \"auto\"\nmode = \"hold\"\n\n\
         [output]\nmode = \"paste\"\nbackend = \"auto\"\n"
    );
    let _ = std::fs::write(&cfg, content);
}

#[cfg(windows)]
pub fn spawn(app: &tauri::AppHandle) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    use tauri::Manager;
    // Don't start a second daemon if one is already up (the Startup-folder
    // shortcut also launches it on login). A double-start gives two daemons and
    // two llama-servers fighting over the same port.
    if is_running() {
        return Ok(());
    }
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let res = app.path().resource_dir().map_err(|e| format!("resource dir: {e}"))?;
    seed_offline_config(&res);
    let daemon = res.join("daemon").join("voice-type.exe");
    Command::new(&daemon)
        .arg("--daemon")
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start daemon at {}: {e}", daemon.display()))
}

/// First-launch: write an OFFLINE config pointing the daemon at the bundled
/// llama-server + cleanup model. Never clobbers an existing user config.
#[cfg(windows)]
fn seed_offline_config(res: &std::path::Path) {
    let cfg = crate::paths::config_toml();
    if cfg.exists() {
        return;
    }
    if let Some(parent) = cfg.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let bin = res.join("llama").join("llama-server.exe").to_string_lossy().replace('\\', "/");
    let model = res
        .join("models")
        .join("quill-2b-Q4_K_M.gguf")
        .to_string_lossy()
        .replace('\\', "/");
    // STT runs NVIDIA Parakeet (multilingual parakeet-tdt-0.6b-v3) in-process via
    // sherpa-onnx on the CPU, same as Linux -- 20x+ faster than real-time even on
    // one core, so speech never needs the GPU and the GPU stays free for cleanup.
    // The ONNX bundle downloads on first run, so parakeet_dir points at the
    // writable models dir.
    let pdir = crate::paths::models_dir()
        .join("parakeet")
        .to_string_lossy()
        .replace('\\', "/");
    let content = format!(
        "[transcribe]\nengine = \"local\"\nparakeet_dir = \"{pdir}\"\n\n\
         [cleanup]\nenabled = true\nengine = \"local\"\n\
         local_bin = \"{bin}\"\nlocal_model = \"{model}\"\nlocal_accel = \"auto\"\n\n\
         [hotkey]\nkey = \"f9\"\nbackend = \"auto\"\nmode = \"hold\"\n\n\
         [output]\nmode = \"paste\"\nbackend = \"auto\"\n"
    );
    let _ = std::fs::write(&cfg, content);
}
