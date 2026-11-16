#!/usr/bin/env bash
# Build the web UI into src/agentop/service/webdist/, which the service serves at /.
# A running service serves the new build at once: reload the page (or the desktop app).
#
#   scripts/build-ui.sh            # npm ci only when node_modules is missing
#   scripts/build-ui.sh --clean    # always npm ci
set -euo pipefail
cd "$(dirname "$0")/../ui"

if [[ "${1:-}" == "--clean" || ! -d node_modules ]]; then
    npm ci --no-audit --no-fund
fi
npm run build
echo "UI built: $(cd ../src/agentop/service/webdist && pwd)"
