# agentop

A local dashboard for monitoring running AI agent sessions — Claude Code, Codex, Gemini, and others.

![agentop dashboard](https://raw.githubusercontent.com/lulurun/agentop/main/docs/screenshot.png)

## What it does

- Detects running Claude Code, Codex, and Gemini CLI processes
- Shows PID, runtime, memory, working directory, and token usage
- Maps processes to tmux sessions
- Reads AI-generated conversation titles from Claude Code and Codex sessions
- Shows token usage (input, output, cache) per session
- Shows git branch and dirty status for each session's project
- Manual per-session description and status
- Auto-refreshing web UI at `http://127.0.0.1:8765`
- `agentop` CLI for starting and managing named sessions
- Agent Files tab showing recently modified files across all agent directories

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

Start a named session (creates a tmux session):

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

## License

MIT
