#!/usr/bin/env bash
# Release the service and its web UI as v<version>: set the version in pyproject.toml,
# commit, tag, and push. The "release" workflow then publishes the GitHub release.
# See docs/design.md section 8.5.
#
#   scripts/release-ui.sh 1.0.0
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
source scripts/release-lib.sh

version="${1:-}"
tag="v$version"
check_main
check_version "$version" "$tag"

# The UI must build before anything is tagged.
scripts/build-ui.sh

sed -i.bak -E "0,/^version = \".*\"/s//version = \"$version\"/" pyproject.toml && rm pyproject.toml.bak
git add pyproject.toml
git commit -q -m "Release $tag"
git tag -a "$tag" -m "agentop $tag"

git --no-pager log --oneline -1
confirm "Push the commit and tag $tag to GitHub?"
git push -q origin main "$tag"
echo "Pushed $tag. The release appears at https://github.com/lulurun/agentop/releases/tag/$tag"
