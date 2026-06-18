//! Write-side commands: save the Groq API key, restart the daemon. All
//! file/key handling stays in Rust; the web layer just calls these.
use crate::paths;

/// Returns a masked form of the saved key for display, e.g. "gsk_…a1b2",
/// or empty string if none. Never returns the full key to the frontend.
#[tauri::command]
pub fn api_key_status() -> String {
    let env = std::fs::read_to_string(paths::env_file()).unwrap_or_default();
    for line in env.lines() {
        if let Some(rest) = line.trim().strip_prefix("GROQ_API_KEY=") {
            let v = rest.trim().trim_matches('"').trim_matches('\'');
            if v.len() >= 8 {
                return format!("{}…{}", &v[..4], &v[v.len() - 4..]);
            } else if !v.is_empty() {
                return "set".into();
            }
        }
    }
    String::new()
}

/// Write GROQ_API_KEY into ~/.config/voice-type/.env, preserving any other
/// lines. Creates the file (0600) if missing.
#[tauri::command]
pub fn save_api_key(key: String) -> Result<(), String> {
    let key = key.trim().to_string();
    if key.is_empty() {
        return Err("key is empty".into());
    }
    let path = paths::env_file();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let mut lines: Vec<String> = Vec::new();
    let mut replaced = false;
    for line in existing.lines() {
        if line.trim_start().starts_with("GROQ_API_KEY=") {
            lines.push(format!("GROQ_API_KEY={key}"));
            replaced = true;
        } else {
            lines.push(line.to_string());
        }
    }
    if !replaced {
        lines.push(format!("GROQ_API_KEY={key}"));
    }
    let body = lines.join("\n") + "\n";
    std::fs::write(&path, body).map_err(|e| e.to_string())?;
    // tighten perms to 0600 on unix
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// Update [hotkey].key and [hotkey].mode in config.toml, preserving the
/// file's comments and formatting (via toml_edit). The daemon must restart
/// to apply.
#[tauri::command]
pub fn save_hotkey(key: String, mode: String) -> Result<(), String> {
    let path = paths::config_toml();
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read config: {e}"))?;
    let mut doc = text
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| format!("parse config: {e}"))?;
    // ensure [hotkey] table exists
    if doc.get("hotkey").is_none() {
        doc["hotkey"] = toml_edit::table();
    }
    doc["hotkey"]["key"] = toml_edit::value(key);
    doc["hotkey"]["mode"] = toml_edit::value(mode);
    std::fs::write(&path, doc.to_string()).map_err(|e| format!("write config: {e}"))?;
    Ok(())
}

#[derive(serde::Serialize)]
pub struct Personalize {
    pub style: String,
    pub corrections: String,
}

#[tauri::command]
pub fn get_personalize() -> Personalize {
    let text = std::fs::read_to_string(paths::config_toml()).unwrap_or_default();
    let t: toml::Value = toml::from_str(&text).unwrap_or(toml::Value::Table(Default::default()));
    let get = |k: &str, default: &str| {
        t.get("personalize")
            .and_then(|s| s.get(k))
            .and_then(|v| v.as_str())
            .unwrap_or(default)
            .to_string()
    };
    Personalize {
        style: get("style", "tidy"),
        corrections: get("corrections", ""),
    }
}

#[tauri::command]
pub fn save_personalize(style: String, corrections: String) -> Result<(), String> {
    let path = paths::config_toml();
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read config: {e}"))?;
    let mut doc = text
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| format!("parse config: {e}"))?;
    if doc.get("personalize").is_none() {
        doc["personalize"] = toml_edit::table();
    }
    doc["personalize"]["style"] = toml_edit::value(style);
    doc["personalize"]["corrections"] = toml_edit::value(corrections);
    std::fs::write(&path, doc.to_string()).map_err(|e| format!("write config: {e}"))?;
    Ok(())
}

/// The local Whisper model id from [transcribe].local_model (default "base").
#[tauri::command]
pub fn get_transcribe_model() -> String {
    let text = std::fs::read_to_string(paths::config_toml()).unwrap_or_default();
    let t: toml::Value = toml::from_str(&text).unwrap_or(toml::Value::Table(Default::default()));
    t.get("transcribe")
        .and_then(|s| s.get("local_model"))
        .and_then(|v| v.as_str())
        .unwrap_or("base")
        .to_string()
}

