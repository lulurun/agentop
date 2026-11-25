# Starting from a prompt

How Sessions starts and tidies its sessions: the owner types a first prompt, and agentop works out the tool, model, name and working directory, starts the session with that prompt, and later closes sessions left idle. It changes Sessions (the `default` project) only; a project's sessions are started by its Coordinator as before ([0_13_projects.md](0_13_projects.md)). Where it sits in agentop is in [0_00_design.md section 4.7](0_00_design.md#47-starting-from-a-prompt).

## 1 Why

- **Convenience.** New session asked for a tool, a folder and a name before any work could be said. Most of that can be read from the work itself.
- **A trial for the Coordinator.** A Coordinator also has to pick a tool and model for each task, and today picks from habit. Sessions is where that choice is tried first, with the owner watching each one and correcting what is wrong: the corrections are the measure ([section 6](#6-the-record)). What holds up here is meant to serve `agentop coordinator task start` later; the Coordinator is not changed yet.

## 2 What was decided (2026-11-24)

- **Not a feature, and not a session.** It was first proposed as a long-running reception session, like a Coordinator, that every prompt goes to. Turned down: each prompt stands alone, so such a session's context would only pile up unrelated requests (the very thing the input bar's reception exists to clear), its answer would have to be read off a screen, and a TUI turn is slow. What it does is a one-shot cheap-model call per prompt (the **probe**) and plain service code. The conversation itself moves to the new session at once.
- **The probe judges the task; code picks the model.** The probe says what kind of task it is and how hard (a **tier**); the **chooser** turns the tier into a tool and model from a catalogue, by installed tools, the owner's order, and the quota left. Quota changes by the minute and a cheap model weighs it badly; code's choice can be explained in one line and reused by the Coordinator without trusting a cheap model. A tool or model the prompt names always wins.
- **No New session.** Sessions' left panel loses Create new session. The middle panel's left area always has the **start buffer**, a pinned tab, and sessions open as tabs beside it.
- **The words that ask for the session stay with agentop (2026-11-24).** A prompt like "please open a new session for dev agentop" was handed over as typed, and the new session read it as its task and opened another session. The probe now also gives the **task**: the prompt with only those words taken out, the rest word for word. A cheap model that rewrote whole prompts would lose detail, so it only cuts, and the row shows the cut before launch. When nothing is left, the session starts with no first prompt.
- **Tidying is code, on a timer.** A session idle for `idle_hours` is closed, and a start at the limit first closes the session idle longest. Closed sessions are in History and resume as before.
- **The probe runs on agy by default** (Gemini Flash, low), the tool whose quota the owner uses least, and the owner can change it in the buffer.
- **Claude's quota is unknown for now.** Codex records its limits in its rollouts; Claude Code keeps no local record, and reading `/usage` off a pane was turned down. An unknown quota ranks a tool neither up nor down.

## 3 The probe

`POST /api/v2/start/probe {prompt}` runs the probe tool's one-shot command (`AgentCli.ask`) with:

- the prompt, cut to 6,000 characters;
- the installed tools and the catalogue's models, by tier;
- candidate working directories: the folders of the owner's sessions in the last 30 days (every project), newest first, and the Git repositories directly under the usual roots (`~/workspace`, `~/code`, `~/src`, `~/projects`, `~/repos`), at most 40.

It answers JSON: `title` (a few words, the session's title), `name` (a short name, `[a-z0-9-]`), `kind` (`code`, `writing`, `research`, `chat`), `tier` (`light`, `standard`, `complex`), `tool` and `model` only when the prompt names them, `cwd` (one of the candidates, a path the prompt names, or `none` when the task needs no files), `cwd_sure` (false when it is guessing), `reason` (one sentence), and `task`: the session's first prompt, which is the prompt with only the words asking for the session taken out: the request to open one, and which tool, model, folder or project it is for ("open a new session for dev X", "start a codex session in X to"). It is "" when nothing is left. A prompt over 1,500 characters isn't cut, because copying it out would take the probe too long.

agentop checks what it got: a `cwd` that isn't a directory is dropped, a named tool that isn't installed or a model it doesn't offer is dropped, a missing tier is `standard`, and a missing `task`, or one longer than the prompt, is the prompt itself. If the probe fails or takes over 40 seconds, the start goes on without it: the title is the prompt's first words, the tier `standard`, the task the prompt as typed, and the folder is asked. A task that needs no files runs in the project folder, `~/.agentop/projects/default/`.

## 4 The chooser

The catalogue is each tool's models with a tier (`AgentCli.models`), as `agentop start --model` takes them:

| Tool | light | standard | complex |
|------|-------|----------|---------|
| claude | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 |
| codex | GPT-6-Luna | GPT-6.1-Terra | GPT-6.2-Sol |
| antigravity | Gemini 3.8 Flash (Low) | Gemini 3.8 Flash (High) | Claude Opus 4.6 (Thinking) |

For the probe's tier, the candidates are that tier's models on installed tools. Each is ranked by its tool's quota: the highest `used_percent` of the tool's windows (Codex: 5-hour and weekly). A tool at 90% or more goes last; otherwise lower use ranks first when two tools differ by 20 points or more, and the owner's tool order (`tool_order`) breaks the rest. An unknown quota counts as 50%. The decision carries a line saying why ("complex: codex is at 12%, claude unknown, so codex's GPT-6.2-Sol").

A tool the prompt names keeps its model at the tier's choice, or the model named.

## 5 Tidying

On a check every minute, for the `default` project's counted sessions that agentop started:

- **Idle.** A session's last activity is the last output in its agent's window (tmux's `window_activity`), the signal the Waiting for input plugin reads. One with none for `idle_hours` (default 24) is stopped, and moves to History. A screen that keeps redrawing (a clock, Codex's warnings) counts as active, so tidying errs towards keeping.
- **The limit.** `max_active_sessions` is one limit for the machine. A start from the buffer at the limit first stops the `default` session whose last activity is oldest, if it has been idle for at least 10 minutes; if none has, the start is refused and says so. Starting never stops a project's session.
- A session closed by either is marked in the record with why.

## 6 The record

`~/.agentop/projects/default/.project/starts.jsonl`, one line per event: each probe (prompt, what it answered, the candidates, its tool and model, time taken), each choice (tier, quota seen, the chooser's line), what was started (with the first prompt it was given, and which of tool, model, name, folder and prompt the owner changed before it started), and each session closed by tidying. The owner's changes against the probe's and the chooser's answers say how often each is right, before the chooser is offered to the Coordinator.

## 7 Settings

Kept on the service (`~/.agentop/state/start.json`), set in the buffer's settings: the probe's tool and model (default `antigravity`, Gemini 3.8 Flash Low; else the first installed tool with a one-shot command), `tool_order` (default codex, claude, antigravity), and `idle_hours` (default 24; 0 turns idle closing off).

## 8 In the app

The start buffer is the first tab of Sessions' left area, pinned. It has the input box, and below it what was started from it, newest first.

1. Sending clears the box. The row reads "Prompt received · working out where to run it…".
2. With the probe's answer it reads "Launching **title** in 3s", with the tool, model and folder as chips and **Change**, **Launch now** and **Cancel**. When the task differs from what was typed, the row shows it as "First prompt …", or "No first prompt" when nothing is left. Change can edit it. The 3 seconds are for catching a wrong folder before an agent starts working in it. Change opens the chips for editing and stops the countdown until Launch.
3. When the folder isn't known, the row asks for it instead of counting down: the best candidates as buttons, and a field.
4. On launch the session starts with the task as its first prompt, or with none, and opens in a tab next to the buffer, unless the owner has started typing another prompt. The row reads "Launched **title**", a link to the session; later rows show it running, waiting, or closed and why.

`GET /api/v2/start/recent` lists the rows, `GET /api/v2/start/models` the catalogue with each tool's quota, and `GET`/`PUT /api/v2/start/settings` the settings.
