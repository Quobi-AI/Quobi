// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Headless support/debug: print the system-readiness snapshot as JSON and
    // exit, without spinning up the webview (which needs WebKitGTK + a display).
    // Handy for diagnosing a fresh install over SSH.
    if std::env::args().any(|a| a == "--readiness") {
        println!("{}", voice_type_desktop_lib::readiness_report());
        return;
    }
    if std::env::args().any(|a| a == "--start-ydotoold") {
        match voice_type_desktop_lib::start_ydotoold_cli() {
            Ok(()) => println!("ydotoold running"),
            Err(e) => {
                eprintln!("failed: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

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
