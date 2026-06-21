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
