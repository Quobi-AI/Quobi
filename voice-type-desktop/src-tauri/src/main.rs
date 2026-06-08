// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // WebKitGTK's DMA-BUF renderer crashes with "Error 71 (Protocol error)"
    // on many Wayland setups (especially NVIDIA). Disabling it before GTK
    // initializes is the standard, well-tested fix and costs nothing on
    // setups that don't need it. Linux-only; no effect on Windows/macOS.
    #[cfg(target_os = "linux")]
    {
        if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
    }
    voice_type_desktop_lib::run()
}
