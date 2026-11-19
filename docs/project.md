# Projects

Status: designed 2026-11-19, being built. Built: projects and their session lists, `readonly`/`counted`, `agentop send` by project, Resume keeping the name, dialogues as projects, Sessions showing `default` only, the Coordinator (started for `default` by `agentop serve`, `POST /api/v2/projects/{name}/coordinator`). Not yet: the Projects plugin, a dialogue's Coordinator. It replaces the "Project groups" idea in the backlog.

## 1 What it is

A **project** is a group of sessions and a folder they share, the **project folder**. Projects are part of the core; plugins make and use their own. Every agentop session belongs to exactly one project.

Each project has a **Coordinator**: a session working in the project folder, the one the owner talks to. It sums up what the owner wants and routes the work to the project's other sessions, starting them when it decides one is needed or when asked. Talking to the Coordinator is the recommended way, not the only one: every session can be opened and used directly, unless its plugin made it read-only.

Projects are kept apart: a session sees, and sends to, the sessions of its own project; Active sessions and History are per project.

| Project | Owner | Shown in | Coordinator's job |
|---------|-------|----------|-------------------|
| `default` | core | Sessions | General: start sessions, fix, update and restart agentop |
| One per project of the owner's | `projects` plugin | Projects | Run a project: requirements, plan, routing work |
| One per dialogue | `dialogue` plugin | Dialogues | Design a dialogue, start and stop it, collect what it made |

## 2 The project and its folder

- A project is `~/.agentop/projects/<name>/`. The name is its folder's name: lowercase letters, digits, `-` and `_`, unique on the machine, never renamed (sessions' working directories and transcripts point at the folder).
- The folder holds everything of the project's that isn't in a git repo: briefs, requirements, plans, decisions, notes, hand-over files, a dialogue's turns and deliverables. Code stays in its repos.
- agentop's own record of it is in `<project>/.project/`: `project.json` (owner, created, the welcome message) and `sessions.json` (its sessions, section 3). Agents don't edit these; they read them through `agentop project show`.
- **Creating** a project, whoever asks for it, always does the same: make the folder and `.project/`, write the Coordinator's `AGENTS.md` and `CLAUDE.md` (section 4), start the Coordinator, and send it the welcome prompt. Creating doesn't open it in the app.
- `default` is created this way the first time `agentop serve` starts, if it isn't there. On every later start, a Coordinator that has stopped (after a reboot) is resumed.
- **Removing** a project stops its sessions and forgets it; its folder is kept unless "Delete the folder too" is ticked. `default` can't be removed.
- A plugin lists and changes only the projects it owns. No plugin changes `default`.

## 3 Sessions in a project

- A session's name is unique within its project only, so every project can have `dev`, `deploy` and `docs`. Its tmux name keeps agentop's random suffix (`dev-k3x9q2`) so it is unique on the machine; the project's `sessions.json` maps one to the other. The CLI and `agentop send` call a session `<project>/<name>`; the API keeps the tmux name as a session's ID.
- `sessions.json` replaces `~/.agentop/state/sessions.json`, with the same `flock`.
- Each project has its own Active sessions and History. Starting, resuming and stopping a session act in its project.
- **Resuming keeps the name.** A session from History comes back as it was, the same record in `sessions.json` under the same name; agentop no longer adds the date it was last used (`history.resume_name`). If a running session in the project has taken the name meanwhile, Resume says so and asks for another name.
- `max_active_sessions` stays one limit for the machine.
- Agents started outside agentop are no longer shown: **Started elsewhere** goes. Memory still indexes their transcripts.
- Memory and recall stay global, across projects.

The plugin starting a session sets two flags on it:

| Flag | Means | Default |
|------|-------|---------|
| `readonly` | Its terminal can only be watched, and `agentop send` refuses it. The plugin still drives it through the core's `Actor` | off |
| `counted` | It takes a place in `max_active_sessions` | on |

A dialogue's agents A and B are `readonly` and not `counted`; every Coordinator and every other session is neither. With projects keeping sessions apart and these two flags, the session `owner` field goes.

## 4 The Coordinator

- Named `Coordinator` in every project. It works in the project folder, with Codex if installed, else Claude, else the first other installed tool.
- It always exists: made with its project, resumed (same conversation) when it has stopped, never started afresh.
- Its instructions are files in the project folder, written when the project is created: `AGENTS.md` (read by Codex and Antigravity) and `CLAUDE.md` (`@AGENTS.md`). They say it is this project's Coordinator, what the folder is for, how sessions reach each other (section 5), and its owner's part (below). The owner and the Coordinator may edit them afterwards; agentop doesn't overwrite them.
- **Welcome.** Its first prompt asks it to introduce itself to the owner in a short welcome: who it is and "you can ask me to …". The owner of the project supplies both the instructions' own part and the welcome; the core's are for `default`:

