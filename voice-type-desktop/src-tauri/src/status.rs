//! Daemon status + config facts for the dashboard's top panel.
use serde::Serialize;
use crate::paths;

#[derive(Serialize)]
pub struct Status {
    pub daemon_running: bool,
    pub hotkey: String,
    pub hotkey_mode: String,
    pub model: String,
    pub tier: String,
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

/// Resolve the cleanup model the SAME way the daemon does. When the engine is
/// local (the privacy-only default), report the on-device Quill model — NOT the
/// dormant cloud `model_paid`/`model_free` entries, which the daemon ignores.
fn resolve_model(t: &toml::Value) -> (String, String) {
    let engine = toml_get(t, "cleanup", "engine").unwrap_or("cloud");
    if engine == "local" {
        let path = toml_get(t, "cleanup", "local_model").unwrap_or("");
        let label = if path.is_empty() { "on-device".to_string() } else { pretty_local_model(path) };
        return (label, "on-device".to_string());
    }
    let tier = toml_get(t, "cleanup", "tier").unwrap_or("free").to_string();
    let explicit = toml_get(t, "cleanup", "model").unwrap_or("");
    if !explicit.is_empty() {
        return (explicit.to_string(), tier);
    }
    let model = if tier == "paid" {
        toml_get(t, "cleanup", "model_paid").unwrap_or("llama-3.3-70b-versatile")
    } else {
        toml_get(t, "cleanup", "model_free").unwrap_or("llama-3.1-8b-instant")
    };
    (model.to_string(), tier)
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
    let (model, tier) = resolve_model(&t);
    Status {
        daemon_running: daemon_running(),
        hotkey: toml_get(&t, "hotkey", "key").unwrap_or("grave").to_string(),
        hotkey_mode: toml_get(&t, "hotkey", "mode").unwrap_or("hold").to_string(),
        model,
        tier,
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
