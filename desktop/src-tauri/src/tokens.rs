//! Client tokens for other services: in the OS keychain, or, where there is none (Linux
//! with no secret service running), in `state/desktop-tokens.json`, mode 0600. That is no
//! weaker than `service.json`, which keeps the service token itself in plain text.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;

const KEYCHAIN_SERVICE: &str = "agentop";

static FILE_LOCK: Mutex<()> = Mutex::new(());

fn keychain(id: &str) -> keyring::Result<keyring::Entry> {
    keyring::Entry::new(KEYCHAIN_SERVICE, id)
}

fn file_path() -> Option<PathBuf> {
    crate::service::data_dir().map(|dir| dir.join("state").join("desktop-tokens.json"))
}

fn read_file() -> BTreeMap<String, String> {
    file_path()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn write_file(tokens: &BTreeMap<String, String>) -> Result<(), String> {
    let path = file_path().ok_or("no home directory")?;
    if tokens.is_empty() {
        return match std::fs::remove_file(&path) {
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error.to_string()),
            _ => Ok(()),
        };
    }
    let dir = path.parent().ok_or("no state directory")?;
    std::fs::create_dir_all(dir).map_err(|error| error.to_string())?;
    let temp = path.with_extension("json.tmp");
    let text = serde_json::to_string(tokens).map_err(|error| error.to_string())?;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    std::io::Write::write_all(&mut options.open(&temp).map_err(|error| error.to_string())?, text.as_bytes())
        .map_err(|error| error.to_string())?;
    std::fs::rename(&temp, &path).map_err(|error| error.to_string())
}

fn update_file(change: impl FnOnce(&mut BTreeMap<String, String>)) -> Result<(), String> {
    let _guard = FILE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut tokens = read_file();
    let before = tokens.clone();
    change(&mut tokens);
    if tokens == before {
        return Ok(());
    }
    write_file(&tokens)
}

pub fn get(id: &str) -> Result<Option<String>, String> {
    if let Ok(Ok(token)) = keychain(id).map(|entry| entry.get_password()) {
        return Ok(Some(token));
    }
    let _guard = FILE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    Ok(read_file().remove(id))
}

pub fn set(id: &str, token: &str) -> Result<(), String> {
    match keychain(id).and_then(|entry| entry.set_password(token)) {
        Ok(()) => update_file(|tokens| {
            tokens.remove(id);
        }),
        Err(_) => update_file(|tokens| {
            tokens.insert(id.to_owned(), token.to_owned());
        }),
    }
}

pub fn delete(id: &str) -> Result<(), String> {
    // A keychain that can't be reached holds nothing of ours.
    let _ = keychain(id).and_then(|entry| entry.delete_credential());
    update_file(|tokens| {
        tokens.remove(id);
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_get_delete_round_trip() {
        let home = std::env::temp_dir().join(format!("agentop-tokens-test-{}", std::process::id()));
        std::env::set_var("AGENTOP_HOME", &home);
        let id = format!("test-{}", std::process::id());
        set(&id, "secret").unwrap();
        assert_eq!(get(&id).unwrap().as_deref(), Some("secret"));
        delete(&id).unwrap();
        assert_eq!(get(&id).unwrap(), None);
        let _ = std::fs::remove_dir_all(home);
    }
}
