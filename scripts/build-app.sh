#!/usr/bin/env bash
# Build the web UI, then the desktop app.
#
# The app shows the UI that this computer's service serves, so the UI build is what
# the app shows; the app binary itself holds only the wrapper and its start page.
#
#   scripts/build-app.sh             # binary: desktop/src-tauri/target/release/agentop-desktop
#   scripts/build-app.sh --appimage  # also an AppImage in .../bundle/appimage/ (Linux)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

"$root/scripts/build-ui.sh"

cd "$root/desktop"
[[ -d node_modules ]] || npm ci --no-audit --no-fund
if [[ "${1:-}" == "--appimage" ]]; then
    npm run package
    ls "$root"/desktop/src-tauri/target/release/bundle/appimage/*.AppImage
else
    npm run build
fi
