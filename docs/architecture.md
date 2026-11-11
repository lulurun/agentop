# Architecture

## 1 Overview

agentop has three concerns: detecting and managing agent processes, orchestrating multi-agent dialogues, and serving a web dashboard.

```
src/
  api.py                   FastAPI web server
  agentop/
    cli.py                 CLI entry point (the `agentop` command)
    agentcli.py            AgentCli base class
    agentclis/             Concrete agent CLI implementations
    actor.py               Sends/receives over tmux
    actors/                Actor subclasses (per-agent send behaviour)
    agent_instance.py      Wires AgentCli + Actor into one runtime object
    scanner.py             Process detection and session building
    ops.py                 Business logic used by both API and CLI
    registry.py            Persistent managed-session registry
    tmux.py                tmux subprocess wrappers
    dialogue/              Two-agent dialogue system
```

### Core and plugins

Everything described here is the **core**: always loaded, never switched off. New service-side features are **plugins**, one folder each under `src/agentop/plugins/` (UI under `ui/src/plugins/`), loaded by the core plugin loader, `service/plugins.py` (`PluginHost`), which lists them at `/api/v2/plugins` and enables or disables them. A missing, failing, or disabled plugin only hides its own feature. Plugins hear about sessions through `SessionStore.add_listener`, which turns each refresh into `session.started`, `session.exited`, `session.closed`, and `scan` events. The design is in [v2-design.md section 12](v2-design.md#12-plugins). Dialogues (section 5 below) are planned as the first plugin and will move to `src/agentop/plugins/dialogue/`.

## 2 Agent layer

### 2.1 AgentCli (`agentcli.py`)

Base class for every supported agent CLI tool. Defines:
- `name` — tool identifier (`"claude"`, `"codex"`, `"antigravity"`)
- `idle_seconds` — how long screen must be stable before treating output as complete
- `matches(proc_name, cmdline)` — process detection
- `start_session()`, `resume_session()` — tmux session lifecycle
- `get_saved_sessions()`, `delete_session()` — history management
- `get_session_meta()` — token usage, AI title, bridge URL

### 2.2 `agentclis/`

One file per supported tool:

| File | Class | Notes |
|------|-------|-------|
| `claude.py` | `ClaudeAgentCli` | Reads `~/.claude/` for session metadata and token usage |
| `codex.py` | `CodexAgentCli` | Reads `~/.codex/state_5.sqlite` |
| `antigravity.py` | `AntigravityAgentCli` | `idle_seconds = 15` (post-completion UI updates) |

`agentclis/__init__.py` exports `AGENTS` list and `get_agent(name)`.

## 3 Actor layer

### 3.1 Actor (`actor.py`)

Generic tmux send/receive. Knows nothing about the dialogue protocol.

- `send(text)` — pastes text into the tmux session and presses Enter
- `receive() -> str | None` — polls the screen until stable for `idle_seconds`, returns full scrollback

### 3.2 `actors/`

Subclasses that override `send()` for tools whose terminals treat bare newlines as Enter:

| File | Class | Why needed |
|------|-------|-----------|
| `actors/antigravity.py` | `AntigravityActor` | agy submits on bare newline |
| `actors/codex.py` | `CodexActor` | Codex submits on bare newline |

Both use `Session.paste_text_bracketed()` (ESC[200~…ESC[201~) so multi-line prompts arrive intact.

`actors/__init__.py` exports `get_actor(name) -> type[Actor]`, defaulting to `Actor`.

### 3.3 AgentInstance (`agent_instance.py`)

Wires one `AgentCli` and one `Actor` together for a single running process.

```python
AgentInstance(agent, tmux_session_name, role_name)
  .actor      # the Actor
  .stop()     # /exit → wait 3s → kill tmux session
  .send_command(text)
  .attach(stop_event)
```

`AgentInstance` is the object `ops.py` creates when starting, stopping, or sending to a session.

## 4 Session management

### 4.1 Scanner (`scanner.py`)

Stateless — reads live process state and tmux pane list on every call.

- `scan_processes()` — iterates psutil, matches against `AGENTS`
- `scan_tmux()` — calls `tmux list-panes`
- `map_process_to_tmux()` — matches a PID to its tmux session via ancestry
- `build_sessions()` — combines process + tmux + agent metadata into session dicts

### 4.1a Where agentop keeps its files (`paths.py`)

Everything agentop writes is under `~/.agentop` (`AGENTOP_HOME` moves it):

| Path | What |
|------|------|
| `config.toml` | Settings you edit |
| `service.json` | The service's name and token, mode `0600` |
| `state/` | What agentop rewrites as it runs: `sessions.json` and its lock, `plugins.json`, `update-status.json`, `update.pid` |
| `logs/` | `serve.log` (a service started by hand), `update.log` |
| `plugins/{id}/` | Each plugin's own files: `store.json` from `api.store`, and anything else under `api.data_dir`. The Memory archive is `plugins/memory/`, and saved dialogues are in `plugins/dialogue/dialogues/` |

New kinds of files go in one of these folders, not at the top.

The folder was `~/.agent-dashboard` with every file at the top until 2026-11-11. `agentop serve` moves it at start (`paths.migrate()`), once the old service has stopped, and leaves nothing at the old name. Until the service has moved it, every command keeps using the old folder as it was. A desktop app built before the move looks in the old folder, so it needs rebuilding.

### 4.1b Transcripts as conversations (`conversation.py`, `service/transcripts.py`)

`service/transcripts.py` lists a session's transcript files, the ones its tool reports (`AgentCli.transcript_files`). When the tool has deleted them it looks in the Memory archive, which keeps each tool's layout, so the same lookup works with the archive as its root. `conversation.py` reads one of those files as prompts and replies, per tool; the File viewer shows it and the Memory plugin's messages table is built from it.

### 4.2 Registry (`registry.py`)

JSON file at `~/.agentop/state/sessions.json`. Tracks which sessions were started by agentop (managed) and stores user-set descriptions.

### 4.3 Ops (`ops.py`)

Business logic layer called by both `api.py` and `agentop/cli.py`:

| Function | What it does |
|----------|-------------|
| `get_sessions()` | Merge scanner output with registry |
| `start()` | Launch agent via `AgentCli.start_session()`, register |
| `stop()` | Create `AgentInstance`, call `.stop()` |
| `send()` | Create `AgentInstance`, call `.send_command()` |
| `get_saved_sessions()` | Aggregate history from all agents |
| `resume_session()` | Resume via `AgentCli.resume_session()` |

## 5 Dialogue system

See [dialogue.md](dialogue.md) for full detail.

Key modules:

| Module | Role |
|--------|------|
| `dialogue/model.py` | `Dialogue` and `DialogueMeta` (persisted to `~/.agentop/plugins/dialogue/dialogues/`) |
| `dialogue/orchestrator.py` | `DialogueOrchestrator` thread — relay loop |
| `dialogue/parser.py` | `ResponseParser` — extracts BEGIN/END blocks from scrollback |
| `dialogue/status.py` | `DialogueStatus` and `ReceiveStatus` enums |
| `dialogue/scenarios/` | TOML scenario files and reader |
| `dialogue/ops.py` | `start_dialogue()`, `list_dialogues()`, `stop_dialogue()`, `remove_dialogue()` |
| `dialogue/runner.py` | Subprocess entry point launched by `ops.start_dialogue()` |

## 6 Web layer

### Main-area tabs (v2 app)

The main area holds tabs in one or two groups side by side (`ui/src/tabs.ts`, pure functions over a `Layout`, kept per connection in `store.ts` and saved in `localStorage`). A tab is a container: it shows a session's terminal, or a file through the enabled `file-viewer` plugin. Clicking a session shows it in the focused group, replacing the session tab shown there; Ctrl or Cmd, the middle button, or the row's menu opens it in a new tab or to the side. The focused group's active tab decides the session in the header and right panel. Closing a session closes its tabs.

The right panel lists the working directory (Files) and the agent tool's own log files for the session (Transcripts, `GET /api/v2/sessions/{id}/transcripts`, from `AgentCli.transcript_files()`). Only files listed there can be read, through the File viewer plugin's route.

`api.py` is a FastAPI app. A background task refreshes `_cache["sessions"]` every 5 seconds by calling `ops.get_sessions()`. All session-list reads are served from cache; mutations (start, stop, send) go directly to `ops`.

The frontend (`html/`) is vanilla JS + xterm.js. The in-browser terminal proxies a `tmux attach-session` over a WebSocket PTY bridge in `api.py`.
