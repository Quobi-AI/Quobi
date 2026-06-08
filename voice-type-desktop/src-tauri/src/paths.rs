//! Resolve the same XDG locations the Python daemon uses, so the GUI reads
//! the exact files the daemon writes.
use std::path::PathBuf;

pub fn config_dir() -> PathBuf {
    // $XDG_CONFIG_HOME/voice-type or ~/.config/voice-type
    if let Ok(x) = std::env::var("XDG_CONFIG_HOME") {
        if !x.is_empty() {
            return PathBuf::from(x).join("voice-type");
        }
    }
    dirs::home_dir().unwrap_or_default().join(".config").join("voice-type")
}

pub fn state_dir() -> PathBuf {
    if let Ok(x) = std::env::var("XDG_STATE_HOME") {
        if !x.is_empty() {
            return PathBuf::from(x).join("voice-type");
        }
    }
    dirs::home_dir().unwrap_or_default().join(".local").join("state").join("voice-type")
}

pub fn config_toml() -> PathBuf {
    config_dir().join("config.toml")
}

pub fn data_dir() -> PathBuf {
    // $XDG_DATA_HOME/voice-type or ~/.local/share/voice-type
    if let Ok(x) = std::env::var("XDG_DATA_HOME") {
        if !x.is_empty() {
            return PathBuf::from(x).join("voice-type");
        }
    }
    dirs::home_dir().unwrap_or_default().join(".local").join("share").join("voice-type")
}

/// Where cleanup GGUFs live. Drop a .gguf here and it becomes selectable.
pub fn models_dir() -> PathBuf {
    data_dir().join("models")
}

pub fn env_file() -> PathBuf {
    config_dir().join(".env")
}

pub fn history_jsonl() -> PathBuf {
    state_dir().join("history.jsonl")
}

/// The shared cleanup prompt. Tries the repo's shared/ dir (dev), then a
/// copy installed alongside the binary. Falls back to a built-in minimal
/// prompt so retry still works if neither is found.
pub fn cleanup_prompt() -> String {
    // 1. bundled next to the executable: <exe_dir>/shared/cleanup-prompt.txt
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join("shared").join("cleanup-prompt.txt");
            if let Ok(s) = std::fs::read_to_string(&p) {
                return s;
            }
        }
    }
    // 2. dev: walk up to find WhisperFlowClone/shared/cleanup-prompt.txt
    if let Ok(cwd) = std::env::current_dir() {
        let mut dir = Some(cwd.as_path());
        while let Some(d) = dir {
            let p = d.join("shared").join("cleanup-prompt.txt");
            if let Ok(s) = std::fs::read_to_string(&p) {
                return s;
            }
            dir = d.parent();
        }
    }
    // 3. fallback
    "Clean the dictation transcript: remove fillers (um, uh), fix punctuation \
     and capitalization, preserve the speaker's exact words. Output only the \
     cleaned text.".to_string()
}
