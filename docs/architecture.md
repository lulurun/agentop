# Architecture

## 1 Overview

agentop has three concerns: detecting and managing agent processes, orchestrating multi-agent dialogues, and serving a web dashboard.

```
src/
  api.py                   FastAPI web server
  agentop/
    cli.py                 CLI entry point (the `agentop` command)
    agentclis/             One module per agent tool: everything that differs between tools
    conversation.py        What every transcript reader shares (Message, JSONL, .zst)
    actor.py               Sends/receives over tmux
    agent_instance.py      Wires AgentCli + Actor into one runtime object
    scanner.py             Process detection and session building
    ops.py                 Business logic used by both API and CLI
    registry.py            Persistent managed-session registry
    tmux.py                tmux subprocess wrappers
    skills/                Skills `agentop serve` installs for the agent tools
    service/host.py        What `agentop serve` checks and installs as it starts
    dialogue/              Two-agent dialogue system
```

### Core and plugins

Everything described here is the **core**: always loaded, never switched off. New service-side features are **plugins**, one folder each under `src/agentop/plugins/` (UI under `ui/src/plugins/`), loaded by the core plugin loader, `service/plugins.py` (`PluginHost`), which lists them at `/api/v2/plugins` and enables or disables them. A plugin can also add `agentop` commands, from a `cli.py` the CLI loads itself (`plugin_cli.py`), without the service; what both read from a plugin's `plugin.toml` is in `plugin_manifest.py`. A missing, failing, or disabled plugin only hides its own feature. Plugins hear about sessions through `SessionStore.add_listener`, which turns each refresh into `session.started`, `session.exited`, `session.closed`, and `scan` events. The design is in [v2-design.md section 12](v2-design.md#12-plugins). Dialogues (section 5 below) are planned as the first plugin and will move to `src/agentop/plugins/dialogue/`.

## 2 Agent layer

`agentclis/` is the one place for the differences between the agent tools. Each tool has one module there, with a subclass of `AgentCli`. The rest of agentop asks a tool's `AgentCli` (`get_agent(name)`) and never names a tool itself: a new tool is a new module, and a change in how a tool works is a change to its module only.

### 2.1 AgentCli (`agentclis/base.py`)

The interface, with the default for each thing that differs:

| What | Members |
|------|---------|
| Spotting its process | `matches(proc_name, cmdline)` |
| Being installed | `binary` (the program `launch_cmd` runs), `skills_dir()` (where agentop installs its skills for it, or None) |
| Launching and resuming | `launch_cmd`, `get_resume_cmd()`, `start_session()`, `resume_session()`, `post_start_hook()` |
| Typing into it | `send_text(session, text)` (plain paste, then Enter), `idle_seconds` (how long the screen must stay still before a reply is finished), `boot_seconds` (how long a dialogue waits before its first prompt) |
| Saved sessions | `get_saved_sessions()`, `delete_session()` |
| What it's doing | `get_session_meta()`: session ID, AI title (`get_ai_title()`), token usage, bridge URL |
| One-shot prompts | `ask_cmd(system)`, `ask(prompt, system, cwd)`, `ask_model`: one prompt in, one answer out, no tools and no session saved; the Memory plugin's summaries. Only Claude has it (`claude --print`) |
| Its transcripts | `transcripts_root()` (the tool's own folder), `archive_files(root)` (what the Memory archive copies), `transcript_files(session_id, root)` (one session's files), `conversation_reader(path)` (how to read one as prompts and replies), `searchable(relative)` (whether it goes in the Memory search table) |

### 2.2 The tools

| File | Class | Differences |
|------|-------|-------------|
| `claude.py` | `ClaudeAgentCli` | Session ID by PID from `~/.claude/sessions/`; title and token usage from the session log in `~/.claude/projects/<slug>/`; accepts the trust prompt. Archives everything but Claude Code's `memory/` notes; only `<project>/<session>.jsonl` is searchable, not subagents or tool output |
| `codex.py` | `CodexAgentCli` | Thread by PID from `~/.codex/logs_2.sqlite`, title from `state_5.sqlite`, tokens from the rollout; pre-trusts the folder. Bracketed paste and a settle delay; `boot_seconds = 20`. Archives `sessions/` and `archived_sessions/`, `.jsonl.zst` included |
| `antigravity.py` | `AntigravityAgentCli` | Title from its CLI log; bracketed paste; `idle_seconds = 15`. Transcripts in `~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl`. Conversations from before the rename to Antigravity are only in its binary `conversations/` store, and are not read |

`agentclis/__init__.py` exports `AGENTS`, `get_agent(name)`, `names()` (the CLI's `--tool` choices), `DEFAULT`, the tool used when none is named, and `installed()`, the tools whose program is on the PATH of the user's login shell (where sessions are typed) or of the service.

### 2.3 Getting the host ready (`service/host.py`, `skills/`)

`agentop serve` calls `host.prepare()` before it listens. It finds the installed tools, and stops with an error when there are none. For each one with a `skills_dir()` (only Claude Code for now) it installs or updates the skills that ship in `src/agentop/skills/`: each is copied with a `.agentop` marker file, and a copy with the marker is replaced when the skill changes. A folder or link of the user's own with the same name is left alone and logged; a broken link, such as one to where the repo used to keep the skills, is replaced. The installed tools' names go to `GET /api/v2/tools` (`installed`), and the New session dialog offers only those.

## 3 Actor layer

### 3.1 Actor (`actor.py`)

Generic tmux send/receive. Knows nothing about the dialogue protocol or the tool.

- `send(text)` — types text in with the tool's `AgentCli.send_text`. Codex and agy submit on a bare newline, so theirs use bracketed paste (`Session.paste_text_bracketed()`, ESC[200~…ESC[201~) and multi-line prompts arrive intact
- `receive() -> str | None` — polls the screen until stable for `idle_seconds`, returns full scrollback

### 3.2 AgentInstance (`agent_instance.py`)

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

`service/transcripts.py` lists a session's transcript files, the ones its tool reports (`AgentCli.transcript_files`). When the tool has deleted them it looks in the Memory archive, which keeps each tool's layout, so the same lookup works with the archive as its root. Each tool's `AgentCli.conversation_reader` reads one of those files as prompts and replies, with what every reader shares in `conversation.py`; the File viewer shows it and the Memory plugin's messages table is built from it.

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

The right panel lists the working directory (Files) and the agent tool's own log files for the session (Transcripts, `GET /api/v2/sessions/{id}/transcripts`, from `AgentCli.transcript_files()`). Only files listed there can be read, through the File viewer plugin's route. The viewer draws a Markdown file in the browser (`ui/src/plugins/viewer/Markdown.tsx`, react-markdown with GitHub's extensions, its HTML cleaned by rehype-sanitize's GitHub list), loaded only when one is opened. The images it names come from the plugin's `/image` route as data, since an `<img>` can't carry the token, and a link to a file beside it opens that file in a tab (`viewer/paths.ts`).

`api.py` is a FastAPI app. A background task refreshes `_cache["sessions"]` every 5 seconds by calling `ops.get_sessions()`. All session-list reads are served from cache; mutations (start, stop, send) go directly to `ops`.

The frontend (`html/`) is vanilla JS + xterm.js. The in-browser terminal proxies a `tmux attach-session` over a WebSocket PTY bridge in `api.py`.
