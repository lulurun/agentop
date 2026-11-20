#!/usr/bin/env bash
# Release agentop as vX.Y.Z: one tag for the code and the desktop app. Sets the
# version in pyproject.toml, tauri.conf.json and Cargo.toml, commits, tags, and pushes.
# For X (vX.0.0) the "desktop" workflow then builds and publishes agentop-desktop; any
# other release is the tag alone. See docs/design.md 8.5.
#
# X is for the desktop app's wrapper (a download), Y for the UI (open pages reload), Z
# for the service only (a restart). With no argument, the part is picked from what
# changed since the last release: desktop/, then ui/, then anything else.
#
#   scripts/release.sh            # picked from the changes
#   scripts/release.sh x|y|z      # that part
#   scripts/release.sh 2.0.0      # exactly that
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

die() { echo "error: $*" >&2; exit 1; }

git fetch --quiet origin main --tags
last=$(git tag -l 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)
[[ -n "$last" ]] || last=v0.0.0
IFS=. read -r major minor patch <<<"${last#v}"

part="${1:-}"
if [[ -z "$part" ]]; then
  changed=$(git diff --name-only "$last"..HEAD 2>/dev/null || true)
  [[ -n "$changed" ]] || die "nothing has changed since $last"
  if grep -q '^desktop/' <<<"$changed"; then part=x
  elif grep -q '^ui/' <<<"$changed"; then part=y
  else part=z
  fi
  echo "Since $last: $part (desktop/ is x, ui/ is y, anything else z)"
fi
case "$part" in
  x|X) version="$((major + 1)).0.0" ;;
  y|Y) version="$major.$((minor + 1)).0" ;;
  z|Z) version="$major.$minor.$((patch + 1))" ;;
  *) version="$part" ;;
esac
tag="v$version"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "give x, y, z or a version like 1.2.3, not '$part'"
[[ "$(git branch --show-current)" == "main" ]] || die "not on main"
[[ -z "$(git status --porcelain)" ]] || die "uncommitted changes"
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
if [[ "$version" == *.0.0 ]]; then next="CI builds and publishes agentop-desktop $tag: a download."
else next="No app release: the desktop app and services update from the tag."
fi
read -r -p "Push the commit and tag $tag to GitHub? $next [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || die "stopped; nothing was pushed (undo the local commit with: git reset --hard origin/main && git tag -d $tag)"
git push -q origin main "$tag"
echo "Pushed $tag."
[[ "$version" != *.0.0 ]] || echo "Watch the build at https://github.com/lulurun/agentop/actions/workflows/desktop.yml"
