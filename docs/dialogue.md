# Dialogue System

## 1 Overview

The Dialogues plugin (`src/agentop/plugins/dialogue/`, UI in `ui/src/plugins/dialogue/`) orchestrates two agent CLI processes collaborating on a shared task. The orchestrator relays messages between them, enforcing a structured protocol so it can extract and forward only the agent's intended output.

Dialogue data is persisted to `~/.agentop/plugins/dialogue/dialogues/<scenario>_<brief>_<id>/`.

The app has a Dialogues workspace (section 8): a wizard starts one, and each one's two agents are shown side by side, live while it runs and as transcripts once it ends. Turning the plugin off in Status → Plugins (Status on the service switcher) stops any running dialogue (after a warning), keeps every saved one, and hides the view, the labels, and the `/api/v2/plugins/dialogue/` routes; `agentop dialogue` then says the plugin is off. The dialogue's agent sessions stay as ordinary sessions.

## 2 How it works

1. The app's New dialogue wizard or `agentop dialogue start` makes the dialogue's folder and brief, then launches two agent tmux sessions and a background orchestrator process. The agents are the plugin's own sessions (`owner: "dialogue"`): the session list leaves them out, their terminals are read-only, and their windows are kept at 80×40 whoever watches them, since the orchestrator reads replies off the screen.
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

`plugins/dialogue/parser.py` scans scrollback bottom-up using `in` matching (not `startswith`) so a leading `●` prefix from Claude Code's renderer does not break detection.

## 4 Scenarios

A scenario TOML file defines the role prompts for both agents. Built-in scenarios are in `src/agentop/plugins/dialogue/scenarios/`.

### 4.0 Scenario vs Brief Policy

Keep scenarios generic and deliberate. Put topic-specific substance in the
brief.

Use this rule of thumb:

- If the difference is **what should the agents review, research, decide, or
  produce**, write a new brief.
- If the difference is **how the agents should collaborate, argue, route turns,
  or decide completion**, write a new scenario.
- If the dialogue needs a different number of roles, routing pattern,
  completion protocol, authority structure, or review loop, write a new
  scenario.

The scenario owns interaction mechanics: role relationship, turn-taking,
challenge pattern, completion markers, and shared workflow expectations.

The brief owns the domain problem: topic, sources, evidence rules, constraints,
deliverables, acceptance criteria, and any project-specific decision policy.

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

### 4.1a The brief's template (`summary`, `description`)

A scenario can carry a one-line `summary` and a `description`: the template of its brief, in Markdown, with `{field}` placeholders. The New dialogue wizard shows the description, asks for every field (all are required), and saves the filled-in description as `brief.md`. The `title` field names the dialogue and its folder. `{name_a}` and `{name_b}` are filled in with the role names; `{brief}`, `{topic}` and `{progress_file}` are reserved for the role prompts and never asked for. A scenario without a description asks for a `title` and the whole brief (`brief_text`).

```toml
name = "my-scenario"
summary = "Strategist proposes, Challenger stress-tests."
description = """
# {title}

## Goal
{goal}

## Deliverables
{deliverables}
"""
```

The four scenarios at the top of `scenarios/` have descriptions; the ones under `shelf/` use the default.

### 4.2 Template variables

| Variable | Value |
|----------|-------|
| `{name_a}` | Role name for agent A |
| `{name_b}` | Role name for agent B |
| `{brief}` | Contents of the `--brief` file |
| `{progress_file}` | Path to `~/.agentop/plugins/dialogue/dialogues/<scenario>_<brief>_<id>/progress.md` |
| `{topic}` | Alias for `{brief}` (legacy compat) |

### 4.3 Built-in scenarios

In `scenarios/`, each with a brief template (4.1a); `scenarios/README.md` describes them:

| File | Roles | Purpose |
|------|-------|---------|
| `product-direction.toml` | ProductStrategist / ProductCritic | Product direction, scope and non-goals |
| `implementation-requirements.toml` *(default)* | ProductOwner / TechnicalArchitect | Turn a direction into implementation requirements |
| `implementation-handoff.toml` | ImplementationLead / CodeReviewer | A handoff for a single code agent, audited |
| `explorer-critic.toml` | Explorer / Critic | Research from the brief's sources, verified by reading them again |

Older or domain-specific ones, in `scenarios/shelf/`:

