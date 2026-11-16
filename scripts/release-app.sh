#!/usr/bin/env bash
# Release the desktop app as desktop-v<version>: set the version in tauri.conf.json and
# Cargo.toml, commit, tag, and push. The "desktop" workflow then builds, signs, and
# publishes it, and installed apps offer the update. See docs/design.md section 8.5.
#
# The UI isn't part of an app release: the app shows the UI its service serves, which
# ships with the service (release-ui.sh). This builds it anyway, with the app, to check
# that both build before anything is tagged.
#
#   scripts/release-app.sh 0.2.0
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
source scripts/release-lib.sh

version="${1:-}"
tag="desktop-v$version"
check_main
check_version "$version" "$tag"

scripts/build-app.sh

conf=desktop/src-tauri/tauri.conf.json
node -e '
  const fs = require("fs"), [file, version] = process.argv.slice(1);
  const conf = JSON.parse(fs.readFileSync(file, "utf8"));
  conf.version = version;
  fs.writeFileSync(file, JSON.stringify(conf, null, 2) + "\n");
' "$conf" "$version"
# desktop_info reports the Cargo version, so the two must match.
sed -i.bak -E "0,/^version = \".*\"/s//version = \"$version\"/" desktop/src-tauri/Cargo.toml && rm desktop/src-tauri/Cargo.toml.bak
(cd desktop/src-tauri && cargo metadata --offline --format-version 1 >/dev/null)  # updates Cargo.lock

git add "$conf" desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock
git commit -q -m "Release $tag"
git tag -a "$tag" -m "agentop desktop $tag"

git --no-pager log --oneline -1
confirm "Push the commit and tag $tag to GitHub? CI builds and publishes the app."
git push -q origin main "$tag"
echo "Pushed $tag. Watch the build at https://github.com/lulurun/agentop/actions/workflows/desktop.yml"
