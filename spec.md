# Local Agent Session Dashboard Spec

## 1. Goal

Build a local dashboard that lists currently running local AI agent sessions, such as Claude Code, Codex, OpenClaw, and other CLI agents.

The dashboard should answer:

* Which agent sessions are currently running?
* What is each session doing?
* Which project/repository is each session attached to?
* What command started it?
* Which `tmux` session, terminal, or process owns it?
* What is its current working directory?
* How long has it been running?
* What is its approximate resource usage?
* Where are its logs, transcripts, or session files?

The first version should prioritize reliability and low complexity over perfect token/cost tracking.

---

## 2. Scope

### In Scope

* Detect running Claude Code and Codex processes.
* Detect related processes by command name or path.
* Read current working directory for each process.
* Detect `tmux` sessions and panes.
* Map processes to `tmux` sessions when possible.
* Read local hidden session directories such as:

  * `~/.claude/`
  * `~/.codex/`
  * `~/.config/claude/`
  * `~/.config/codex/`
* Show recent files modified by each tool.
* Allow manual naming of sessions.
* Allow manual task descriptions.
* Show process-level resource usage.
* Provide a local web dashboard.
* Store metadata in a local registry file.

### Out of Scope for V1

* Perfect Claude/Codex token accounting.
* Cloud-side usage synchronization.
* Automatic semantic understanding of what each agent is doing.
* Editing or controlling Claude/Codex sessions from the dashboard.
* Multi-user authentication.
* Remote deployment.

---

## 3. Target Environment

Primary target:

* Linux desktop or Linux server
* Bash or Zsh
* `tmux` optional but recommended
* Python 3.10+

Secondary target:

* macOS, with minor command differences

Not targeted initially:

* Windows native shell

---

## 4. Core Concepts

### 4.1 Agent Session

An agent session is one running local agent process or one terminal/tmux context running an agent.

Example sessions:

```text
claude-billing-fix
codex-pnl-refactor
claude-openclaw-docs
codex-test-runner
```

Each session has:

* session name
* tool type
* process ID
* command
* working directory
* project/repository
* git branch
* current task
* status
* start time
* CPU usage
* memory usage
* log/session file paths

---

## 5. Detection Sources

The system should combine multiple sources instead of relying on one source.

### 5.1 Process Table

Use process inspection as the primary source of truth for currently running sessions.

Example commands:

```bash
pgrep -af "claude|codex|openclaw"
ps aux | grep -E "claude|codex|openclaw"
```

For each matching process, collect:

* PID
* parent PID
* command line
* start time
* CPU usage
* memory usage
* current working directory

On Linux:

```bash
pwdx <PID>
readlink /proc/<PID>/cwd
cat /proc/<PID>/cmdline
cat /proc/<PID>/stat
cat /proc/<PID>/status
```

### 5.2 Tmux Sessions

If `tmux` is installed, use it to improve naming and terminal context.

Commands:

```bash
tmux list-sessions
tmux list-panes -a -F '#{session_name}|#{window_name}|#{pane_index}|#{pane_pid}|#{pane_current_path}|#{pane_current_command}'
```

Collected fields:

* tmux session name
* tmux window name
* pane index
* pane PID
* pane current path
* pane current command

The dashboard should try to map agent process PID to tmux pane PID by checking process ancestry.

### 5.3 Local Hidden Directories

Inspect known local agent directories.

Candidate paths:

```text
~/.claude/
~/.codex/
~/.config/claude/
~/.config/codex/
~/.local/share/claude/
~/.local/share/codex/
```

The scanner should detect:

* recently modified files
* transcript files
* JSONL files
* SQLite databases
* logs
* config files

Useful commands:

```bash
find ~/.claude ~/.codex ~/.config -iname '*claude*' -o -iname '*codex*'
find ~/.claude ~/.codex -type f -printf '%T@ %p\n' | sort -nr | head -50
```

The dashboard should not assume exact file schema. It should show discovered files and timestamps first.

---

## 6. Manual Session Registry

Because agent CLIs may not expose reliable names or task descriptions, the system should maintain a local registry file.

Default path:

```text
~/.agent-dashboard/sessions.json
```

Example:

