# Shared by release-ui.sh and release-app.sh. Sourced, not run.

die() { echo "error: $*" >&2; exit 1; }

# A release is cut from main as it is on GitHub, with nothing uncommitted.
check_main() {
    [[ "$(git branch --show-current)" == "main" ]] || die "not on main"
    [[ -z "$(git status --porcelain)" ]] || die "uncommitted changes"
    git fetch --quiet origin main --tags
    [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || die "main differs from origin/main: pull or push first"
}

check_version() { # version tag
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 1.2.3, not '$1'"
    git rev-parse -q --verify "refs/tags/$2" >/dev/null && die "tag $2 already exists"
    return 0
}

confirm() { # message
    read -r -p "$1 [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || die "stopped; nothing was pushed (undo the local commit with: git reset --hard origin/main)"
}
