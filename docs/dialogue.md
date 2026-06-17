# Dialogue System

## 1 Overview

The dialogue system orchestrates two agent CLI processes collaborating on a shared task. The orchestrator relays messages between them, enforcing a structured protocol so it can extract and forward only the agent's intended output.

Dialogue data is persisted to `~/.agent-dashboard/dialogues/<scenario>_<brief>_<id>/`.

## 2 How it works

1. `agentop dialogue start` launches two agent tmux sessions and a background orchestrator process.
2. The orchestrator sends turn prompts to agent A, waits for a response, extracts the message body, and forwards it to agent B — then alternates.
3. Agents signal completion by setting `status:complete` in their delimiter block.
4. The orchestrator stops when it sees a complete signal, reaches `max_turns`, or is manually stopped.

## 3 Protocol

### 3.1 Delimiter format

Every agent response must be wrapped in `BEGIN`/`END` delimiter blocks at the end of the output:

```
--- BEGIN {name} turn:{turn} nonce:{nonce} status:continue ---
message body here
--- END {name} turn:{turn} nonce:{nonce} status:continue ---
```

To signal completion:

```
--- BEGIN {name} turn:{turn} nonce:{nonce} status:complete ---
final summary here
--- END {name} turn:{turn} nonce:{nonce} status:complete ---
```

- `name` — the agent's role name from the scenario (e.g. `PM`, `Strategist`)
- `turn` — monotonically increasing turn number
- `nonce` — 4-byte hex value unique to each turn, prevents stale matches
- Everything outside the delimiters is the agent's private workspace and is not forwarded

### 3.2 Retry on missing delimiter

If no valid delimiter block is found, the orchestrator sends a recovery prompt (up to 2 retries) before halting with status `agent_missing_delimiter`.

### 3.3 Parser

`dialogue/parser.py` scans scrollback bottom-up using `in` matching (not `startswith`) so a leading `●` prefix from Claude Code's renderer does not break detection.

## 4 Scenarios

A scenario TOML file defines the role prompts for both agents. Built-in scenarios are in `src/agentop/dialogue/scenarios/`.

### 4.1 TOML format

```toml
name = "my-scenario"

[settings]
max_turns = 40

[role_a]
name = "Strategist"
prompt = """
Your name is {name_a}. You are collaborating with {name_b} on: {brief}
...
"""

[role_b]
name = "Challenger"
prompt = """
Your name is {name_b}. You are working with {name_a}.
...
"""

[opening]
prompt = """
You are collaborating on the following project:
{brief}

Shared progress file: {progress_file}
Begin now.
"""
```

### 4.2 Template variables

| Variable | Value |
|----------|-------|
| `{name_a}` | Role name for agent A |
| `{name_b}` | Role name for agent B |
| `{brief}` | Contents of the `--brief` file |
| `{progress_file}` | Path to `~/.agent-dashboard/dialogues/<scenario>_<brief>_<id>/progress.md` |
| `{topic}` | Alias for `{brief}` (legacy compat) |

### 4.3 Built-in scenarios

| File | Roles | Purpose |
|------|-------|---------|
| `pm-sde.toml` | PM / SDE | Product manager drives, engineer implements |
| `strategist-challenger.toml` | Strategist / Challenger | One proposes, one stress-tests |
| `architect-implementer.toml` | Architect / Implementer | Design then build |
| `builder-reviewer.toml` | Builder / Reviewer | Build then review |
| `researcher-skeptic.toml` | Researcher / Skeptic | Research under scrutiny |
| `bull-bear-judge.toml` | Bull / Bear / Judge | Investment thesis debate |
| `red-team-blue-team.toml` | Red / Blue | Adversarial security review |
| `junior-senior.toml` | Junior / Senior | Mentored implementation |
| `planner-executor.toml` | Planner / Executor | Plan then execute |
| `productive-pessimistic.toml` | Productive / Pessimistic | Optimist vs. skeptic |
| `software-team.toml` | Team roles | General software team |
| `trading-researcher-reviewer.toml` | Researcher / Reviewer | Trading strategy research |

## 5 Persisted files

Each dialogue gets a directory at `~/.agent-dashboard/dialogues/<scenario>_<brief>_<id>/` (the folder is named from the scenario name and brief filename; `<id>` is an 8-char unique token):

| File | Contents |
|------|----------|
| `meta.json` | ID, agent names, tmux sessions, status, PID |
| `brief.md` | Copy of the `--brief` file |
| `scenario.toml` | Copy of the scenario file used |
| `dialogue.log` | Orchestrator logs (idle detection, parse results, status changes) |
| `progress.md` | Shared progress file the agents read and update |

## 6 Adding agents to a dialogue

Any supported tool (`claude`, `codex`, `antigravity`) can be assigned to either role. Mixed-tool dialogues are supported:

```bash
agentop dialogue start \
  --agent-a claude \
  --agent-b codex \
  --brief briefs/my-task.md
```

## 7 Bracketed paste

Codex and Antigravity submit on bare newlines, so their actors (`actors/codex.py`, `actors/antigravity.py`) wrap all sent text in bracketed paste sequences (ESC[200~…ESC[201~). Claude Code buffers multi-line input natively and uses plain paste.

This is handled transparently — `get_actor(agent.name)` returns the right `Actor` subclass.