| File | Roles | Purpose |
|------|-------|---------|
| `pm-sde.toml` | PM / SDE | Product manager drives, engineer implements |
| `strategist-challenger.toml` | Strategist / Challenger | One proposes, one stress-tests |
| `strategy-discovery.toml` | StrategyExplorer / WorkflowCritic | Strategy discovery |
| `investment-product-strategy.toml` | ProductStrategist / InvestmentSkeptic | Investment product strategy |
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

Each dialogue gets a directory at `~/.agentop/plugins/dialogue/dialogues/<scenario>_<brief>_<id>/` (the folder is named from the scenario name and brief filename; `<id>` is an 8-char unique token):

| File | Contents |
|------|----------|
| `meta.json` | ID, agent names, tmux sessions, status, PID, start and end time |
| `brief.md` | Copy of the `--brief` file |
| `scenario.toml` | Copy of the scenario file used |
| `turns.jsonl` | One line per message an agent sent: `turn`, `side` (`a` or `b`), `name`, `status` (`continue` or `complete`), `body`, `at`. The transcript the app shows. Dialogues from before it was kept (2026-11-15) are read from `dialogue.log` instead |
| `dialogue.log` | Orchestrator logs: idle detection, status changes, and each turn's parsed body length plus the full message body |
| `progress.md` | Shared progress file the agents read and update |
| `deliverables/` | Created by the agents if the scenario/brief asks for it — both agents share this directory as their cwd |

## 6 Adding agents to a dialogue

Any supported tool (`claude`, `codex`, `antigravity`) can be assigned to either role. Mixed-tool dialogues are supported:

```bash
agentop dialogue start \
  --agent-a claude \
  --agent-b codex \
  --brief briefs/my-task.md
```

## 7 Bracketed paste

Every tool's `send_text` (in `agentclis/`) sends text as one bracketed paste (ESC[200~…ESC[201~), so a line break stays in the prompt. A plain tmux paste turns each line break into Enter: Codex and Antigravity submit on it, and so does Claude Code when the paste is short (a long one it buffers as a paste anyway). Claude moved to bracketed paste on 2026-11-17, after short two-line prompts from the Input bar arrived as two prompts.

This is handled transparently: the `Actor` types with its tool's `AgentCli.send_text`.

## 8 In the app

The Dialogues workspace (`ui/src/plugins/dialogue/`):

- **New dialogue** (in the header, and on the placard shown while no tab is open) opens the wizard in a tab: pick a scenario, fill in its brief's fields, pick each agent's tool and the maximum turns. Starting returns at once; the agents boot in the background, and the dialogue shows as Starting until its orchestrator runs.
- **The left panel** is only the list: dialogues In progress and Finished. Picking one shows agent A in the left tab group and agent B in the right one, replacing the dialogue shown before.
- **An agent's tab** shows its tmux session live and read-only while the dialogue runs (the service attaches with `tmux attach -f read-only,ignore-size` and drops anything typed, and the terminal scales its font to show the whole window). Once the dialogue has ended it shows the agent's messages from `turns.jsonl`, drawn as Markdown.
- **The right panel** shows the dialogue's status, scenario, agents, times and folder; its stats (turns, messages and words per agent, how long it took, what ended it, format retries); its artifacts (the files under `deliverables/`) and its brief and progress, each opening in a tab; and Stop and Remove. Remove asks first, then stops the agents if they run and deletes the folder.

Routes, under `/api/v2/plugins/dialogue/`:

| Method | Path | What |
|--------|------|------|
| `GET` | `/scenarios` | The built-in scenarios, with their brief template and fields |
| `POST` | `/dialogues` | `{scenario, values, agent_a, agent_b, max_turns}`: check every field, make the folder and brief, start launching. 202 with `{id}` |
| `GET` | `/dialogues`, `/dialogues/{id}` | The list; one dialogue with its brief, progress, log tail and stats |
| `GET` | `/dialogues/{id}/turns` | The messages, in order |
| `GET` | `/dialogues/{id}/files`, `/dialogues/{id}/file?path=` | `brief.md`, `progress.md` and `deliverables/`; one of those files' text (first 1 MB). Nothing else in the folder can be read |
| `POST` | `/dialogues/{id}/stop` | Stop it and close both agents |
| `DELETE` | `/dialogues/{id}` | Remove it and everything in its folder |
