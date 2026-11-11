// No console window on Windows; harmless elsewhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! The small native side of the app. The window gets no shell or file access.
//! It has these commands only, and each does one fixed thing, plus writing text to
//! the clipboard.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

const DEFAULT_PORT: u16 = 9775;
const KEYCHAIN_SERVICE: &str = "agentop";

#[derive(Deserialize)]
struct ServiceFile {
    token: String,
    instance_id: String,
    name: String,
}

#[derive(Deserialize, Default)]
struct ConfigFile {
    host: Option<String>,
    port: Option<u16>,
    tls_cert: Option<String>,
}

#[derive(Serialize)]
struct ServiceInfo {
    url: String,
    token: String,
    instance_id: String,
    name: String,
}

fn data_dir() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("AGENTOP_HOME") {
        return Some(PathBuf::from(path));
    }
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".agentop"))
}

/// The service installed on this machine, if there is one.
#[tauri::command]
fn read_service_info() -> Option<ServiceInfo> {
    let dir = data_dir()?;
    let service: ServiceFile =
        serde_json::from_str(&std::fs::read_to_string(dir.join("service.json")).ok()?).ok()?;
    let config: ConfigFile = std::fs::read_to_string(dir.join("config.toml"))
        .ok()
        .and_then(|text| toml::from_str(&text).ok())
        .unwrap_or_default();
    let scheme = if config.tls_cert.is_some() { "https" } else { "http" };
    let host = match config.host.as_deref() {
        None | Some("0.0.0.0") | Some("::") => "127.0.0.1".to_string(),
        Some(host) => host.to_string(),
    };
    Some(ServiceInfo {
        url: format!("{scheme}://{host}:{}", config.port.unwrap_or(DEFAULT_PORT)),
        token: service.token,
        instance_id: service.instance_id,
        name: service.name,
    })
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

fn main() {
    tauri::Builder::default()
        // Write-only: the capability grants writing text, never reading the clipboard.
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![
            read_service_info,
            token_get,
            token_set,
            token_delete
        ])
        .run(tauri::generate_context!())
        .expect("failed to start agentop");
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
        assert_eq!(info.instance_id, "i");
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
}
