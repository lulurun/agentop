#!/usr/bin/env bash
# Update the service from git, and go back to the old commit if that fails.
#
# Started by agentop.service.update, apart from the service, because it restarts
# the service: in a transient unit under systemd, otherwise as a detached
# process. The service passes the command that restarts it. Progress goes to
# update-status.json, and detail to update.log, both in the agentop data directory.
#
# Everything is inside main, because git pull can replace this file while it runs
# and bash reads a script as it goes. A function is parsed whole before it starts.
set -uo pipefail

REPO=${AGENTOP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
DATA=${AGENTOP_HOME:-$HOME/.agent-dashboard}
UNIT=${AGENTOP_UNIT:-agentop-api.service}
BRANCH=${AGENTOP_BRANCH:-main}
PYTHON=${AGENTOP_PYTHON:-python3}
HEALTH_URL=${AGENTOP_HEALTH_URL:-http://127.0.0.1:9775/api/v2/health}
HEALTH_TIMEOUT=${AGENTOP_HEALTH_TIMEOUT:-30}
INSTALL_CMD=${AGENTOP_INSTALL_CMD:-"$PYTHON" -m pip install --quiet -e .}
BUILD_CMD=${AGENTOP_BUILD_CMD:-cd ui && npm ci --silent && npm run -s build}
RESTART_CMD=${AGENTOP_RESTART_CMD:-systemctl --user daemon-reload && systemctl --user restart "$UNIT"}

STATUS="$DATA/update-status.json"
OLD=""
NEW=""

status() { # state step message
    "$PYTHON" - "$STATUS" "$1" "$2" "$3" "$OLD" "$NEW" <<'PY'
import json, os, sys, time
path, state, step, message, old, new = sys.argv[1:]
body = {"state": state, "step": step or None, "message": message or None,
        "from_commit": old[:7] or None, "to_commit": new[:7] or None, "updated_at": int(time.time())}
with open(path + ".tmp", "w") as f:
    json.dump(body, f)
os.replace(path + ".tmp", path)
PY
}

refuse() { # message: nothing has changed
    echo "Refused: $1"
    status failed "" "$1"
    exit 1
}

healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    while ((SECONDS < deadline)); do
        curl -fsSk -m 2 "$HEALTH_URL" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

deploy() { # the working tree, whichever commit it is on
    status running installing "" && (eval "$INSTALL_CMD") || return 1
    status running building "" && (eval "$BUILD_CMD") || return 1
    status running restarting "" && (eval "$RESTART_CMD") || return 1
    status running verifying "" && healthy
}

roll_back() { # why
    echo "Rolling back to $OLD: $1"
    status running rolling_back "$1"
    if git reset --hard "$OLD" && deploy; then
        status rolled_back "" "$1 The service is back on the previous version."
        exit 1
    fi
    status rollback_failed "" "$1 Going back also failed, so the service needs attention on this machine."
    exit 2
}

find_node() {
    command -v npm >/dev/null && return
    # A unit doesn't get a login shell's PATH, and nvm only exists in one.
    local bin
    bin=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
    [[ -n "$bin" ]] && PATH="$bin:$PATH"
}

main() {
    mkdir -p "$DATA"
    exec >>"$DATA/update.log" 2>&1
    echo "=== update $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cd "$REPO" || refuse "The install is missing: $REPO"
    export GIT_SSH_COMMAND="ssh -o BatchMode=yes" GIT_TERMINAL_PROMPT=0

    status running checking ""
    find_node
    OLD=$(git rev-parse HEAD)
    [[ "$(git branch --show-current)" == "$BRANCH" ]] || refuse "The install isn't on $BRANCH."
    [[ -z "$(git status --porcelain)" ]] || refuse "The install has uncommitted changes."
    [[ -z "${AGENTOP_BUILD_CMD:-}" ]] && ! command -v npm >/dev/null && refuse "npm isn't available to the service."
    git fetch --quiet || refuse "Could not reach the git remote."
    NEW=$(git rev-parse '@{upstream}')
    if [[ "$OLD" == "$NEW" ]]; then
        status done "" "Already up to date."
        exit 0
    fi
    git merge-base --is-ancestor "$OLD" "$NEW" || refuse "The install has commits that aren't on the remote."

    status running pulling ""
    git merge --ff-only --quiet "$NEW" || refuse "Could not fast-forward to the new commit."
    deploy || roll_back "The new version failed to install or start."
    status done "" ""
    exit 0
}

main
