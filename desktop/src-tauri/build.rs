fn main() {
    // Declaring the app's commands gives each an `allow-…` permission. Tauri then checks
    // them like plugin commands, which it must: the UI comes from the local service, and a
    // page from any origin other than the app's own can call only what a capability grants.
    let commands = &[
        "read_service_info",
        "desktop_info",
        "startup_status",
        "service_start",
        "token_get",
        "token_set",
        "token_delete",
        "setup_status",
        "setup_run",
        "setup_add_path",
        "update_open",
        "update_run",
    ];
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(commands)),
    )
    .expect("failed to run the tauri build script");
}
