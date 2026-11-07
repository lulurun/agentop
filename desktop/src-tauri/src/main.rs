// No console window on Windows; harmless elsewhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! The small native side of the app. The window gets no shell or file access.
//! It has these commands only, and each does one fixed thing.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Command;

const UNIT: &str = "agentop-api.service";
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
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".agent-dashboard"))
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

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Action {
    Status,
    Start,
    Stop,
    Restart,
}

#[derive(Serialize)]
struct ControlResult {
    installed: bool,
    active: bool,
    error: Option<String>,
}

fn systemctl(args: &[&str]) -> std::io::Result<std::process::Output> {
    Command::new("systemctl").arg("--user").args(args).output()
}

/// Runs systemctl directly: a GUI app doesn't get a login shell's PATH, so
/// `agentop` may not be findable. `action` can only be one of the variants above.
#[tauri::command]
fn service_control(action: Action) -> ControlResult {
    let verb = match action {
        Action::Status => None,
        Action::Start => Some("start"),
        Action::Stop => Some("stop"),
        Action::Restart => Some("restart"),
    };
    let mut error = None;
    if let Some(verb) = verb {
        match systemctl(&[verb, UNIT]) {
            Ok(output) if !output.status.success() => {
                error = Some(String::from_utf8_lossy(&output.stderr).trim().to_string());
            }
            Err(failure) => error = Some(failure.to_string()),
            Ok(_) => {}
        }
    }
    let show = systemctl(&["show", UNIT, "-p", "LoadState,ActiveState"]);
    let text = show
        .map(|output| String::from_utf8_lossy(&output.stdout).into_owned())
        .unwrap_or_default();
    ControlResult {
        installed: text.contains("LoadState=loaded"),
        active: text.contains("ActiveState=active"),
        error,
    }
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
        .invoke_handler(tauri::generate_handler![
            read_service_info,
            service_control,
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

    #[test]
    fn actions_outside_the_list_are_refused() {
        assert!(serde_json::from_str::<Action>("\"start\"").is_ok());
        for bad in ["\"disable\"", "\"start; rm -rf /\"", "\"edit\""] {
            assert!(serde_json::from_str::<Action>(bad).is_err());
        }
    }
}
