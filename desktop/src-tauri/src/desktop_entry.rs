//! Linux: a desktop entry and icon for this app, so the taskbar shows its icon.
//!
//! On Wayland the compositor ignores the window's own icon: it finds the `.desktop` file
//! named after the window's app id and shows that file's `Icon`. GTK takes the app id from
//! the program name, which `main` sets to `agentop-desktop`, so the entry is
//! `~/.local/share/applications/agentop-desktop.desktop`. Without it KDE shows a generic
//! "W". (Tauri's `enableGTKAppId` doesn't help: under KWin, GTK 3 still sends the program
//! name as the app id.) A bare binary or an AppImage installs nothing, so the app writes
//! both files itself, and rewrites them only when they change (a new icon, or the app moved).

use std::path::{Path, PathBuf};

const ICON: &[u8] = include_bytes!("../icons/icon.png");

pub fn register(app_id: &str) {
    if let Err(error) = try_register(app_id) {
        eprintln!("agentop-desktop: couldn't add the desktop entry: {error}");
    }
}

fn try_register(app_id: &str) -> std::io::Result<()> {
    let Some(data_home) = data_home() else { return Ok(()) };
    // An AppImage runs from a temporary mount; launch the AppImage itself.
    let exec = match std::env::var_os("APPIMAGE") {
        Some(path) => PathBuf::from(path),
        None => std::env::current_exe()?,
    };

    write_if_changed(&icon_path(&data_home, app_id), ICON)?;
    write_if_changed(
        &entry_path(&data_home, app_id),
        desktop_entry(app_id, &exec).as_bytes(),
    )?;
    Ok(())
}

fn entry_path(data_home: &Path, app_id: &str) -> PathBuf {
    data_home.join(format!("applications/{app_id}.desktop"))
}

fn icon_path(data_home: &Path, app_id: &str) -> PathBuf {
    data_home.join(format!("icons/hicolor/512x512/apps/{app_id}.png"))
}

fn data_home() -> Option<PathBuf> {
    match std::env::var_os("XDG_DATA_HOME") {
        Some(dir) if !dir.is_empty() => Some(PathBuf::from(dir)),
        _ => std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/share")),
    }
}

fn desktop_entry(app_id: &str, exec: &Path) -> String {
    format!(
        "[Desktop Entry]\n\
         Type=Application\n\
         Name=agentop-desktop\n\
         Comment=Agent sessions on this computer\n\
         Exec={}\n\
         Icon={app_id}\n\
         StartupWMClass={app_id}\n\
         Categories=Development;\n\
         Terminal=false\n",
        quote_exec(exec)
    )
}

/// The Exec key's quoting: in double quotes, with `"`, `` ` ``, `$` and `\` escaped, and a
/// literal `%` doubled.
fn quote_exec(path: &Path) -> String {
    let mut quoted = String::from("\"");
    for c in path.to_string_lossy().chars() {
        match c {
            '"' | '`' | '$' | '\\' => {
                quoted.push('\\');
                quoted.push(c);
            }
            '%' => quoted.push_str("%%"),
            _ => quoted.push(c),
        }
    }
    quoted.push('"');
    quoted
}

fn write_if_changed(path: &Path, content: &[u8]) -> std::io::Result<()> {
    if std::fs::read(path).ok().as_deref() == Some(content) {
        return Ok(());
    }
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, content)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exec_is_quoted() {
        assert_eq!(quote_exec(Path::new("/opt/a b/agentop")), "\"/opt/a b/agentop\"");
        assert_eq!(quote_exec(Path::new("/x/$y%z")), "\"/x/\\$y%%z\"");
    }

    #[test]
    fn entry_names_the_icon_after_the_app_id() {
        let entry = desktop_entry("agentop-desktop", Path::new("/usr/bin/agentop-desktop"));
        assert!(entry.contains("\nIcon=agentop-desktop\n"));
        assert!(entry.contains("\nStartupWMClass=agentop-desktop\n"));
        assert!(entry.contains("\nExec=\"/usr/bin/agentop-desktop\"\n"));
    }

    #[test]
    fn files_are_named_after_the_app_id() {
        let home = Path::new("/h");
        assert_eq!(
            entry_path(home, "agentop-desktop"),
            Path::new("/h/applications/agentop-desktop.desktop")
        );
        assert_eq!(
            icon_path(home, "agentop-desktop"),
            Path::new("/h/icons/hicolor/512x512/apps/agentop-desktop.png")
        );
    }
}
