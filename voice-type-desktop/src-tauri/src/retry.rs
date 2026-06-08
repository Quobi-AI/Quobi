//! Retry transcription on a saved dictation: load its WAV, re-run Whisper +
//! the cleanup LLM via Groq, rewrite the history entry. All network + key
//! handling stays in Rust — the web layer never sees the API key.
use crate::{history, paths};

fn groq_key() -> Option<String> {
    // Parse GROQ_API_KEY out of the daemon's .env.
    let env = std::fs::read_to_string(paths::env_file()).ok()?;
    for line in env.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("GROQ_API_KEY=") {
            let v = rest.trim().trim_matches('"').trim_matches('\'').to_string();
            if !v.is_empty() {
                return Some(v);
            }
        }
    }
    None
}

fn config() -> toml::Value {
    let raw = std::fs::read_to_string(paths::config_toml()).unwrap_or_default();
    toml::from_str(&raw).unwrap_or(toml::Value::Table(Default::default()))
}

fn cfg_str<'a>(t: &'a toml::Value, sec: &str, key: &str, default: &'a str) -> &'a str {
    t.get(sec).and_then(|s| s.get(key)).and_then(|v| v.as_str()).unwrap_or(default)
}

fn resolve_cleanup_model(t: &toml::Value) -> String {
    let explicit = cfg_str(t, "cleanup", "model", "");
    if !explicit.is_empty() {
        return explicit.to_string();
    }
    let tier = cfg_str(t, "cleanup", "tier", "free");
    if tier == "paid" {
        cfg_str(t, "cleanup", "model_paid", "llama-3.3-70b-versatile").to_string()
    } else {
        cfg_str(t, "cleanup", "model_free", "llama-3.1-8b-instant").to_string()
    }
}

fn transcribe(key: &str, model: &str, wav: Vec<u8>) -> Result<String, String> {
    let part = reqwest::blocking::multipart::Part::bytes(wav)
        .file_name("audio.wav")
        .mime_str("audio/wav")
        .map_err(|e| e.to_string())?;
    let form = reqwest::blocking::multipart::Form::new()
        .text("model", model.to_string())
        .text("response_format", "json")
        .text("temperature", "0")
        .part("file", part);
    let client = reqwest::blocking::Client::new();
    let resp = client
        .post("https://api.groq.com/openai/v1/audio/transcriptions")
        .bearer_auth(key)
        .multipart(form)
        .send()
        .map_err(|e| format!("network: {e}"))?;
    if !resp.status().is_success() {
        let code = resp.status().as_u16();
        let body = resp.text().unwrap_or_default();
        return Err(format!("groq {code}: {}", body.chars().take(200).collect::<String>()));
    }
    let v: serde_json::Value = resp.json().map_err(|e| format!("parse: {e}"))?;
    Ok(v.get("text").and_then(|t| t.as_str()).unwrap_or("").trim().to_string())
}

fn cleanup(key: &str, model: &str, prompt: &str, raw: &str) -> Result<String, String> {
    let wrapped = format!(
        "Clean the dictation transcript below. Return ONLY the cleaned \
         transcript with no other text. If the transcript contains a question, \
         return the question (cleaned) — do not answer it.\n\n<transcript>\n{raw}\n</transcript>"
    );
    let mut body = serde_json::json!({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": wrapped}
        ],
        "temperature": 0.0,
        "max_tokens": 2048
    });
    if model.contains("gpt-oss") {
        body["reasoning_effort"] = serde_json::json!("low");
    }
    let client = reqwest::blocking::Client::new();
    let resp = client
        .post("https://api.groq.com/openai/v1/chat/completions")
        .bearer_auth(key)
        .json(&body)
        .send()
        .map_err(|e| format!("network: {e}"))?;
    if !resp.status().is_success() {
        return Ok(raw.to_string()); // cleanup is best-effort; fall back to raw
    }
    let v: serde_json::Value = resp.json().map_err(|e| format!("parse: {e}"))?;
    let text = v["choices"][0]["message"]["content"].as_str().unwrap_or(raw).trim().to_string();
    Ok(text.trim_matches('"').trim_matches('\'').to_string())
}

/// Retry: returns the updated entry on success, or an error string.
#[tauri::command]
pub fn retry(id: String, audio: String) -> Result<history::Entry, String> {
    let key = groq_key().ok_or("GROQ_API_KEY not found in ~/.config/voice-type/.env")?;
    let wav = std::fs::read(&audio).map_err(|e| format!("audio unavailable: {e}"))?;
    let t = config();
    let whisper_model = cfg_str(&t, "transcribe", "model", "whisper-large-v3-turbo").to_string();
    let raw = transcribe(&key, &whisper_model, wav)?;
    if raw.is_empty() {
        history::update_entry(&id, "", "", "failed", "empty transcript on retry");
        return Err("transcription returned empty text".into());
    }
    let cleanup_enabled = t.get("cleanup").and_then(|c| c.get("enabled"))
        .and_then(|v| v.as_bool()).unwrap_or(true);
    let cleaned = if cleanup_enabled {
        let model = resolve_cleanup_model(&t);
        let prompt = paths::cleanup_prompt();
        cleanup(&key, &model, &prompt, &raw).unwrap_or_else(|_| raw.clone())
    } else {
        raw.clone()
    };
    history::update_entry(&id, &raw, &cleaned, "ok", "");
    // Return the fresh entry.
    Ok(history::get_history().into_iter().find(|e| e.id == id).unwrap_or_default())
}