```json
{
  "sessions": {
    "claude-billing-fix": {
      "tool": "claude",
      "project": "~/repos/billing-service",
      "task": "Investigate invoice retry bug",
      "status": "running",
      "tmux_session": "claude-billing-fix",
      "tags": ["backend", "bugfix"],
      "notes": "Check retry idempotency and payment provider timeout behavior."
    },
    "codex-pnl-refactor": {
      "tool": "codex",
      "project": "~/repos/trading-system",
      "task": "Refactor PnLTracker fee handling",
      "status": "waiting",
      "tmux_session": "codex-pnl-refactor",
      "tags": ["trading", "python"],
      "notes": "Separate trading PnL and fee PnL."
    }
  }
}
```

Manual registry fields should override auto-detected fields where appropriate.

---

## 7. Naming Strategy

Recommended convention:

```text
<tool>-<project>-<task>
```

Examples:

```text
claude-openclaw-docs
codex-trading-pnl
claude-grafana-backup
codex-lightgbm-features
```

Start sessions with matching `tmux` names:

```bash
tmux new -s claude-openclaw-docs
tmux new -s codex-trading-pnl
```

This makes process/session mapping easier and more reliable.

---

## 8. Dashboard Views

### 8.1 Main Session List

Columns:

| Field      | Description                                      |
| ---------- | ------------------------------------------------ |
| Name       | Manual or inferred session name                  |
| Tool       | Claude, Codex, OpenClaw, other                   |
| Status     | Running, waiting, stopped, unknown               |
| Project    | Current working directory or manual project path |
| Git Branch | Current branch if inside git repo                |
| Task       | Manual task description                          |
| PID        | Main process ID                                  |
| Runtime    | Time since process start                         |
| CPU        | Current CPU usage                                |
| Memory     | Current memory usage                             |
| Tmux       | Associated tmux session/window/pane              |

### 8.2 Session Detail View

For one session, show:

* full command line
* process tree
* current working directory
* git status summary
* recent modified files in project
* recent hidden session files
* tmux metadata
* manually entered notes
* recent log/transcript file links

### 8.3 File Discovery View

Show recent files under known agent directories:

| Tool   | File            | Modified Time | Size | Type   |
| ------ | --------------- | ------------: | ---: | ------ |
| Claude | `~/.claude/...` |           ... |  ... | jsonl  |
| Codex  | `~/.codex/...`  |           ... |  ... | sqlite |

---

## 9. Status Inference

V1 status can be approximate.

Possible values:

* `running`: process exists
* `stopped`: registry entry exists but process is gone
* `unknown`: process exists but cannot be mapped
* `waiting`: manually set
* `done`: manually set

Future improvement:

* detect terminal output activity
* detect recent transcript/log updates
* detect CPU inactivity
* detect blocked input state

---

## 10. Resource Usage

V1 should show OS-level usage only:

* CPU percentage
* RSS memory
* process start time
* elapsed runtime

Implementation options:

* Python `psutil`
* Linux `/proc`
* shell commands: `ps`, `top`, `pgrep`

Recommended: use `psutil` for portability.

---

## 11. Git Integration

If a session working directory is inside a git repository, collect:

```bash
git rev-parse --show-toplevel
git branch --show-current
git status --short
git log -1 --oneline
```

Display:

* repo root
* branch
* dirty/clean status
* latest commit

Do not run expensive git operations in large repositories.

---

## 12. Implementation Architecture

### 12.1 Backend

Recommended stack:

* Python
* FastAPI
* psutil
* optional: SQLite

Responsibilities:

* scan processes
* scan tmux
* scan hidden directories
* read/write manual registry
* expose JSON API
* serve dashboard UI

Example API endpoints:

```text
GET /api/sessions
GET /api/sessions/{name}
POST /api/sessions/{name}
GET /api/files/recent
GET /api/health
```

### 12.2 Frontend

V1 can be simple:

* HTML
* JavaScript
* periodic refresh every 2–5 seconds

No framework required.

Future version:

* React
* filters
* tags
* search
* detail drawer

---

## 13. Data Model

### 13.1 Auto-detected Session Object

