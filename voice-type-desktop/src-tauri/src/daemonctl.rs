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

// ---- First-run install: stabilize binaries + autostart --------------------

/// Copy `src` to `dst` if dst is missing or a different size (so a newer app
/// bundle refreshes it). std::fs::copy preserves the source's +x bit on Unix.
fn copy_file_if_changed(src: &std::path::Path, dst: &std::path::Path) {
    if !src.exists() {
        return;
    }
    let differs = match (std::fs::metadata(src), std::fs::metadata(dst)) {
        (Ok(s), Ok(d)) => s.len() != d.len(),
        (Ok(_), Err(_)) => true, // dst missing
        _ => false,
    };
    if !differs {
        return;
    }
    if let Some(p) = dst.parent() {
        let _ = std::fs::create_dir_all(p);
    }
    let _ = std::fs::copy(src, dst);
}

/// Copy every file from the bundled llama dir into the stable dir, once (the
/// server binary's presence is the "already done" sentinel).
fn copy_llama_if_missing(src: &std::path::Path, dst: &std::path::Path) {
    #[cfg(windows)]
    let server = "llama-server.exe";
    #[cfg(not(windows))]
    let server = "llama-server";
    if dst.join(server).exists() || !src.exists() {
        return;
    }
    let _ = std::fs::create_dir_all(dst);
    if let Ok(rd) = std::fs::read_dir(src) {
        for e in rd.flatten() {
            let p = e.path();
            if p.is_file() {
                let _ = std::fs::copy(&p, dst.join(e.file_name()));
            }
        }
    }
}

/// Where the "start on login" entry lives: an XDG autostart .desktop on Linux
/// (systemd turns it into `app-quobi\x2ddaemon@autostart.service`), a hidden-
/// launch .vbs in the Startup folder on Windows.
#[cfg(not(windows))]
fn autostart_entry() -> std::path::PathBuf {
    crate::paths::config_dir()
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_default()
        .join("autostart")
        .join("quobi-daemon.desktop")
}
#[cfg(windows)]
fn autostart_entry() -> std::path::PathBuf {
    let appdata = std::env::var("APPDATA").unwrap_or_default();
    std::path::PathBuf::from(appdata)
        .join("Microsoft").join("Windows").join("Start Menu")
        .join("Programs").join("Startup").join("quobi-daemon.vbs")
}

/// Is "start the daemon on login" currently enabled?
pub fn get_autostart() -> bool {
    autostart_entry().exists()
}

