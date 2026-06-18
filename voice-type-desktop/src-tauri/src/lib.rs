//! voice-type desktop GUI — Tauri core.
//!
//! All privileged work (file access, config, the Groq retry network calls,
//! the API key) lives here in Rust. The React/TS frontend can only call the
//! commands registered below — it never touches the filesystem or the key.
mod paths;
mod status;
mod history;
mod retry;
mod settings;
mod daemonctl;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![
            status::get_status,
            status::start_daemon,
            history::get_history,
            retry::retry,
            settings::api_key_status,
            settings::save_api_key,
            settings::save_hotkey,
            settings::get_personalize,
            settings::save_personalize,
            settings::get_transcribe_model,
            settings::save_transcribe_model,
            settings::is_model_downloaded,
            settings::download_progress,
            settings::start_model_download,
            settings::is_cleanup_downloaded,
            settings::start_cleanup_download,
            settings::is_whisper_downloaded,
            settings::start_whisper_download,
            settings::is_parakeet_downloaded,
            settings::start_parakeet_download,
            settings::recommended_stt,
            settings::get_stt_engine,
            settings::set_stt_engine,
            settings::get_cleanup_settings,
            settings::save_cleanup_settings,
            settings::discover_local_models,
            settings::restart_daemon,
            daemonctl::reset_keyboard,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
