# agentop — System Specification

## 1. Concept

**agentop** is a local dashboard for monitoring and managing AI agent CLI sessions running on the same machine.

### The core problem

When running multiple AI agent sessions in parallel (Claude Code, Codex, Gemini, etc.), it becomes hard to track which agent is working on what. You need to know at a glance:

- Which agents are currently running?
- What project and task is each one working on?
- How long has it been running and how much has it consumed?
- How do I jump into its terminal?

### The approach

Each agent session runs inside a **tmux** window. tmux provides a stable, persistent terminal that:
- Survives disconnects
- Can be attached to at any time with `tmux attach -t <session-name>`
- Provides a reliable mechanism for sending input programmatically

agentop wraps tmux session management and pairs it with a local web dashboard (and a CLI) that gives a unified view of all running agents.

### What you can do

The following operations are available from **both the CLI and the web UI**:

| Operation | CLI | UI |
|---|---|---|
| List all sessions | `agentop list` | Sessions tab |
| Start a new session | `agentop start --tool <tool> --cwd <dir>` | New Session button |
| Stop a session | `agentop stop <name>` | Stop button in detail panel |
| Set session description | `agentop set-description <name> <text>` | Edit in detail panel |
| Send a prompt | `agentop send <name> <text>` | Send button in detail panel |

### Supported agents

| Agent | Binary | Detection |
|---|---|---|
| Claude Code | `claude` | Process name or first cmdline token is `claude` |
| Codex | `codex` | Process name or first cmdline token is `codex` |
| Gemini CLI | `gemini` | Any cmdline token basename is `gemini` or `gemini-*` (handles `node /path/bin/gemini`) |

Adding a new agent requires subclassing `BaseAgent` and appending an instance to `AGENTS`.

---

## 2. Implementation

### 2.1 Agent abstraction (`dashboard/agents.py`)

All agent-specific logic lives in `BaseAgent` subclasses. The base class provides the shared tmux lifecycle and a default `matches()` implementation. Subclasses override only what differs.

**Interface:**

```python
class BaseAgent:
    name: str                          # "claude", "codex", "gemini", …

    def matches(proc_name, cmdline) -> bool      # process detection
    def launch_cmd -> str                         # shell command to run inside tmux
    def post_start_hook(tmux_session)             # first-run setup (e.g. trust prompt)
    def get_ai_title(pid, cwd) -> Optional[str]  # read AI-generated title from disk
    def get_extra_meta(pid, cwd) -> dict          # tool-specific fields (token usage, etc.)
    def get_session_meta(pid, cwd) -> dict        # merges get_ai_title + get_extra_meta
    def start_session(cwd) -> dict                # full tmux lifecycle (see below)
```

`get_session_meta()` is implemented once in `BaseAgent` and calls `get_ai_title()` + `get_extra_meta()`, so subclasses only override the two leaf methods.

The `AGENTS` list is ordered: more specific matchers first. `get_agent(name)` looks up by `name`.

### 2.2 Session naming and tmux session names

**Managed session** — started by agentop via `start_session()`:

1. A temporary tmux session is created: `agentop_{tool}_tmp_{rand6}`.
2. The agent is launched inside it via `tmux send-keys`.
3. agentop polls (up to 5 s) for the agent child process to appear using `matches()`.
4. Once the PID is found, the tmux session is renamed to `agentop_{tool}_{pid}`.
5. `post_start_hook()` is called (e.g. Claude auto-accepts the trust prompt).

The session name encodes both the tool and the PID, which makes it self-describing and avoids collisions. If the PID is never found, the `tmp` name is kept.

**Unmanaged session** — agent was started externally (not via agentop):

- Name is derived from the tmux session name if the process is inside one and the session name contains the tool name.
- Otherwise, name falls back to `{tool}-{pid}`.

A session is **managed** if its tmux session name starts with `agentop_`. Managed sessions get additional controls in the detail panel (stop, send prompt, edit description).

### 2.3 Description

Descriptions are stored in `~/.agent-dashboard/sessions.json` (the registry), keyed by session name. They survive restarts.

`ops.set_description(name, text)` calls `registry.upsert_session(name, {"description": text})`.

When sessions are built, registry descriptions are merged in by matching session name.

The UI shows description in the Description column. In the detail panel, managed sessions have an editable description field.

### 2.4 AI-generated title (`get_ai_title`)

Each agent reads from its own storage format to surface the AI-generated conversation title. This is shown in the Description column when no manual description has been set.

**Claude:**
- `~/.claude/sessions/{pid}.json` → `sessionId`
- `~/.claude/projects/{cwd_slug}/{session_id}.jsonl` → scan for `{"type": "ai-title", "aiTitle": "…"}`; use the last occurrence.
- `cwd_slug` = cwd with `/` replaced by `-`.