| Project | "You can ask me to …" |
|---------|------------------------|
| `default` | open a new session for a task, look at what the sessions are doing, fix agentop, update it, restart it |
| A project | talk through requirements and a plan, start sessions to design, write docs, develop, deploy, route work to them and sum up where things are |
| A dialogue | design the dialogue (scenario, brief, agents, turns), start it, stop it, and collect what it made into this folder |

### 4.1 Other sessions know the project folder

A session other than the Coordinator usually works in a repo, whose `AGENTS.md`/`CLAUDE.md` belong to the repo and aren't touched. Instead its first prompt starts with a short header: the project, the project folder's path, that the Coordinator is called `Coordinator`, its own name, and "files that aren't for the repo go in the project folder". Then its task. It is started with the project folder as an extra writable directory (`--add-dir` for Claude and Codex; Codex's sandbox would otherwise keep it to the repo).

## 5 Sending between sessions

`agentop send` already knows which tmux session it runs in (`Session.current()`); from that it finds the sender's project.

1. The target is looked up in the sender's project only: `agentop send dev "…"` from a project's Coordinator reaches that project's `dev`.
2. From a shell outside any session, it is `default`; `--project <name>` picks another.
3. Another project's session takes its full name: `agentop send other/dev "…"`.
4. The header is `[agentop send from <project>/<name>]`, so the receiver can answer.
5. A `readonly` session refuses it.

## 6 For plugins

Python, through the plugin API:

- `api.projects.create(name, welcome, instructions)`, `list()`, `get(name)`, `remove(name, delete_folder)`: the plugin's own projects only.
- `api.projects.start(project, name, tool, cwd, prompt, readonly=False, counted=True)`, and stop and resume.
- Events when a project's sessions change, as for sessions today.

The UI: the core's Active sessions and History sections, and its session tabs, take a project, so a plugin's feature reuses them as they are. The core's feature for `default` is Sessions.

The core's commands work for every project: `agentop project list`, `agentop project show <name>`, and `agentop start --project <name> --name dev --cwd ~/repo [--tool claude] [--prompt "…"]` (from inside a session, `--project` defaults to its own). Creating and removing are the owning plugin's: `agentop project create | remove` (the `projects` plugin adds them to the core's `project` group) and `agentop dialogue start | remove`.

## 7 Features

### 7.1 Sessions (core)

The `default` project, as Sessions is today, without Started elsewhere and with Coordinator in Active sessions.

### 7.2 Projects (plugin `projects`)

- **Left panel**: New project (a name field), the project switcher below it, then the same sections as Sessions for the chosen project.
- Commands: `agentop project create <name>` and `agentop project remove <name> [--delete-folder]`. The Coordinator starts other sessions with `agentop start` (section 6).

### 7.3 Dialogues (plugin `dialogue`)

A dialogue is a project with three sessions: Coordinator, and agents A and B (`readonly`, not `counted`, kept at 80×40). Its folder is the project folder, which A and B already share as their working directory, so `brief.md`, `meta.json`, `turns.jsonl` and `deliverables/` stay where they are.

- **Left panel**: New dialogue instead of New project, then In progress and Finished as now. Picking a dialogue opens A and B side by side, as now.
- **New dialogue** creates the project and its Coordinator, and opens the wizard. The wizard is still the quick way; the Coordinator can do the same with `agentop dialogue start --project <name> …`.
- When the dialogue ends, the orchestrator sends the Coordinator a note: done, why, and where the turns and deliverables are.
- The Coordinator can be opened from the right panel. It can be stopped, and is resumed when the dialogue is opened again, so old dialogues cost nothing.

## 8 When a plugin is off

Its feature, commands and routes go away. Its projects' sessions are stopped, after a warning, as Dialogues does today; their folders are kept, and turning the plugin on again shows them, resuming each Coordinator when its project is opened.

## 9 Moving existing data

- Every session and history entry in `~/.agentop/state/sessions.json` goes into `default`. A name with the date an earlier resume added (`fix-auth 2026-11-01 10:00`) loses it. Names only have to be unique among running sessions, so ended ones may share one.
- Each folder in `~/.agentop/plugins/dialogue/dialogues/` moves to `~/.agentop/projects/<its name>/`, owned by `dialogue`. Its Coordinator is created when it is first opened.

## 10 Open questions

- Which files a project's Coordinator keeps in its folder by default (`brief.md`, `plan.md`, `decisions.md`, a log of what it routed where).
- Whether a Coordinator gets a note when one of its sessions goes idle (the `attention` plugin's waiting set), so it can pick up results without being told.
