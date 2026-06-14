# Architecture

## 1 Overview

agentop has three concerns: detecting and managing agent processes, orchestrating multi-agent dialogues, and serving a web dashboard.

```
src/
  api.py                   FastAPI web server
  cli.py                   CLI entry point
  agentop/
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

### 4.2 Registry (`registry.py`)

JSON file at `~/.agent-dashboard/registry.json`. Tracks which sessions were started by agentop (managed) and stores user-set descriptions.

### 4.3 Ops (`ops.py`)

Business logic layer called by both `api.py` and `cli.py`:

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
| `dialogue/model.py` | `Dialogue` and `DialogueMeta` (persisted to `~/.agent-dashboard/dialogues/`) |
| `dialogue/orchestrator.py` | `DialogueOrchestrator` thread — relay loop |
| `dialogue/parser.py` | `ResponseParser` — extracts BEGIN/END blocks from scrollback |
| `dialogue/status.py` | `DialogueStatus` and `ReceiveStatus` enums |
| `dialogue/scenarios/` | TOML scenario files and reader |
| `dialogue/ops.py` | `start_dialogue()`, `list_dialogues()`, `stop_dialogue()`, `remove_dialogue()` |
| `dialogue/runner.py` | Subprocess entry point launched by `ops.start_dialogue()` |

## 6 Web layer

`api.py` is a FastAPI app. A background task refreshes `_cache["sessions"]` every 5 seconds by calling `ops.get_sessions()`. All session-list reads are served from cache; mutations (start, stop, send) go directly to `ops`.

The frontend (`html/`) is vanilla JS + xterm.js. The in-browser terminal proxies a `tmux attach-session` over a WebSocket PTY bridge in `api.py`.