**Codex:**
- Scan `~/.codex/sessions/**/rollout-*.jsonl` sorted by mtime (newest first, up to 30).
- First line of each file is a `session_meta` object with `payload.cwd` and `payload.id`.
- Find the file whose `cwd` matches the session's cwd → extract `session_id`.
- Primary: scan `~/.codex/session_index.jsonl` for a line with matching `id` → return `thread_name`. This file is only written when a session ends, so it is empty for live sessions.
- Fallback: scan `~/.codex/history.jsonl` (written in real time) for the first entry with matching `session_id` → use its `text` field (truncated to 80 chars) as a provisional title.

**Gemini:**
- Does not persist conversation titles to disk. Returns `None`.

### 2.5 Token usage (`get_extra_meta` → `token_usage`)

All agents return a `token_usage` dict in the same shape:

```json
{
  "input_tokens": 0,
  "output_tokens": 0,
  "cache_read_input_tokens": 0,
  "cache_creation_input_tokens": 0
}
```

This is stored at the top level of the session dict as `session["token_usage"]`.

**Claude:**
- Locate the session's JSONL file via `~/.claude/sessions/{pid}.json` → `sessionId` → project JSONL.
- Scan all lines; for each `{"type": "assistant"}` message, extract `message.usage`.
- Deduplicate by `message.id` (same message may be repeated in the JSONL on retries).
- Sum `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.

**Codex:**
- Locate rollout file via `_find_rollout_file(cwd)` (see §2.4).
- Scan for `event_msg` lines where `payload.type == "token_count"`.
- Use the **last** occurrence's `payload.info.total_token_usage` (Codex keeps a running total).
- Map: `cached_input_tokens` → `cache_read_input_tokens`; `cache_creation_input_tokens` = 0.

**Gemini:**
- Locate project dir: scan `~/.gemini/tmp/*/` for a `.project_root` file matching the cwd.
- List `chats/session-*.json` and `chats/session-*.jsonl` sorted by mtime, newest first.
- For each file (newest first), parse messages and sum `tokens` fields. Use the first file that has non-zero totals (skips empty new-session files).
- Two file formats:
  - `.json`: full JSON object with `messages` array; each message may have `tokens`.
  - `.jsonl`: first line is a header; subsequent lines are message objects.
- Map: `tokens.input` → `input_tokens`; `tokens.output + tokens.thoughts + tokens.tool` → `output_tokens`; `tokens.cached` → `cache_read_input_tokens`.

### 2.6 Claude remote-control bridge (`get_extra_meta`)

When Claude is started with `--remote-control`, it writes a `bridgeSessionId` to `~/.claude/sessions/{pid}.json`. agentop reads this and surfaces:

- `bridge_session_id`
- `bridge_url`: `https://claude.ai/code/{bridge_session_id}`
- `remote_name`: `{hostname}-{first8chars}` — human-friendly identifier
- `claude_status`: current status string from the session file

### 2.7 Process scanning (`dashboard/scanner.py`)

`scan_processes()` iterates all processes via `psutil`. For each process, it calls `_detect_tool(name, cmdline)` which tries each agent's `matches()` in `AGENTS` order. The first match wins.

Processes are filtered if their cmdline contains any entry from `IGNORE_CMDLINE_PATTERNS` (e.g. `grep`, `agentop`, `uvicorn`).

`scan_tmux()` runs `tmux list-panes -a` and parses the output.

`map_process_to_tmux(pid, panes)` collects all ancestor and child PIDs of the process and finds the pane whose `pane_pid` is in that set.

`build_sessions(descriptions)` combines process scan + tmux scan + per-agent metadata. It deduplicates by session name (keeping the lowest PID — relevant for tools like Gemini that spawn a parent + child node process that both match).

### 2.8 Backend (`dashboard/main.py` + `dashboard/ops.py`)

FastAPI backend. A background async loop (`_refresh_loop`) calls `ops.get_sessions()` and `scanner.scan_agent_dirs()` every 5 seconds and caches the results.

**API endpoints:**

```
GET  /api/health                    — last_updated timestamp
GET  /api/sessions                  — cached session list
POST /api/sessions/start            — start new session {tool, cwd, description}
GET  /api/sessions/{name}           — single session with process_tree + recent_project_files
POST /api/sessions/{name}           — update description
POST /api/sessions/{name}/stop      — stop managed session
POST /api/sessions/{name}/send      — send text to managed session via tmux
GET  /api/files/recent              — recent agent dir files
GET  /api/info                      — server info (home dir)
```

`ops.py` is the shared business logic layer used by both the web API and the CLI. It calls into `scanner` and `registry`.

**Registry** (`dashboard/registry.py`): reads/writes `~/.agent-dashboard/sessions.json`. `upsert_session(name, fields)` merges fields into the existing entry.

### 2.9 Agent file directories scanned

```
~/.claude/
~/.codex/
~/.gemini/
~/.config/claude/
~/.config/codex/
~/.config/gemini/
~/.local/share/claude/
~/.local/share/codex/
```

File type is inferred from extension: `.jsonl`, `.json`, `.db`/`.sqlite`/`.sqlite3`, `.log`, or the raw extension otherwise.

---

## 3. Sessions List (UI)

### 3.1 Columns

| Column | Source | Notes |
|---|---|---|
| Name | tmux session name or `{tool}-{pid}` | Managed sessions have `MANAGED` badge |
| Tool | Detected agent type | Color-coded badge |
| Status | `RUNNING` / `BUSY` | BUSY shown when Claude reports it via bridge |
| Description | Registry description, or AI title if no description set | Editable inline for managed sessions |
| CWD | Process cwd | Home dir abbreviated to `~` |
| Tokens | `token_usage` sum | Formatted with K/M suffix |
| PID | Process ID | |
| Runtime | Time since process start | |
| Mem | RSS memory | |
| Tmux | tmux session name | |

### 3.2 Sorting

All columns are sortable by clicking the header. Click again to toggle asc/desc. An arrow indicator shows the active sort column and direction. Default sort: **Runtime descending**.

### 3.3 Copy tmux attach

Each row has a button (visible on hover or always visible) that copies `tmux attach -t {session}` to the clipboard, so you can jump into the agent's terminal in one click.

### 3.4 Managed vs unmanaged sessions

- **Managed**: tmux session name starts with `agentop_`. Started by agentop. Show `MANAGED` badge, get Stop and Send controls in the detail panel.
- **Unmanaged**: agent was started externally. Shown in the list but no stop/send controls.

---

## 4. Detail Panel (UI)

Clicking a session row opens a side panel. All sessions show:

- **Overview**: tool, status, PID, runtime, memory, CWD, tmux session, full cmdline.
- **Token Usage**: total, output, input, cache read, cache write (shown only if `token_usage` is populated).
- **Git**: branch, repo root, latest commit (shown only if the cwd is inside a git repo).
- **Remote** (Claude only): bridge URL, remote name (shown only if `--remote-control` is active).
- **Process Tree**: ancestry chain from root to the agent process.
- **Recent Project Files**: latest modified files in the cwd (up to 15).

**Managed sessions additionally show:**

- **Description** field — editable, saved to registry on change.
- **Send Prompt** — text input + send button, dispatches via `tmux send-keys`.
- **Stop** button — sends `/exit` to the tmux session, then kills the tmux session after a 3-second grace period.

---

## 5. Starting New Sessions (UI)

The **+ New Session** button opens a modal with:

- **Agent Tool** selector (Claude, Codex, Gemini).
- **Working Directory** text input.
- **Description** text input (optional).

On submit, calls `POST /api/sessions/start`. The backend calls `ops.start(tool, cwd, description)` which:
1. Calls the agent's `start_session(cwd)` — creates tmux session, launches agent, waits for PID, renames session.
2. If a description was provided, saves it to the registry under the new session name.

The UI refreshes after a short delay to pick up the new session.

---

## 6. Agent Files Tab (UI)

A secondary tab showing recently modified files under all agent directories.

Columns: Tool, Type, File (path abbreviated), Size, Modified.

All columns are sortable; default sort is **Modified descending**.

The file list is refreshed from `GET /api/files/recent` when the tab is switched to, and on manual refresh. Up to 60 files are shown. File type is shown as a monospace label (`jsonl`, `json`, `sqlite`, `log`, etc.).

Tool labels follow the same color-coded badges as the Sessions tab.

---

## 7. CLI (`agentop`)

The CLI is a thin wrapper over `ops.py` providing the same operations as the web UI.

```
agentop list                                        — table of all sessions
agentop start --tool <tool> --cwd <dir>              — start new managed session
agentop stop <name>                                 — stop managed session
agentop set-description <name> <text>               — update description
agentop send <name> <text>                          — send text to session
```

---

## 8. Security

The dashboard binds to `127.0.0.1:8765` only. Do not expose it to the internet without adding authentication. Agent directories may contain auth tokens and conversation content — the dashboard shows file paths and metadata only, never renders raw file contents.

---

## 9. Adding a New Agent

1. Subclass `BaseAgent` in `dashboard/agents.py`.
2. Set `name` (lowercase, used in tmux session names, detection, and badges).
3. Override `matches()` if the default pattern (`proc_name == name`) is insufficient (e.g. Gemini runs as `node`).
4. Override `launch_cmd` if the command differs from `name`.
5. Override `post_start_hook()` for any first-run interaction needed.
6. Override `get_ai_title()` to read the conversation title from disk.
7. Override `get_extra_meta()` to return `{"token_usage": {...}}` and any other fields.
8. Append an instance to `AGENTS` (more specific matchers go first).
9. Add a color style for `.tool-{name}` in `index.html`.
10. Add `~/.{name}` to `AGENT_DIRS` in `scanner.py` if the agent writes files there.
