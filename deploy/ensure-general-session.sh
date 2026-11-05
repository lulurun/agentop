#!/usr/bin/env bash
# Start a general Claude session if none is running. Run by agentop-api.service
# after the API starts (ExecStartPost); safe to run by hand.
set -u
AGENTOP="$HOME/.pyenv/versions/3.13.9/bin/agentop"
PYTHON="$HOME/.pyenv/versions/3.13.9/bin/python"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  "$AGENTOP" list --json >/dev/null 2>&1 && break
  sleep 1
done

if ! "$AGENTOP" list --json 2>/dev/null | "$PYTHON" -c "import sys,json; exit(0 if any(s['name'].startswith('general-') for s in json.load(sys.stdin)) else 1)"; then
  echo "Starting general Claude session…"
  "$AGENTOP" start general --tool claude --cwd "$HOME"
fi
