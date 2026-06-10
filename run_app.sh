#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYENV_ROOT:-$HOME/.pyenv}/shims/python"
AGENTOP="${PYENV_ROOT:-$HOME/.pyenv}/shims/agentop"

# Install package (editable) if not already installed
if ! "$PYTHON" -c "import agentop" 2>/dev/null; then
  echo "Installing agentop..."
  "$PYTHON" -m pip install -e . -q
fi

PID_FILE="$SCRIPT_DIR/dashboard.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dashboard already running (pid $(cat "$PID_FILE")). Stop it first with: kill \$(cat dashboard.pid)"
  exit 1
fi

echo "Starting Agentop Dashboard at http://127.0.0.1:9775"
echo "Logging to $SCRIPT_DIR/dashboard.log"

nohup "$PYTHON" "$SCRIPT_DIR/src/api.py" \
  >> "$SCRIPT_DIR/dashboard.log" 2>&1 &

echo $! > "$PID_FILE"
echo "Started (pid $!). To stop: kill \$(cat dashboard.pid)"

# Start a general Claude session if one isn't already running
sleep 3
if ! "$AGENTOP" list --json 2>/dev/null | "$PYTHON" -c "import sys,json; exit(0 if any(s['name'].startswith('general-') for s in json.load(sys.stdin)) else 1)" 2>/dev/null; then
  echo "Starting general Claude session…"
  "$AGENTOP" start general --tool claude --cwd "$HOME"
fi
