# agentop

Run AI coding agents (Claude Code, Codex, Antigravity) in tmux, and work with all of them from one window.

## The idea

Agent CLIs are built to run in a terminal, and tmux is the natural place to keep them running. Agentop does not replace the terminal with a chat view, and it does not call any LLM API. It starts each agent in its own tmux session, finds the ones you started yourself, and brings them all into one app. Each agent is used the way it was built to be used, and runs on your own login and subscription.

```
  web app ─┐                      ┌─ tmux: claude   (fix-auth-a1b2c3)
           ├── agentop service ───┼─ tmux: codex    (review-7f3a91)
  desktop ─┘    (one per machine) └─ tmux: agy      (eiger-c4d2e0)
```

## Core concepts

- **Session.** One agent CLI running in one tmux session. Its id is the tmux session name. A session agentop started is **managed**: it can be stopped, resumed, and sent text. One agentop finds running on its own is **external**: it is listed, and can be attached if it is in tmux.
- **Sessions outlive everything else.** Closing the app, or restarting or updating the service, leaves every agent running. tmux owns the agents; agentop only attaches to them.
- **Service.** `agentop serve` runs on each machine that runs agents. It keeps the session list, bridges terminals over WebSocket, and serves the `/api/v2` API. Every request except the health check needs the service's token.
- **App.** One UI, built as a web app (served by the service at `/app/`) and as a Tauri desktop app. Sessions are on the left, the agent's live terminal is in the middle, and details (token usage, changed files) are on the right. The terminal is a real `tmux attach`, so everything the agent's TUI does works as it does in your own terminal.
- **Connections.** The app can add any agentop service it can reach, on this machine or another, and switch between them. It can also see the status of the service it is connected to, and restart or update it.
- **History.** Past conversations of each tool, which can be resumed as new sessions.
- **CLI.** The `agentop` command does the same from a shell: start, stop, list, history, and service control.

## Plugins

Everything above is the **core**. It is always loaded and cannot be switched off. New features are built as **plugins**:

- Each plugin is one folder under `src/agentop/plugins/`, with its UI under `ui/src/plugins/`, and a `plugin.toml` manifest.
- A core loader finds them at service start and loads each one on its own. A plugin gets a narrow API: its own routes under `/api/v2/plugins/<id>/`, core events, supervised background tasks, a way to drive agents, and its own data file.
- Plugin UI goes into fixed slots in the app, such as a badge on a session row, a view in the left column, or a section in Settings. Settings → Plugins switches each plugin on or off for that service.
- **A plugin can fail, be removed, or be disabled, and the only visible effect is that its feature disappears.** Sessions, terminals, and service control keep working.

The plugin system is designed and the loader is being built; see [TODO.md](TODO.md). What each plugin does, starting with dialogues, is in [docs/plugins.md](docs/plugins.md).

## Requirements

- Linux with systemd, Python 3.11+, and tmux
- Node 20.19+ or 22.12+, to build the app
- At least one agent CLI: `claude`, `codex`, or `agy`

## Install

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
pip install -e .
(cd ui && npm ci && npm run build)   # the build output is not committed
agentop serve                        # http://127.0.0.1:9775
agentop open                         # opens the app, signed in
```

Running it as a service, connecting to other machines, and HTTPS are in [docs/v2-setup.md](docs/v2-setup.md).

## Documentation

- [Setup](docs/v2-setup.md): running the service and the app
- [CLI reference](docs/cli.md): every command and option
- [Architecture](docs/architecture.md): code structure and modules
- [Design](docs/v2-design.md): the full v2 design, including the plugin system (section 12)
- [Plugins](docs/plugins.md): what each plugin does
- [TODO](TODO.md): work agreed but not started

## License

MIT
