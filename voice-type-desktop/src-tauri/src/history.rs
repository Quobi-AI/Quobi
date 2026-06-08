//! Read and rewrite the daemon's history.jsonl (one JSON object per line).
use serde::{Deserialize, Serialize};
use crate::paths;

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Entry {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub ts: String,
    #[serde(default = "default_kind")]
    pub kind: String,
    #[serde(default = "default_status")]
    pub status: String,
    #[serde(default)]
    pub duration: f64,
    #[serde(default)]
    pub raw: String,
    #[serde(default)]
    pub cleaned: String,
    #[serde(default)]
    pub audio: String,
    #[serde(default)]
    pub error: String,
}

fn default_kind() -> String { "dictation".into() }
fn default_status() -> String { "ok".into() }

/// All entries, newest-first (the file is oldest-first).
#[tauri::command]
pub fn get_history() -> Vec<Entry> {
    let raw = match std::fs::read_to_string(paths::history_jsonl()) {
        Ok(s) => s,
        Err(_) => return vec![],
    };
    let mut out: Vec<Entry> = raw
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| serde_json::from_str::<Entry>(l).ok())
        .collect();
    out.reverse();
    out
}

/// Rewrite the entry with `id`, replacing the listed fields. Returns true if
/// an entry matched. Used by the retry flow.
pub fn update_entry(id: &str, raw: &str, cleaned: &str, status: &str, error: &str) -> bool {
    let path = paths::history_jsonl();
    let content = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let mut updated = false;
    let mut lines: Vec<String> = Vec::new();
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<Entry>(line) {
            Ok(mut e) if e.id == id => {
                e.raw = raw.to_string();
                e.cleaned = cleaned.to_string();
                e.status = status.to_string();
                e.error = error.to_string();
                lines.push(serde_json::to_string(&e).unwrap_or_else(|_| line.to_string()));
                updated = true;
            }
            _ => lines.push(line.to_string()),
        }
    }
    if !updated {
        return false;
    }
    let tmp = path.with_extension("jsonl.tmp");
    if std::fs::write(&tmp, lines.join("\n") + "\n").is_err() {
        return false;
    }
    std::fs::rename(&tmp, &path).is_ok()
}