/// Set [transcribe].local_model (and force engine=local). Daemon restart applies.
#[tauri::command]
pub fn save_transcribe_model(model: String) -> Result<(), String> {
    let path = paths::config_toml();
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read config: {e}"))?;
    let mut doc = text
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| format!("parse config: {e}"))?;
    if doc.get("transcribe").is_none() {
        doc["transcribe"] = toml_edit::table();
    }
    doc["transcribe"]["local_model"] = toml_edit::value(model);
    doc["transcribe"]["engine"] = toml_edit::value("local");
    std::fs::write(&path, doc.to_string()).map_err(|e| format!("write config: {e}"))?;
    Ok(())
}

/// The HuggingFace hub cache dir, honouring HF_HOME / HUGGINGFACE_HUB_CACHE,
/// matching how faster-whisper / huggingface_hub resolve it.
fn hf_hub_cache() -> std::path::PathBuf {
    if let Ok(c) = std::env::var("HUGGINGFACE_HUB_CACHE") {
        if !c.is_empty() {
            return std::path::PathBuf::from(c);
        }
    }
    if let Ok(h) = std::env::var("HF_HOME") {
        if !h.is_empty() {
            return std::path::PathBuf::from(h).join("hub");
        }
    }
    dirs::home_dir().unwrap_or_default()
        .join(".cache").join("huggingface").join("hub")
}

/// Resolve a Whisper model id to its HuggingFace repo the SAME way faster-whisper
/// does (its `_MODELS` table). Most live under Systran/faster-whisper-<name>, but
/// turbo and distil tiers are hosted elsewhere — so a naive "Systran/..." guess
/// would point at the wrong (or nonexistent) cache dir.
fn whisper_repo(name: &str) -> String {
    match name {
        "large-v3-turbo" | "turbo" => "mobiuslabsgmbh/faster-whisper-large-v3-turbo".into(),
        "large" => "Systran/faster-whisper-large-v3".into(),
        "distil-large-v2" => "Systran/faster-distil-whisper-large-v2".into(),
        "distil-medium.en" => "Systran/faster-distil-whisper-medium.en".into(),
        "distil-small.en" => "Systran/faster-distil-whisper-small.en".into(),
        "distil-large-v3" => "Systran/faster-distil-whisper-large-v3".into(),
        _ => format!("Systran/faster-whisper-{name}"),
    }
}

/// True if the faster-whisper model is already cached, so selecting it won't
/// trigger a download. The GUI uses this to decide whether to confirm + show
/// a progress bar.
#[tauri::command]
pub fn is_model_downloaded(name: String) -> bool {
    // HF cache dir: "models--<org>--<repo>" (the "/" in the repo id -> "--").
    let cache_dir = format!("models--{}", whisper_repo(&name).replace('/', "--"));
    let snaps = hf_hub_cache().join(cache_dir).join("snapshots");
    let Ok(entries) = std::fs::read_dir(&snaps) else { return false };
    // A finished download leaves at least one non-empty snapshot dir.
    for e in entries.flatten() {
        if e.path().is_dir() {
            if let Ok(mut inner) = std::fs::read_dir(e.path()) {
                if inner.next().is_some() {
                    return true;
                }
            }
        }
    }
    false
}

#[derive(serde::Serialize)]
pub struct DownloadProgress {
    pub state: String, // idle | downloading | done | error
    pub model: String,
    pub pct: i64,
    pub error: String,
}

/// Current model-download status, read from the JSON file the downloader
/// writes. The GUI polls this to animate a progress bar.
#[tauri::command]
pub fn download_progress() -> DownloadProgress {
    let path = paths::state_dir().join("download.json");
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let v: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
    DownloadProgress {
        state: v.get("state").and_then(|x| x.as_str()).unwrap_or("idle").to_string(),
        model: v.get("model").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        pct: v.get("pct").and_then(|x| x.as_i64()).unwrap_or(0),
        error: v.get("error").and_then(|x| x.as_str()).unwrap_or("").to_string(),
    }
}

