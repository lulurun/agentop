# agentop

A local dashboard for monitoring running AI agent sessions — Claude Code, Codex, and Gemini CLI.

![agentop dashboard](https://raw.githubusercontent.com/lulurun/agentop/main/docs/screenshot.png)

## What it does

- Detects running Claude Code, Codex, and Gemini CLI processes
- Shows session, tool, status, PID, runtime, memory, token usage, and working directory
- Reads AI-generated conversation titles from Claude Code and Codex sessions
- Shows token usage (input, output, cache) per session
- Shows git branch and dirty status for each session's project
- Auto-refreshing web UI at `http://127.0.0.1:8765`
- `agentop` CLI for starting and managing named sessions

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

To stop:

```bash
kill $(cat dashboard.pid)
```

## CLI

List sessions:

```bash
agentop list
agentop list --json
```

Start a session (tmux session named `<session_name>-<pid>`):

```bash
agentop start myapp --tool claude
agentop start myapp --tool claude --cwd ~/projects/myapp
```

Stop a session:

```bash
agentop stop myapp-12345
```

## License

MIT
