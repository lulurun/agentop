# agentop

A local dashboard for monitoring running AI agent sessions — Claude Code, Codex, and Gemini CLI.

![agentop dashboard](https://raw.githubusercontent.com/lulurun/agentop/main/docs/screenshot.png)

## What it does

- Detects running Claude Code, Codex, Gemini CLI, and Antigravity processes
- Shows session, tool, status, PID, runtime, memory, token usage, and working directory
- Reads AI-generated conversation titles from Claude Code and Codex sessions
- Shows token usage (input, output, cache) per session
- Shows git branch and dirty status for each session's project
- History tab: browse past sessions with resume support (active sessions excluded)
- Auto-refreshing web UI at `http://127.0.0.1:8765`
- `agentop` CLI for starting, stopping, and browsing session history

## Requirements

- Python 3.11+
- `tmux`

## Install

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
pip install -e .
```

## Start the dashboard

```bash
./run_app.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser.

To restart:

```bash
[ -f dashboard.pid ] && kill $(cat dashboard.pid) 2>/dev/null; rm -f dashboard.pid
./run_app.sh
```

To stop:

```bash
kill $(cat dashboard.pid)
```

Logs are written to `dashboard.log`.

## CLI

List running sessions:

```bash
agentop list
agentop list --json
```

Start a session in a named tmux window:

```bash
agentop start myapp --tool claude
agentop start myapp --tool claude --cwd ~/projects/myapp
agentop start myapp --tool codex
agentop start myapp --tool gemini
agentop start myapp --tool antigravity
```

Stop a session:

```bash
agentop stop myapp-12345
```

Browse session history:

```bash
agentop history
agentop history --tool claude
agentop history --limit 20 --json
```

## License

MIT