/// Kick off a model download in the background (the daemon binary's
/// --download-model subcommand). Returns immediately; the GUI then polls
/// download_progress() until state is "done" or "error".
#[tauri::command]
pub fn start_model_download(name: String) -> Result<(), String> {
    let bin = dirs::home_dir().unwrap_or_default()
        .join(".local").join("bin").join("voice-type");
    crate::daemonctl::hidden_command(bin)
        .arg("--download-model")
        .arg(&name)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start download: {e}"))
}

/// Quill cleanup-model tiers hosted at quobi/quill. Maps the GUI tier id to the
/// on-disk / repo filename.
fn quill_filename(tier: &str) -> Option<&'static str> {
    match tier {
        "0.8b" => Some("quill-0.8b-Q4_K_M.gguf"),
        "2b" => Some("quill-2b-Q4_K_M.gguf"),
        "4b" => Some("quill-4b-Q4_K_M.gguf"),
        _ => None,
    }
}

/// True if a cleanup model of the given tier is already on disk anywhere under
/// the models dir — so the GUI knows whether to offer a download or just select
/// it. Matches by tier marker (e.g. "-4b-") rather than one exact filename, so a
/// model the user already has (quill-4b-…, qwen35-4b-cleanup-…, in any subfolder)
/// counts as installed and we don't re-offer a 2.6 GB download they don't need.
#[tauri::command]
pub fn is_cleanup_downloaded(tier: String) -> bool {
    if quill_filename(&tier).is_none() {
        return false;
    }
    // e.g. tier "4b" -> "-4b-", "0.8b" -> "-0.8b-". Both quill-<tier>- and
    // qwen35-<tier>-cleanup- contain this dash-delimited marker.
    let marker = format!("-{}-", tier.to_lowercase());
    fn any_match(d: &std::path::Path, marker: &str) -> bool {
        let Ok(rd) = std::fs::read_dir(d) else { return false };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                if any_match(&p, marker) {
                    return true;
                }
            } else if p.extension().map_or(false, |x| x.eq_ignore_ascii_case("gguf")) {
                let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("").to_lowercase();
                if name.contains(marker) {
                    return true;
                }
            }
        }
        false
    }
    any_match(&paths::models_dir(), &marker)
}

/// Kick off a cleanup-model (Quill GGUF) download in the background via the
/// daemon's --download-cleanup subcommand. Returns immediately; the GUI polls
/// download_progress() until state is "done" or "error".
#[tauri::command]
pub fn start_cleanup_download(app: tauri::AppHandle, tier: String) -> Result<(), String> {
    if quill_filename(&tier).is_none() {
        return Err(format!("unknown model tier: {tier}"));
    }
    let bin = crate::daemonctl::daemon_binary(&app)
        .ok_or_else(|| "daemon binary not found".to_string())?;
    crate::daemonctl::hidden_command(bin)
        .arg("--download-cleanup")
        .arg(&tier)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start download: {e}"))
}

/// True if ANY whisper.cpp STT model is on disk (small or large-v3-turbo) — so
/// the first-run banner doesn't nag a user who already has one. Excludes the
/// tiny Silero VAD model (ggml-silero-*.bin), which isn't a transcription model.
#[tauri::command]
pub fn is_whisper_downloaded() -> bool {
    let dir = paths::models_dir().join("whisper");
    let Ok(rd) = std::fs::read_dir(&dir) else { return false };
    rd.flatten().any(|e| {
        let n = e.file_name().to_string_lossy().to_lowercase();
        n.starts_with("ggml-") && n.ends_with(".bin") && !n.contains("silero")
    })
}

/// Kick off the whisper.cpp STT model download in the background via the
/// daemon's --download-whisper subcommand. The GUI polls download_progress()
/// until state is "done" or "error".
#[tauri::command]
pub fn start_whisper_download(app: tauri::AppHandle) -> Result<(), String> {
    let bin = crate::daemonctl::daemon_binary(&app)
        .ok_or_else(|| "daemon binary not found".to_string())?;
    crate::daemonctl::hidden_command(bin)
        .arg("--download-whisper")
        .arg("small")
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start download: {e}"))
}

