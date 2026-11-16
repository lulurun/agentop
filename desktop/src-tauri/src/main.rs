// No console window on Windows; harmless elsewhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! The desktop app: a thin wrapper around this computer's service.
//!
//! It runs `agentop serve` on loopback while it is open (see `service.rs`), then shows
//! the UI that service serves, signed in with the token in `service.json`. So the UI is
//! always the service's own, and updates with it. The wrapper updates itself from
//! GitHub Releases.
//!
//! The window gets no shell or file access: only the commands below, each doing one
//! fixed thing, writing text to the clipboard, the updater, and opening this project's
//! GitHub pages in the browser.

#[cfg(target_os = "linux")]
mod desktop_entry;
mod service;

use serde::{Deserialize, Serialize};
use service::{Service, Startup};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::ipc::CapabilityBuilder;
use tauri::{AppHandle, Manager, RunEvent};

const KEYCHAIN_SERVICE: &str = "agentop";
/// The revision of the commands below. The UI, which comes from the service and so may be
/// newer than this app, asks for an update when it needs a higher one.
const NATIVE_API: u32 = 1;
const RELEASES_URL: &str = "https://github.com/lulurun/agentop/releases";

#[derive(Deserialize)]
struct ServiceFile {
    token: String,
    name: String,
}

#[derive(Serialize)]
struct ServiceInfo {
    url: String,
    token: String,
    name: String,
}

/// The service on this machine, if there is one.
#[tauri::command]
fn read_service_info() -> Option<ServiceInfo> {
    let dir = service::data_dir()?;
    let service: ServiceFile =
        serde_json::from_str(&std::fs::read_to_string(dir.join("service.json")).ok()?).ok()?;
    Some(ServiceInfo {
        url: service::local_url(&service::read_config()),
        token: service.token,
        name: service.name,
    })
}

#[derive(Serialize)]
struct DesktopInfo {
    version: &'static str,
    native_api: u32,
    /// Whether the updater can replace this copy: not a development build, and on Linux
    /// only an AppImage. Otherwise the app links to the release page.
    self_update: bool,
    releases_url: &'static str,
    owns_service: bool,
    log: Option<String>,
}

#[tauri::command]
fn desktop_info(service: tauri::State<'_, Arc<Service>>) -> DesktopInfo {
    DesktopInfo {
        version: env!("CARGO_PKG_VERSION"),
        native_api: NATIVE_API,
        self_update: can_self_update(),
        releases_url: RELEASES_URL,
        owns_service: service.owns_service(),
        log: service::log_path().map(|path| path.display().to_string()),
    }
}

fn can_self_update() -> bool {
    if cfg!(debug_assertions) {
        return false;
    }
    if cfg!(target_os = "linux") {
        return std::env::var_os("APPIMAGE").is_some();
    }
    true
}

#[tauri::command]
fn startup_status(service: tauri::State<'_, Arc<Service>>) -> Startup {
    service.startup()
}

/// Start this computer's service again, after it stopped or failed to start.
#[tauri::command]
fn service_start(app: AppHandle) {
    start_in_background(app);
}

fn keychain_entry(id: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYCHAIN_SERVICE, id).map_err(|error| error.to_string())
}