/// Enable/disable "start on login" by writing or removing the autostart entry.
/// The entry points at the STABLE daemon copy, so it survives reboots (the
/// AppImage's own mount path does not).
pub fn set_autostart(enabled: bool) -> Result<(), String> {
    let f = autostart_entry();
    if !enabled {
        let _ = std::fs::remove_file(&f);
        #[cfg(not(windows))]
        {
            let _ = Command::new("systemctl").args(["--user", "daemon-reload"]).status();
            let _ = Command::new("systemctl").args(["--user", "stop", DAEMON_UNIT]).status();
        }
        return Ok(());
    }
    if let Some(p) = f.parent() {
        std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    let exec = crate::paths::daemon_bin();
    #[cfg(not(windows))]
    let content = format!(
        "[Desktop Entry]\nType=Application\nName=Quobi Dictation Service\n\
         Comment=Quobi on-device dictation engine (Parakeet STT + local cleanup)\n\
         Exec={} --daemon\nIcon=quobi\nTerminal=false\n\
         Categories=Utility;Accessibility;\nNoDisplay=true\nStartupNotify=false\n\
         X-GNOME-Autostart-enabled=true\nX-KDE-autostart-after=panel\n",
        exec.display()
    );
    #[cfg(windows)]
    let content = format!(
        "CreateObject(\"WScript.Shell\").Run \"\"\"{}\"\" --daemon\", 0, False\r\n",
        exec.display()
    );
    std::fs::write(&f, content).map_err(|e| e.to_string())?;
    #[cfg(not(windows))]
    {
        // Make the generated unit available this session, not just next login.
        let _ = Command::new("systemctl").args(["--user", "daemon-reload"]).status();
    }
    Ok(())
}

/// First-run install. The app bundle is read-only and, for an AppImage, mounted
/// at a per-launch `/tmp/.mount_Quobi-XXXX` path that changes every boot — so we
/// copy the daemon + llama-server out to STABLE user dirs, seed the config to
/// point at those, and enable autostart the first time. Idempotent: the copies
/// are skipped once present, and autostart is only force-enabled once (a later
/// user opt-out in Settings sticks).
pub fn ensure_install(app: &tauri::AppHandle) {
    use tauri::Manager;
    // Serialize: the setup-hook thread and a Start click can both call this on
    // first launch; without the lock their file copies could interleave.
    static INSTALL_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = INSTALL_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let Ok(res) = app.path().resource_dir() else { return };
    #[cfg(windows)]
    let (dname, lname) = ("voice-type.exe", "llama-server.exe");
    #[cfg(not(windows))]
    let (dname, lname) = ("voice-type", "llama-server");

    copy_file_if_changed(&res.join("daemon").join(dname), &crate::paths::daemon_bin());
    copy_llama_if_missing(&res.join("llama"), &crate::paths::bundled_llama_dir());
    seed_offline_config(&crate::paths::bundled_llama_dir().join(lname));

    let marker = crate::paths::data_dir().join(".autostart-initialized");
    if !marker.exists() {
        let _ = set_autostart(true);
        if let Some(p) = marker.parent() {
            let _ = std::fs::create_dir_all(p);
        }
        let _ = std::fs::write(&marker, "1");
    }
}

/// Start the daemon. Ensures the stable install first (copies + seed + first-run
/// autostart), then prefers the systemd user service, falling back to the stable
/// binary, then the in-bundle copy.
#[cfg(not(windows))]
pub fn spawn(app: &tauri::AppHandle) -> Result<(), String> {
    ensure_install(app);
    // 1. systemd user service (now present after ensure_install enabled autostart).
    let ok = Command::new("systemctl")
        .args(["--user", "start", DAEMON_UNIT])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if ok {
        return Ok(());
    }
    // 2. The stable daemon copy.
    let local = crate::paths::daemon_bin();
    if local.exists() {
        return Command::new(local)
            .arg("--daemon")
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("could not start daemon: {e}"));
    }
    // 3. Last resort: the daemon bundled in the read-only resource dir.
    use tauri::Manager;
    let res = app.path().resource_dir().map_err(|e| format!("resource dir: {e}"))?;
    let daemon = res.join("daemon").join("voice-type");
    Command::new(&daemon)
        .arg("--daemon")
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start daemon at {}: {e}", daemon.display()))
}

/// Write an OFFLINE config pointing cleanup at the (stable) llama-server and STT
/// at the downloaded Parakeet bundle. The models are NOT bundled — they download
/// to the writable models dir on first run. Never clobbers an existing config.
#[cfg(not(windows))]
fn seed_offline_config(llama_bin: &std::path::Path) {
    let cfg = crate::paths::config_toml();
    if cfg.exists() {
        return;
    }
    if let Some(parent) = cfg.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let bin = llama_bin.to_string_lossy().to_string();
    let model = crate::paths::models_dir()
        .join("quill-2b-Q4_K_M.gguf")
        .to_string_lossy()
        .to_string();
    // STT runs NVIDIA Parakeet in-process via sherpa-onnx on the CPU. Default to
    // the English model (v2); the GUI can switch to multilingual (v3) and repoint
    // parakeet_dir. Models download to the writable models dir on first run.
    let pdir = crate::paths::models_dir()
        .join("parakeet")
        .join("english")
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
    ensure_install(app);
    // Don't start a second daemon if one is already up (the Startup-folder entry
    // also launches it on login). A double-start gives two daemons and two
    // llama-servers fighting over the same port.
    if is_running() {
        return Ok(());
    }
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let daemon = crate::paths::daemon_bin();
    let daemon = if daemon.exists() {
        daemon
    } else {
        use tauri::Manager;
        let res = app.path().resource_dir().map_err(|e| format!("resource dir: {e}"))?;
        res.join("daemon").join("voice-type.exe")
    };
    Command::new(&daemon)
        .arg("--daemon")
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start daemon at {}: {e}", daemon.display()))
}

/// Write an OFFLINE config pointing cleanup at the (stable) llama-server and STT
/// at the downloaded Parakeet bundle. Models download on first run. Never
/// clobbers an existing config.
#[cfg(windows)]
fn seed_offline_config(llama_bin: &std::path::Path) {
    let cfg = crate::paths::config_toml();
    if cfg.exists() {
        return;
    }
    if let Some(parent) = cfg.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let bin = llama_bin.to_string_lossy().replace('\\', "/");
    let model = crate::paths::models_dir()
        .join("quill-2b-Q4_K_M.gguf")
        .to_string_lossy()
        .replace('\\', "/");
    let pdir = crate::paths::models_dir()
        .join("parakeet")
        .join("english")
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