/// True if the Parakeet ONNX bundle is fully on disk (all of encoder/decoder/
/// joiner + tokens.txt) — so the first-run banner doesn't nag a user who already
/// has it. This is the default local STT model.
#[tauri::command]
pub fn is_parakeet_downloaded() -> bool {
    let dir = paths::models_dir().join("parakeet");
    let has = |stem: &str| {
        dir.join(format!("{stem}.int8.onnx")).exists() || dir.join(format!("{stem}.onnx")).exists()
    };
    has("encoder") && has("decoder") && has("joiner") && dir.join("tokens.txt").exists()
}

/// Kick off the Parakeet STT model download in the background via the daemon's
/// --download-parakeet subcommand. The GUI polls download_progress() until state
/// is "done" or "error".
#[tauri::command]
pub fn start_parakeet_download(app: tauri::AppHandle) -> Result<(), String> {
    let bin = crate::daemonctl::daemon_binary(&app)
        .ok_or_else(|| "daemon binary not found".to_string())?;
    crate::daemonctl::hidden_command(bin)
        .arg("--download-parakeet")
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start download: {e}"))
}

#[derive(serde::Serialize)]
pub struct CleanupSettings {
    pub engine: String, // "cloud" | "local"
    pub local_model: String,
    pub local_accel: String, // "auto" | "gpu" | "cpu"
}

#[tauri::command]
pub fn get_cleanup_settings() -> CleanupSettings {
    let text = std::fs::read_to_string(paths::config_toml()).unwrap_or_default();
    let t: toml::Value = toml::from_str(&text).unwrap_or(toml::Value::Table(Default::default()));
    let get = |k: &str, default: &str| {
        t.get("cleanup")
            .and_then(|s| s.get(k))
            .and_then(|v| v.as_str())
            .unwrap_or(default)
            .to_string()
    };
    CleanupSettings {
        engine: get("engine", "cloud"),
        local_model: get("local_model", ""),
        local_accel: get("local_accel", "auto"),
    }
}

#[tauri::command]
pub fn save_cleanup_settings(
    engine: String,
    local_model: String,
    local_accel: String,
) -> Result<(), String> {
    let path = paths::config_toml();
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read config: {e}"))?;
    let mut doc = text
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| format!("parse config: {e}"))?;
    if doc.get("cleanup").is_none() {
        doc["cleanup"] = toml_edit::table();
    }
    doc["cleanup"]["engine"] = toml_edit::value(engine);
    doc["cleanup"]["local_model"] = toml_edit::value(local_model);
    doc["cleanup"]["local_accel"] = toml_edit::value(local_accel);
    std::fs::write(&path, doc.to_string()).map_err(|e| format!("write config: {e}"))?;
    Ok(())
}

/// Every .gguf under the models dir (recursive), newest first. Drop a model
/// into ~/.local/share/voice-type/models/ and it shows up in the picker.
#[tauri::command]
pub fn discover_local_models() -> Vec<String> {
    fn walk(d: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
        if let Ok(rd) = std::fs::read_dir(d) {
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, out);
                } else if p.extension().map_or(false, |x| x.eq_ignore_ascii_case("gguf")) {
                    out.push(p);
                }
            }
        }
    }
    let mut files = Vec::new();
    walk(&paths::models_dir(), &mut files);
    files.sort_by_key(|p| {
        std::cmp::Reverse(p.metadata().and_then(|m| m.modified()).ok())
    });
    files
        .into_iter()
        .filter_map(|p| p.to_str().map(String::from))
        .collect()
}

/// Restart the daemon so it picks up a new key / config: kill any running
/// instance, then spawn a fresh one.
#[tauri::command]
pub fn restart_daemon(app: tauri::AppHandle) -> Result<(), String> {
    crate::daemonctl::kill();
    std::thread::sleep(std::time::Duration::from_millis(400));
    crate::daemonctl::spawn(&app)
}
