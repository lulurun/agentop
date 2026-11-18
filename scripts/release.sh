#!/usr/bin/env bash
# Release agentop as v<version>: one tag for the code and the desktop app. Sets the
# version in pyproject.toml, tauri.conf.json and Cargo.toml, commits, tags, and pushes.
# The "desktop" workflow then builds and publishes agentop-desktop. See docs/design.md 8.5.
#
#   scripts/release.sh 1.2.0
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

die() { echo "error: $*" >&2; exit 1; }

version="${1:-}"
tag="v$version"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 1.2.3, not '$version'"
[[ "$(git branch --show-current)" == "main" ]] || die "not on main"
[[ -z "$(git status --porcelain)" ]] || die "uncommitted changes"
git fetch --quiet origin main --tags
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || die "main differs from origin/main: pull or push first"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "tag $tag already exists"

# Nothing is tagged that doesn't build.
scripts/build-app.sh

sed -i.bak -E "0,/^version = \".*\"/s//version = \"$version\"/" pyproject.toml && rm pyproject.toml.bak
conf=desktop/src-tauri/tauri.conf.json
node -e '
  const fs = require("fs"), [file, version] = process.argv.slice(1);
  const conf = JSON.parse(fs.readFileSync(file, "utf8"));
  conf.version = version;
  fs.writeFileSync(file, JSON.stringify(conf, null, 2) + "\n");
' "$conf" "$version"
# desktop_info reports the Cargo version, so it must match too.
sed -i.bak -E "0,/^version = \".*\"/s//version = \"$version\"/" desktop/src-tauri/Cargo.toml && rm desktop/src-tauri/Cargo.toml.bak
(cd desktop/src-tauri && cargo update -p agentop-desktop --offline -q)

git add pyproject.toml "$conf" desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock
git commit -q -m "Release $tag"
git tag -a "$tag" -m "agentop $tag"

git --no-pager log --oneline -1
read -r -p "Push the commit and tag $tag to GitHub? CI builds and publishes agentop-desktop. [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || die "stopped; nothing was pushed (undo the local commit with: git reset --hard origin/main && git tag -d $tag)"
git push -q origin main "$tag"
echo "Pushed $tag. Watch the build at https://github.com/lulurun/agentop/actions/workflows/desktop.yml"
