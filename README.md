# agentop

Monitor and orchestrate AI agent CLI sessions (Claude Code, Codex, Antigravity).

![agentop dashboard](https://raw.githubusercontent.com/lulurun/agentop/main/docs/screenshot.png)

![in-browser terminal](https://raw.githubusercontent.com/lulurun/agentop/main/docs/screenshot-terminal.png)

## What it does

- Detects running Claude Code, Codex, and Antigravity processes via tmux
- Web dashboard showing session status, token usage, runtime, and memory
- In-browser terminal attached to any live session
- `agentop` CLI for starting, stopping, and browsing session history
- **Dialogue system** — orchestrates two agents collaborating on a shared task via a structured protocol

## Documentation

- [Architecture](docs/architecture.md) — code structure and module responsibilities
- [CLI reference](docs/cli.md) — all commands and options
- [Dialogue system](docs/dialogue.md) — two-agent dialogues, scenarios, and protocol
- [Dashboard](docs/dashboard.md) — web UI and systemd service setup

## Requirements

- Python 3.11+
- `tmux`

## Install

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
pip install -e .
```

## License

MIT
