# CLI Reference

All commands are accessed via `agentop <command>`. Run `agentop --help` or `agentop <command> --help` for options.

## 1 Session commands

### 1.1 list

Show all running agent sessions detected on this machine.

```bash
agentop list
agentop list --json
```

Columns: SESSION, TOOL, STATUS, PID, RUNTIME, MEM, TOKENS, CWD

`--json` outputs the full session array. Useful fields:

| Field | Description |
|-------|-------------|
| `name` | Unique session identifier (also the tmux session name) |
| `tool` | `claude` \| `codex` \| `antigravity` |
| `managed` | `true` if started by agentop; only managed sessions can be stopped |
| `pid` | Agent process PID |
| `runtime_seconds` | Seconds since process started |
| `memory_mb` | Resident memory in MB |
| `token_usage` | `{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}` |
| `ai_title` | AI-generated conversation title |
| `session_id` | Internal session ID used to resume later |
| `tmux` | `{session, window, pane}` |
| `bridge_url` | claude.ai remote-control URL (Claude only) |

### 1.2 start

Launch a new agent session in a managed tmux session.

```bash
agentop start <session_name> --tool <tool> [--cwd <path>]
```

- `session_name` — must be under 32 characters; the tmux session is named `{session_name}-{pid}`
- `--tool` — `claude`, `codex`, or `antigravity`
- `--cwd` — working directory (default: home directory)

After starting:

```bash
tmux attach-session -t <session_name>-<pid>
```

### 1.3 stop

Gracefully stop a managed session.

```bash
agentop stop <session>
```

Sends `/exit` to the agent, waits 3 seconds, then kills the tmux session. Only managed sessions (started by agentop) can be stopped.

### 1.4 history

Show saved/historical sessions that can be resumed.

```bash
agentop history
agentop history --tool claude
agentop history --limit 20
agentop history --json
```

Active sessions are excluded. Columns: TOOL, TITLE, LAST ACTIVE, CWD.

## 2 Dialogue commands

All dialogue subcommands are under `agentop dialogue`.

### 2.1 dialogue start

Start a two-agent dialogue on a shared topic.

```bash
agentop dialogue start \
  --brief <path/to/brief.md> \
  [--agent-a <tool>] \
  [--agent-b <tool>] \
  [--cwd-a <path>] \
  [--cwd-b <path>] \
  [--scenario <path/to/scenario.toml>] \
  [--max-turns N]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--brief` | required | Markdown file describing the task/goal |
| `--agent-a` | `claude` | Tool for agent A |
| `--agent-b` | `claude` | Tool for agent B |
| `--cwd-a` | home | Working directory for agent A |
| `--cwd-b` | home | Working directory for agent B |
| `--scenario` | built-in `pm-sde` | Path to a scenario TOML file |
| `--max-turns` | from scenario | Maximum relay turns |

Example:

```bash
agentop dialogue start \
  --agent-a codex \
  --agent-b codex \
  --brief briefs/investment-plan.md \
  --scenario src/agentop/dialogue/scenarios/strategist-challenger.toml
```

### 2.2 dialogue list

List all dialogues and their status.

```bash
agentop dialogue list
```

Columns: ID, STATUS, AGENT A, AGENT B, SESSION A, SESSION B. Sessions marked `[open]` still have a live tmux session.

Status values: `starting`, `running`, `stopped`, `completed`, `error`, `agent_missing_delimiter`

### 2.3 dialogue stop

Stop a running dialogue orchestrator.

```bash
agentop dialogue stop <id>
agentop dialogue stop <id> --close   # also kill both agent tmux sessions
```

### 2.4 dialogue remove

Remove stopped or completed dialogue data from disk.

```bash
agentop dialogue remove <id>
agentop dialogue remove --all        # remove all stopped/completed dialogues
```

Running dialogues are refused. Removable statuses: `stopped`, `completed`, `error`, `agent_missing_delimiter`.
