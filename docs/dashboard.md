# Dashboard

## 1 Overview

The web dashboard runs at `http://127.0.0.1:9775` and auto-refreshes every 5 seconds. It shows all detected agent sessions — both managed (started by agentop) and external (detected from running processes).

## 2 Features

- Session list with tool, status, PID, runtime, memory, token usage, and working directory
- AI-generated conversation titles (Claude and Codex)
- Token usage breakdown (input, output, cache creation, cache read)
- In-browser terminal — click `▶` on any live session to attach to its tmux pane via xterm.js
- History tab — browse past sessions with resume support
- Session descriptions — set a label on any managed session

## 3 Running the dashboard

### 3.1 Manual start

```bash
./run_app.sh
```

Logs are written to `dashboard.log`. The PID is in `dashboard.pid`.

To restart:

```bash
[ -f dashboard.pid ] && kill $(cat dashboard.pid) 2>/dev/null; rm -f dashboard.pid
./run_app.sh
```

To stop:

```bash
kill $(cat dashboard.pid)
```

### 3.2 Systemd service (recommended)

`deploy/agentop-api.service` is the unit. It runs `src/api.py` directly, logs to
`/panda-infra/agentop/api.log`, restarts on any exit, and after each start runs
`deploy/ensure-general-session.sh` to make sure a general session exists.
`KillMode=process` means a stop or restart kills only the API, never the tmux
sessions, even when the unit started the tmux server.

```bash
systemctl --user status agentop-api
systemctl --user restart agentop-api
tail -f /panda-infra/agentop/api.log
```

### 3.3 First-time service setup

The unit is linked from the repo, not copied, so edits here are what systemd runs
after a `daemon-reload`.

```bash
mkdir -p /panda-infra/agentop
systemctl --user enable --now ~/workspace/agentop/deploy/agentop-api.service
loginctl enable-linger lulurun     # start at boot without login
```

## 4 API endpoints

The dashboard is backed by a FastAPI server. Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sessions` | List all sessions (from cache) |
| `GET` | `/api/sessions/{name}` | Session detail with process tree |
| `POST` | `/api/sessions/start` | Start a new managed session |
| `POST` | `/api/sessions/resume` | Resume a saved session |
| `POST` | `/api/sessions/{name}/stop` | Stop a managed session |
| `PATCH` | `/api/sessions/{name}/description` | Set session description |
| `GET` | `/api/saved-sessions` | List saved/historical sessions |
| `DELETE` | `/api/saved-sessions/{tool}/{id}` | Delete a saved session |
| `GET` | `/api/health` | Health check with last refresh timestamp |
| `WS` | `/ws/sessions/{name}/terminal` | WebSocket PTY terminal |

## 5 Nginx proxy

The dashboard is also accessible at `/agentop/` when proxied via nginx. Static assets and API paths are base-path aware.
