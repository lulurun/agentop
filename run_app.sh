#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Install package (editable) if not already installed
if ! python3 -c "import agentop" 2>/dev/null; then
  echo "Installing agentop..."
  pip3 install -e .
fi

PID_FILE="$SCRIPT_DIR/dashboard.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dashboard already running (pid $(cat "$PID_FILE")). Stop it first with: kill \$(cat dashboard.pid)"
  exit 1
fi

echo "Starting Agentop Dashboard at http://127.0.0.1:8765"
echo "Logging to $SCRIPT_DIR/dashboard.log"

nohup python3 "$SCRIPT_DIR/src/api.py" \
  > "$SCRIPT_DIR/dashboard.log" 2>&1 &

echo $! > "$PID_FILE"
echo "Started (pid $!). To stop: kill \$(cat dashboard.pid)"