#[tauri::command]
fn token_get(id: String) -> Result<Option<String>, String> {
    match keychain_entry(&id)?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

#[tauri::command]
fn token_set(id: String, token: String) -> Result<(), String> {
    keychain_entry(&id)?
        .set_password(&token)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn token_delete(id: String) -> Result<(), String> {
    match keychain_entry(&id)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

/// What the page from the service may call. Only that exact origin: any other page on
/// loopback gets nothing.
fn service_capability(url: &str) -> CapabilityBuilder {
    let mut capability = CapabilityBuilder::new("service")
        .remote(format!("{url}/*"))
        .local(false)
        .window("main");
    for permission in [
        "core:default",
        "clipboard-manager:allow-write-text",
        "updater:default",
        "process:allow-restart",
        "allow-read-service-info",
        "allow-desktop-info",
        "allow-startup-status",
        "allow-service-start",
        "allow-token-get",
        "allow-token-set",
        "allow-token-delete",
    ] {
        capability = capability.permission(permission);
    }
    capability.permission_scoped(
        "opener:allow-open-url",
        vec![serde_json::json!({ "url": format!("{RELEASES_URL}*") })],
        vec![],
    )
}

static CAPABILITY_ADDED: AtomicBool = AtomicBool::new(false);

/// Start or find the service, then show its UI. The window shows the start page meanwhile,
/// and stays on it, saying why, if the service doesn't come up.
fn start_in_background(app: AppHandle) {
    let service = app.state::<Arc<Service>>().inner().clone();
    service.mark_starting();
    std::thread::spawn(move || {
        let Startup::Ready { url } = service.start() else { return };
        if !CAPABILITY_ADDED.swap(true, Ordering::SeqCst) {
            if let Err(error) = app.add_capability(service_capability(&url)) {
                eprintln!("agentop: couldn't allow the service's page: {error}");
            }
        }
        let Some(window) = app.get_webview_window("main") else { return };
        let on_service = window
            .url()
            .map(|current| current.as_str().starts_with(&url))
            .unwrap_or(false);
        if on_service {
            return;
        }
        match url.parse() {
            Ok(parsed) => {
                if let Err(error) = window.navigate(parsed) {
                    eprintln!("agentop: couldn't open {url}: {error}");
                }
            }
            Err(error) => eprintln!("agentop: bad service address {url}: {error}"),
        }
    });
}

/// NVIDIA's own kernel driver, not nouveau, which is Mesa.
#[cfg(target_os = "linux")]
fn nvidia_driver_loaded() -> bool {
    std::path::Path::new("/proc/driver/nvidia/version").exists()
}

fn main() {
    // WebKitGTK's DMA-BUF renderer can show a blank window with NVIDIA's driver. Turning
    // it off everywhere made the terminal visibly slow on Mesa (AMD), where it works, so
    // only with that driver loaded. Set before any window exists; a value the user set wins.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() && nvidia_driver_loaded() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    #[cfg(target_os = "linux")]
    desktop_entry::register();

    let service = Arc::new(Service::new());
    let app = tauri::Builder::default()
        // Write-only: the capability grants writing text, never reading the clipboard.
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .manage(service.clone())
        .invoke_handler(tauri::generate_handler![
            read_service_info,
            desktop_info,
            startup_status,
            service_start,
            token_get,
            token_set,
            token_delete
        ])
        .setup(|app| {
            start_in_background(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to start agentop");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // Sessions are in tmux, so they keep running.
            service.stop();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tests share the process environment, so they take turns.
    static HOME_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn with_home(service: Option<&str>, config: Option<&str>) -> Option<ServiceInfo> {
        let _guard = HOME_LOCK.lock().unwrap();
        let dir = std::env::temp_dir().join(format!("agentop-test-{:?}", std::thread::current().id()));
        std::fs::create_dir_all(&dir).unwrap();
        for (name, body) in [("service.json", service), ("config.toml", config)] {
            let _ = std::fs::remove_file(dir.join(name));
            if let Some(body) = body {
                std::fs::write(dir.join(name), body).unwrap();
            }
        }
        std::env::set_var("AGENTOP_HOME", &dir);
        let info = read_service_info();
        std::env::remove_var("AGENTOP_HOME");
        std::fs::remove_dir_all(&dir).unwrap();
        info
    }

    const SERVICE: &str = r#"{"token":"t","instance_id":"i","name":"box"}"#;

    #[test]
    fn no_service_file_means_no_service() {
        assert!(with_home(None, None).is_none());
    }

    #[test]
    fn a_damaged_service_file_means_no_service() {
        assert!(with_home(Some("not json"), None).is_none());
    }

    #[test]
    fn defaults_to_the_loopback_port() {
        let info = with_home(Some(SERVICE), None).unwrap();
        assert_eq!(info.url, "http://127.0.0.1:9775");
    }

    #[test]
    fn follows_the_config() {
        let info = with_home(Some(SERVICE), Some("port = 9000\ntls_cert = \"c\"\n")).unwrap();
        assert_eq!(info.url, "https://127.0.0.1:9000");
    }

    #[test]
    fn a_wildcard_bind_is_reached_on_loopback() {
        let info = with_home(Some(SERVICE), Some("host = \"0.0.0.0\"\n")).unwrap();
        assert_eq!(info.url, "http://127.0.0.1:9775");
    }

    #[test]
    fn the_service_capability_builds() {
        // Panics on a permission identifier that doesn't parse.
        let _ = service_capability("http://127.0.0.1:9775");
    }
}
