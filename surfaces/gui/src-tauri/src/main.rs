// Prevent a console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    #[cfg(target_os = "linux")]
    {
        // Fix blank white screen on Linux (Wayland/X11 + NVIDIA) caused by WebKitGTK renderer conflicts
        if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
        if std::env::var_os("WEBKIT_DISABLE_COMPOSITING_MODE").is_none() {
            std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
        }
        if std::env::var_os("GDK_BACKEND").is_none() && std::env::var_os("DISPLAY").is_some() {
            std::env::set_var("GDK_BACKEND", "x11");
        }
        if std::env::var_os("GTK_IM_MODULE").is_none() {
            if std::env::var_os("XMODIFIERS").map_or(false, |m| m.to_string_lossy().contains("ibus")) {
                std::env::set_var("GTK_IM_MODULE", "ibus");
            }
        }
    }

    astra_desktop_lib::run();
}

