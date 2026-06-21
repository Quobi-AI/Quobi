//! Daemon status + config facts for the dashboard's top panel.
use serde::Serialize;
use crate::paths;

#[derive(Serialize)]
pub struct Status {
    pub daemon_running: bool,
    pub hotkey: String,
    pub hotkey_mode: String,
    pub model: String,
    pub cleanup_enabled: bool,
    pub output_mode: String,
    pub session: String,
}

fn toml_get<'a>(t: &'a toml::Value, section: &str, key: &str) -> Option<&'a str> {
    t.get(section)?.get(key)?.as_str()
}

fn toml_get_bool(t: &toml::Value, section: &str, key: &str) -> Option<bool> {
    t.get(section)?.get(key)?.as_bool()
}

/// A friendly label for an on-disk cleanup GGUF, e.g.
/// ".../qwen35-4b-cleanup-Q4_K_M.gguf" -> "Quill 4B". Falls back to the bare
/// filename (sans .gguf) for anything unrecognised.
fn pretty_local_model(path: &str) -> String {
    let base = path.rsplit('/').next().unwrap_or(path);
    let lower = base.to_lowercase();
    for (marker, label) in [("-0.8b-", "Quill 0.8B"), ("-2b-", "Quill 2B"), ("-4b-", "Quill 4B")] {
        if lower.contains(marker) {
            return label.to_string();
        }
    }
    base.strip_suffix(".gguf").unwrap_or(base).to_string()
}

/// Resolve the on-device cleanup model the daemon will use: a friendly label
/// for the configured Quill GGUF (or "on-device" when none is selected yet).
fn resolve_model(t: &toml::Value) -> String {
    let path = toml_get(t, "cleanup", "local_model").unwrap_or("");
    if path.is_empty() {
        "on-device".to_string()
    } else {
        pretty_local_model(path)
    }
}

fn daemon_running() -> bool {
    crate::daemonctl::is_running()
}

fn session() -> String {
    #[cfg(windows)]
    {
        return "windows".into();
    }
    #[cfg(not(windows))]
    {
        if std::env::var("WAYLAND_DISPLAY").is_ok() {
            "wayland".into()
        } else if std::env::var("XDG_SESSION_TYPE").map(|s| s == "wayland").unwrap_or(false) {
            "wayland".into()
        } else {
            "x11".into()
        }
    }
}

#[tauri::command]
pub fn get_status() -> Status {
    let raw = std::fs::read_to_string(paths::config_toml()).unwrap_or_default();
    let t: toml::Value = toml::from_str(&raw).unwrap_or(toml::Value::Table(Default::default()));
    let model = resolve_model(&t);
    Status {
        daemon_running: daemon_running(),
        hotkey: toml_get(&t, "hotkey", "key").unwrap_or("grave").to_string(),
        hotkey_mode: toml_get(&t, "hotkey", "mode").unwrap_or("hold").to_string(),
        model,
        cleanup_enabled: toml_get_bool(&t, "cleanup", "enabled").unwrap_or(true),
        output_mode: toml_get(&t, "output", "mode").unwrap_or("paste").to_string(),
        session: session(),
    }
}

#[tauri::command]
pub fn start_daemon(app: tauri::AppHandle) -> Result<(), String> {
    // `app` is auto-injected by Tauri; the frontend still calls invoke("start_daemon").
    crate::daemonctl::spawn(&app)
}