```json
{
  "name": "claude-openclaw-docs",
  "tool": "claude",
  "pid": 12345,
  "ppid": 12000,
  "cmdline": "claude --dangerously-skip-permissions",
  "cwd": "/home/user/repos/openclaw",
  "runtime_seconds": 3600,
  "cpu_percent": 2.4,
  "memory_mb": 512,
  "tmux": {
    "session": "claude-openclaw-docs",
    "window": "0",
    "pane": "0"
  },
  "git": {
    "repo_root": "/home/user/repos/openclaw",
    "branch": "main",
    "dirty": true,
    "latest_commit": "abc1234 update docs"
  },
  "recent_files": [
    "/home/user/.claude/projects/.../session.jsonl"
  ]
}
```

### 13.2 Manual Metadata Object

```json
{
  "tool": "claude",
  "project": "~/repos/openclaw",
  "task": "Write local docs search setup",
  "status": "running",
  "tmux_session": "claude-openclaw-docs",
  "tags": ["docs", "agent"],
  "notes": "Focus on local docs indexing and InfluxDB search."
}
```

---

## 14. CLI Helper Commands

Provide helper commands for common operations.

### 14.1 Start Named Session

```bash
agent-session start claude-openclaw-docs --tool claude --project ~/repos/openclaw --task "Write docs search setup"
```

Expected behavior:

* create/update registry entry
* start tmux session
* open shell in project directory

### 14.2 List Sessions

```bash
agent-session list
```

### 14.3 Update Task

```bash
agent-session set-task claude-openclaw-docs "Debug Discord pairing flow"
```

### 14.4 Mark Done

```bash
agent-session done claude-openclaw-docs
```

---

## 15. Security and Privacy

The dashboard should bind to localhost by default:

```text
127.0.0.1:8765
```

Do not expose the dashboard to the internet without authentication.

Sensitive files may exist under:

```text
~/.claude/
~/.codex/
~/.config/
```

The dashboard should avoid displaying full secrets or auth files.

V1 should:

* show file paths and metadata
* avoid rendering full file contents by default
* redact obvious keys/tokens if previews are added later

---

## 16. V1 Milestones

### Milestone 1: Process Scanner

* Detect Claude/Codex processes.
* Show PID, command, cwd, runtime, CPU, memory.

### Milestone 2: Tmux Scanner

* List tmux sessions and panes.
* Map agent processes to tmux sessions.

### Milestone 3: Manual Registry

* Add `~/.agent-dashboard/sessions.json`.
* Support manual task names and notes.

### Milestone 4: Local Web UI

* Display table of sessions.
* Auto-refresh every few seconds.

### Milestone 5: File Discovery

* Scan `~/.claude`, `~/.codex`, and config directories.
* Show recent session/log/transcript files.

---

## 17. Recommended V1 Implementation Order

1. Write Python scanner using `psutil`.
2. Add tmux pane scanner using `tmux list-panes`.
3. Add simple JSON registry.
4. Expose `/api/sessions` with FastAPI.
5. Add minimal HTML dashboard.
6. Add hidden directory file discovery.
7. Add helper CLI for starting named sessions.

---

## 18. Example Minimal UI

Main page:

```text
Local Agent Dashboard

[Refresh]

Name                    Tool     Status    Project              Runtime   CPU   Mem    Task
claude-openclaw-docs    Claude   running   ~/repos/openclaw     02:14:33  1.2%  430MB  Write local docs search setup
codex-trading-pnl       Codex    running   ~/repos/trading      00:41:12  4.8%  710MB  Refactor PnLTracker fee handling
```

---

## 19. Future Enhancements

* WebSocket live updates.
* Terminal activity detection.
* Token/cost extraction if supported by each tool.
* Claude Code statusline integration.
* Codex session metadata integration if available.
* Per-session transcript viewer.
* Per-session TODO extraction.
* Browser notifications for stopped or idle sessions.
* Integration with Grafana/InfluxDB.
* Local LLM summarization of each session's latest activity.
* Automatic session naming from first prompt.

---

## 20. Design Decision

Use `tmux` plus process inspection as the stable foundation.

Do not depend on private Claude/Codex internal file schemas for core functionality. Treat hidden directories as optional enrichment only.

This keeps the dashboard robust even if Claude Code or Codex changes their internal storage format.
