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
| `managed` | `true` if started by agentop; only managed sessions can be stopped or sent prompts |
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

- `session_name` — must be under 32 characters; the tmux session is named `{session_name}-{random}`, and keeps that name for life
- `--tool` — `claude`, `codex`, or `antigravity`
- `--cwd` — working directory (default: home directory)

After starting:

```bash
tmux attach-session -t <session_name>-<random>
```

### 1.3 stop

Gracefully stop a managed session.

```bash
agentop stop <session>
```

Sends `/exit` to the agent, waits 3 seconds, then kills the tmux session. Only managed sessions (started by agentop) can be stopped.

### 1.4 send

Type a prompt into another live session and submit it, as if the user had typed it there.

```bash
agentop send <target> "<prompt>"
agentop send <target> < prompt.txt     # no prompt: read it from stdin
```

- `target` — a session name from `agentop list`, ignoring case and the `-<suffix>`: `eiger` finds `Eiger-2953204`. With no match it says `I don't see an active session called 'eiger'`, and with several it lists them. It never starts a session
- Run from inside tmux, the prompt gets a first line `[agentop send from <this session>]`; `--no-header` leaves it off
- Only managed sessions (started by agentop) take prompts
- Nothing comes back by itself: switch to the target to follow it and answer it. A prompt can ask the target to reply with its own `agentop send`, which arrives as a new prompt in the sender

The `handoff` skill uses it to pass a side task, such as a deploy, from one session to another. When the sender needs a result, such as a build version, the prompt asks for a one-line reply.

### 1.5 history

Show saved/historical sessions that can be resumed.

```bash
agentop history
agentop history --tool claude
agentop history --limit 20
agentop history --json
```

Active sessions are excluded. Columns: TOOL, TITLE, LAST ACTIVE, CWD.

### 1.6 recall

Search every past conversation on this machine: the prompts and replies the Memory plugin keeps ([design 12.11](v2-design.md#1211-memory-agentops-own-record-of-conversations)). It reads `~/.agentop/plugins/memory/messages.sqlite` directly, so the service needn't be running. The command is the Memory plugin's: with the plugin disabled, it says so and does nothing.

```bash
agentop recall atama link                      # messages holding every word
agentop recall "atama plus" --since 6w         # a phrase; since 12h, 30d, 6w, or a date
agentop recall stores.jp --tool codex --project ~/workspace/Hotaka
agentop recall --session <session>             # its summary, then the whole session
agentop recall --session <session> --around <#id> [--radius 6]   # read around a hit
agentop recall <words> --json                  # {"sessions": [...], "messages": [...]}
```

Words match anywhere in a message, ignoring case (an FTS5 trigram index), so part of a name, a domain, or Japanese text works. Words under three characters are matched as plain text. Results are best match first, each with its `#id`, date, tool, project, session, and, for a reply, the prompt it answered.

Above the messages, **Sessions about this** lists up to five sessions whose summary matches: its title, a few sentences, and the keywords that matched. The summaries are the Memory plugin's (design 12.11.3, phase 5), so they find a session by what it was about even when no message has the words. A session gets one once it has been quiet for 30 minutes. The `recall` skill (`src/agentop/skills/recall/`) teaches a Claude session to use it.

## 2 Dialogue commands

All dialogue subcommands are under `agentop dialogue`. The command comes from the Dialogues plugin: while the plugin is off, `agentop dialogue` says so and does nothing.

### 2.1 dialogue start

Start a two-agent dialogue on a shared topic.

```bash
agentop dialogue start \
  --brief <path/to/brief.md> \
  [--agent-a <tool>] \
  [--agent-b <tool>] \
  [--scenario <path/to/scenario.toml>] \
  [--max-turns N]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--brief` | required | Markdown file describing the task/goal |
| `--agent-a` | `claude` | Tool for agent A |
| `--agent-b` | `claude` | Tool for agent B |
| `--scenario` | built-in `pm-sde` | Path to a scenario TOML file |
| `--max-turns` | from scenario | Maximum relay turns |

Both agents always run with cwd set to the dialogue's own directory at
`~/.agentop/plugins/dialogue/dialogues/<id>/` — there is no way to override this. Any
`deliverables/` folder a scenario asks the agents to create ends up there,
alongside `brief.md`, `scenario.toml`, and `dialogue.log`. Agents can still
read or write files elsewhere by using absolute paths.

Example:

```bash
agentop dialogue start \
  --agent-a codex \
  --agent-b codex \
  --brief briefs/investment-plan.md \
  --scenario src/agentop/plugins/dialogue/scenarios/strategist-challenger.toml
```

### 2.2 dialogue list

List all dialogues and their status.

```bash
agentop dialogue list
```

Columns: ID, STATUS, AGENT A, AGENT B, SESSION A, SESSION B. Sessions marked `[open]` still have a live tmux session.

Status values: `starting`, `running`, `stopped`, `completed`, `error`, `agent_missing_delimiter`

### 2.3 dialogue stop

Stop a running dialogue orchestrator and close both agent tmux sessions.
Everything said so far is already in `dialogue.log`, so the sessions are
safe to drop — the same cleanup happens automatically when a dialogue
completes on its own.

```bash
agentop dialogue stop <id>
```

### 2.4 dialogue remove

Remove dialogue data from disk.

```bash
# Remove all stopped dialogues (default, with confirmation)
agentop dialogue remove

# Remove all completed dialogues
agentop dialogue remove --status completed

# Remove a specific dialogue by ID (any status, no confirmation)
agentop dialogue remove --id <id>

# Skip confirmation prompt
agentop dialogue remove --status stopped --yes
```

`--status` bulk removal only accepts `stopped` or `completed`. Any other status requires `--id`.

If the dialogue has open tmux sessions (e.g. status is `running`), they are stopped and closed automatically before the directory is deleted.

## 3 Service commands

```bash
agentop serve [--host H] [--port P] [--name N] [--tls-cert F --tls-key F]   # foreground, http://127.0.0.1:9775
                                      # a non-loopback --host needs --tls-cert and --tls-key
agentop open                          # open the app in a browser, signed in
agentop service status                # whether the service runs, and how
agentop service restart               # or stop; sessions keep running
agentop service update                # pull, install, build, restart; goes back if that fails
agentop service token [--rotate [--revoke-clients]]   # print, or replace, the service token
agentop service clients [--revoke ID | --revoke-all]  # list the clients signed in, or sign them out
agentop service connect-info [--host H | --url URL]   # what to paste into Connect to a service
```

The service serves the app at `/` and the API at `/api/v2`. Settings in `~/.agentop/config.toml` (`host`, `port`, `name`, `tls_cert`, `tls_key`) are the defaults for `serve`. `client_ttl` (default `"12h"`) is how long a client stays signed in without renewing, at least `60s`, and `client_max_age` (default `"30d"`, `"none"` for no limit) is how long it stays signed in at most. Both take seconds, or `30m`, `12h`, `30d`.

The service token only signs a client in: the app, or each CLI command, gets its own token with it, which expires (design D7). Rotating the service token leaves signed-in clients alone; add `--revoke-clients` to sign them out too. `service clients` works on `~/.agentop/state/clients.json` directly, so it works while the service is stopped, and a running service notices at once. Running it as a system service or behind a proxy is in [v2-setup.md](v2-setup.md).
