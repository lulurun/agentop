# agentop

A local dashboard for monitoring running AI agent sessions — Claude Code, Codex, and others.

![agentop dashboard](https://raw.githubusercontent.com/lulurun/agentop/main/docs/screenshot.png)

## What it does

- Detects running Claude Code and Codex processes
- Shows PID, runtime, CPU, memory, working directory
- Maps processes to tmux sessions
- Reads the AI-generated conversation title from Claude Code sessions
- Shows git branch and dirty status for each session's project
- Manual per-session description, tags, and status
- Auto-refreshing web UI at `http://127.0.0.1:8765`
- `agentop` CLI for starting and managing named sessions

## Requirements

- Python 3.10+
- `tmux` (optional, but recommended)

## Install

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
pip install -r requirements.txt
```

## Start the dashboard

```bash
./run.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser.

To stop:

```bash
kill $(cat dashboard.pid)
```

## CLI

Start a named session (creates a tmux session and registry entry):

```bash
./agentop start claude-myproject --tool claude --cwd ~/repos/myproject --description "Refactor auth module"
```

List sessions:

```bash
./agentop list
```

Update description:

```bash
./agentop set-description claude-myproject "Now debugging the token refresh flow"
```

Stop a session:

```bash
./agentop stop claude-myproject
```

### Recommended tmux naming convention

```
<tool>-<project>-<task>

claude-billing-fix
codex-trading-pnl
```

Starting sessions with matching tmux names makes process mapping automatic.

## Session registry

Manual metadata is stored in `~/.agent-dashboard/sessions.json`. Fields:

| Field | Description |
|---|---|
| `tool` | `claude`, `codex`, or `openclaw` |
| `cwd` | Working directory |
| `description` | Free-text notes about what the session is doing |
| `status` | `running`, `waiting`, `done` |
| `tmux_session` | tmux session name |
| `tags` | List of tags |

## How Claude Code titles are detected

For Claude Code sessions, agentop reads the AI-generated conversation title directly from `~/.claude/projects/<project>/<session>.jsonl`. These titles (e.g. *"Refactor PnLTracker fee handling"*) are automatically shown in the dashboard without any extra configuration.

## Architecture

```
agentop/
├── agentop              # CLI helper
├── dashboard/
│   ├── main.py          # FastAPI backend
│   ├── scanner.py       # Process, tmux, git, and file scanning
│   ├── registry.py      # JSON session registry
│   └── static/
│       └── index.html   # Web UI
├── run.sh               # Start script
└── spec.md              # Design spec
```

## License

MIT
