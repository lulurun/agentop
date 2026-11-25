# Projects

Status: the mechanism is built (folders, session lists, the Coordinator's start and resume, routing between sessions), and so are the tools that hold a Projects Coordinator to its pursuit: tasks, stop reasons, idle checks and review (section 4.3). The goal, an intelligent Coordinator, is not reached yet: how well a Coordinator pursues is what section 4.1 asks of it and what the rest of this design serves. What is still open is in section 10. Where projects sit in agentop is in [0_00_design.md section 13](0_00_design.md#13-projects).

## 1 What it is

**The goal** is an intelligent Coordinator: a session that listens to what the owner wants, works out the best approach, and drives agents through agentop's own session operations (start, send, read, stop) until the project is done. The owner states the requirement and makes the decisions only they can make; the Coordinator does the rest of the getting-there. That goal is not reached yet, and it is what projects are for.

So a **project is a goal with a Coordinator pursuing it.** Each project has its own topic: something to build, a question to settle, a dream that is still vague. What sets a project apart from a pile of sessions is that one session, the **Coordinator**, owns the goal. It sums up what the owner wants, decides what to do next, starts the sessions the work needs and routes tasks between them, and reports back. Its job is pursuit: choosing and taking the next best action until the goal is met or the owner is really needed (section 4.1). Talking to the Coordinator is the recommended way, not the only one: every session can be opened and used directly, unless its plugin made it read-only.

**Sessions is the `default` project, the one without a goal.** It is the owner's manual way of working, kept as it was before projects: on-demand, occasional, unsure development or investigation, random tasks the owner drives themselves ([design 12.1](0_00_design.md#121-core-and-plugins)). There the owner is the Coordinator: they start each session from its first prompt in the start buffer ([0_04_start.md](0_04_start.md)), and nothing routes between them.

**What a project looks like.** A project is a group of sessions and a folder they share, the **project folder** (section 2), where the Coordinator keeps the project's brief, plans and decisions, and its source repositories live under `code/`. Every agentop session belongs to exactly one project. Projects are kept apart: a session sees, and sends to, the sessions of its own project; Active sessions and History are per project. Projects are part of the core; plugins make and use their own.

| | `default` | Any other project |
|---|---|---|
| Sessions listed in | Sessions | Projects (or Dialogues), per project |
| Coordinator | None: the owner | One, always there |
| Sessions started by | The owner, from the start buffer | The Coordinator (`agentop start`); no start buffer |
| Claude `--remote-control` | On | Off |

`default` and other projects run on the same code: one session store, registry, start, resume and stop, and the same API, keyed by project. The rows above are the only differences on the service, and they all follow from `projects.has_coordinator`. In the UI, Projects reuses Sessions' sections, tabs and right panel, without the start buffer.

| Project | Owner | Shown in | Coordinator's job |
|---------|-------|----------|-------------------|
| `default` | core | Sessions | None: the owner starts and runs its sessions |
| One per project of the owner's | `projects` plugin | Projects | Run a project: requirements, plan, routing work |
| One per dialogue | `dialogue` plugin | Dialogues | Design a dialogue, start and stop it, collect what it made |

## 2 The project and its folder

- A project is `~/.agentop/projects/<name>/`. The name is its folder's name: lowercase letters, digits, `-` and `_`, unique on the machine, never renamed (sessions' working directories and transcripts point at the folder).
- The folder holds the whole project. Briefs, requirements, plans, decisions, notes, hand-over files, a dialogue's turns and deliverables live at its top level; source repositories live under `<project>/code/<source-tree>`.
- Source intake is explicit. A repository URL is cloned under `code/`. A local non-repository tree is copied there, initialized as Git, and committed. A local Git repository is copied in full only when its tracked and untracked status is clean; otherwise the Coordinator asks the owner for a fully committed repository. An existing destination is never overwritten.
- agentop's own record of it is in `<project>/.project/`: `project.json` (owner, created, the welcome message) and `sessions.json` (its sessions, section 3). Agents don't edit these; they read them through `agentop project show`.
- **Creating** a project other than `default`, whoever asks for it, always does the same: make the folder and `.project/`, write the Coordinator's `AGENTS.md` and `CLAUDE.md` (section 4), start the Coordinator, and send it the welcome prompt. Creating doesn't open it in the app.
- `default` is made, folder and `.project/` only, the first time one of its sessions is recorded. It gets no instructions and no Coordinator.
- A Projects project's folder also has `track/` (section 4.3), the Coordinator's direction, plan and execution record, whose first direction is written when the Coordinator is first started, and `reviews/`, the reports of `agentop coordinator review`. `.project/` also holds `tasks.json` and `events.jsonl`, the pursuit record.
- **Removing** a project only takes it off the list (the left panel): `.project/project.json` is deleted, its running sessions are stopped, since they would be orphaned, and remove says which. Nothing else in the folder is written, marked or deleted, unless "Delete the folder too" is ticked (`--delete-folder`). `default` can't be removed. The registry lists no sessions of a folder without `project.json`. A project created again on the kept folder resumes: the track and `.project/` are as they were, tasks still open stay open (their sessions are gone, so the Coordinator fails or restarts them), and the new Coordinator gets the resume prompt rather than the welcome (section 4.3, Track).
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

- Named `Coordinator` in every project but `default`. It works in the project folder, with Codex if installed, else Claude, else the first other installed tool.
- It always exists: made with its project, resumed (same conversation) when it has stopped, never started afresh.
- Its instructions are files in the project folder, written when the project is created: `AGENTS.md` (read by Codex and Antigravity) and `CLAUDE.md` (`@AGENTS.md`). They say it is this project's Coordinator, what the folder is for, how sessions reach each other (section 5), and its owner's part (below). The owner and the Coordinator may edit them afterwards; agentop doesn't overwrite them, with one exception: in a Projects project, AGENTS.md carries the **Coordinator rules**, a block between `<!-- agentop:coordinator-rules … -->` markers that agentop adds, or replaces with the current rules, each time it starts or resumes the Coordinator. The rules are in AGENTS.md rather than only in the skill because the tools reload AGENTS.md on every turn, so they survive compaction, while the skill in the first prompt fades.
- For a project owned by the Projects plugin, agentop keeps `COORDINATOR_SKILL.md` in the project folder and embeds its body in the Coordinator's initial and resume prompts. Root instructions contain only a conditional pointer: the session named `Coordinator` reloads the file after `/clear`; every other session must not load it. The skill is the manual for the rules: the turn loop, the stop reasons with good and bad examples, delegating with `agentop coordinator task`, the track, the project folder and source intake, and the milestone review and context reset. How a Coordinator is judged is in the skill only as a short closing section; the full philosophy stays in section 4.1, the standard any evaluation goes back to. It is outside agentop's globally installed skills bundle and is not loaded into dialogue Coordinators or another project session.
- **Welcome.** Its first prompt asks it to greet the owner, from the owner's side, with "Hi, I'm the Coordinator of this project.", and list what they can ask it as bullets. The owner of the project supplies the list and the instructions' own part:

| Project | "You can ask me to:" |
|---------|------------------------|
| A project | discuss the direction and requirements; work out an execution plan; design; create a new repo; start sessions and assign or schedule tasks to them; sum up where things are. Its last line: you can also talk to each session directly |
| A dialogue | design a dialogue (scenario, brief, agents); start it; stop it; collect what it made and sum it up |

### 4.1 Pursuit philosophy

This section is the fixed standard a Coordinator is evaluated against, for as long as projects exist. The rules, the skill and the tools (4.2, 4.3) are how agentop currently tries to meet it, and may change; this does not.

The Coordinator's primary job is **pursuit**: proactively choose and take the next best available action toward the project's objective, observe the evidence, and choose again. In an ambiguous situation it investigates, runs a bounded reversible experiment, or asks another session for a focused recommendation instead of returning every uncertainty to the owner. It continues until the objective is achieved or a real terminal condition requires the owner, new authority, a risk decision, or an external-state change.

Coordinator quality and LLM result quality are different axes. A stronger Coordinator pursues more useful next-best actions; a weaker one stops, waits, or escalates prematurely. A good result can come from a capable LLM or luck even when coordination was passive. A poor result can occur after reasonable pursuit of an uncertain path. Therefore agentop asks the Coordinator for **effort and pursuit**, and asks the underlying LLM for **result quality**. Over many decisions, capable models should convert good pursuit into good results more often, but one outcome does not retrospectively define the quality of the coordination behavior.

“More pursuit” does not mean more sessions, prompts, retries, or process. It means more useful progress: selecting actions by expected value, containing risk, checking evidence, learning, and continuing. Pursuit ends when the objective is met, a material human decision or new permission is required, a safety boundary is reached, or no remaining action has reasonable expected value.

**Deciding under ambiguity.** The Coordinator uses the context it has and investigates when that is useful. It makes informed assumptions that keep work moving, rather than returning uncertainty to the owner because no instruction prescribes one exact path. Inspecting the project and repository, running safe read-only checks, testing a reversible hypothesis, and asking another session for a focused expert recommendation all reduce uncertainty; none is a reason to hand the decision itself away. The Coordinator integrates the evidence and chooses. It asks the owner when missing input would materially change product intent, authority, external effects, destructive scope, or an expensive or hard-to-reverse outcome; otherwise it chooses the likely-good path, contains its risk, and verifies it.

**Evaluating a Coordinator.** By the quality and persistence of its pursuit:

- **Yes:** a stronger Coordinator pursues more useful next-best actions, keeps making progress under incomplete information, and stops only for a real terminal condition.
- **No:** a Coordinator is not good merely because an answer or implementation happened to be good, nor bad merely because an uncertain attempt produced a bad result. Raw result quality is mainly the capability of the LLM doing the work.

A lucky result does not excuse passive coordination, and an unlucky result does not erase strong pursuit based on the evidence available at the time. Results are feedback for calibration, not hindsight proof that the process was good or bad. For each stretch of work, ask whether the Coordinator:

1. kept the objective in view;
2. recognized the uncertainty;
3. chose reasonably from the information available at the time;
4. bounded the risk;
5. checked the evidence;
6. learned from what happened;
7. and continued, stopping only for a real terminal condition.

Coordinator effort and LLM result quality are reviewed separately.

### 4.2 Why the rules are enforced, not only written

Written guidance gave uneven results. The commonest failure was a passive stop: a Coordinator ended its turn with a summary, or a list of open questions with no recommendation, and waited, when the next action was clear from the objective. The handover contract (goal, criteria, artifact, at most three rounds) was kept when the model remembered it. So the parts that can be checked are checked by agentop's commands, and the rest is measured, so that the owner sees a trend rather than an impression.

### 4.3 Pursuit tools (Projects plugin)

Only a Projects project's Coordinator works under them; a dialogue's does not. Its commands are under `agentop coordinator` ([0_08_cli.md 2c](0_08_cli.md#2c-coordinator-commands-projects-plugin)), the task's session's report included: `agentop coordinator task report`.

**Track** (`<project>/track/`). The Coordinator's record, written by agentop's commands so it doesn't depend on a prompt telling the Coordinator to edit files. It replaces `STATE.md`.

```
track/
  counter.json        # the current direction, the task count per action, the total; the plan's highest numbers
  tasks.jsonl         # one line per task event: open, report, reject, accept, fail, carry
  1/  DIRECTION.md  PLAN.md  EXECUTION_M1.md  EXECUTION_M2.md
  2/  DIRECTION.md  PLAN.md  EXECUTION_M1.md
```

- **Generations.** The current direction is the highest numbered folder (`counter.json` caches it). `agentop coordinator direction new --text -` makes `track/N+1/` with its `DIRECTION.md` and a `PLAN.md` (`--copy-plan`: a copy of the last plan's milestones); files of older directions are never written again, and commands that would write them refuse. It refuses while tasks of the current direction are open, unless `--keep-open`; then each is re-homed with `task carry <id> --to <action>`, and until then yield refuses. While direction 1 is still the placeholder and no task has run, `direction new` fills it in rather than making a second.
- **DIRECTION.md**: `# Direction N`, the date, who decided it, then `## Direction` (what the project pursues now, and what done means), `## Why it changed` and `## Carried forward` (repos and branches, deployed state, the owner's standing constraints, lessons). It may be edited by hand while it is current.
- **PLAN.md**: milestones `## M{m} <title>  [status]` (planned, in progress, done, dropped), each with its actions `- M{m}A{a} <title>  [status] → <task IDs>` (planned, in progress, waiting on owner, blocked, done, `dropped (reason)`). Numbers are never reused in a generation: a new milestone or action takes the next free number, even when placed earlier with `--before`; order is the order in the file. The Coordinator writes titles, order and summaries through `plan milestone add`, `plan action add|set`, `plan ask|answer` and `milestone close`; agentop updates the statuses and task links as tasks open (in progress), are accepted (done, when no other task of the action is open), or fail (back to planned). `plan show` prints the open milestones in full and closed ones as a line and their summary (`--all`: the file).
- **Questions for the owner** are actions waiting on the owner, with the question, the recommendation, the default and when it was asked. `yield owner` makes one in the current milestone, or refreshes the open one with the same question and counts `asked N×`; `plan answer M1A3 "…"` records the answer under it, writes it as an execution note by the owner, and marks it done.
- **EXECUTION_M{m}.md**: one entry per task, rewritten by agentop at each of its events: `## P2_M1A2-2  session \`dev\`  claude (Sonnet 5.5)`, opened and closed, rounds, status; Input (the goal, criteria, artifact and the first lines of the brief), Output (each round's report), Evaluation (each rejection's feedback, then the accept's `--checked` or the fail's note). `agentop coordinator note M2A1 "…" [--by owner]` adds an entry for work done without a task, and `plan action set … --status done` needs an accepted task or a note on the action (or `--note`).
- **IDs.** A task is `P{N}_M{m}A{a}-{k}`: k counts per action from 1, so a retry or a second parallel task takes k+1, while rejected rounds stay in one task. The counter is in `counter.json`, updated under the pursuit lock, and survives a project re-created on its folder. `tasks.jsonl` is append-only (id, action, direction, session, time, event, status, round), so review can derive tasks, rounds and times per direction. A task's session keeps its short name; its ID is shown in its brief, reports and lists, and the task commands take either.
- **Resume.** `agentop coordinator status` is the resume view: the current direction, the plan with its open milestones, the current milestone's execution entries, open tasks, and the yield checks below as warnings. The resume prompt and every after-clear note tell the Coordinator to run it. A Coordinator starting on a folder whose track is in use (more than the placeholder) gets the resume prompt, not the welcome.
- **Migration.** A new project gets `track/1/` with a placeholder direction and an empty M1, instead of `STATE.md`. A project with `STATE.md` and no `track/` works under the old `STATE.md` checks until its Coordinator runs `direction new`; then `STATE.md` is left in place and no longer checked.

**Tasks** (`agentop coordinator task`). Delegated work is a task on an action of the plan: `agentop coordinator task start --action M1A2 <name> --tool … --cwd … --goal … --accept … --artifact …` refuses without an action of the current plan that isn't done or dropped (once the project has a track), a goal, at least one acceptance criterion, and an artifact path that is a file inside the project folder (not `.project/`). It starts the session with a brief: the goal, the criteria, the artifact's absolute path, and how to report. The brief can be passed on stdin (`--prompt -`), so the shell leaves backticks alone. The session ends its work with `agentop coordinator task report "<summary>"`, which refuses while the artifact is missing (unless `--blocked`), records the round and tells the Coordinator, so a result does not depend on the session remembering to call back. The Coordinator then decides: `accept --checked "<what it verified>"` (the artifact must exist; the session is stopped), `reject --feedback …` (the next round; refused after the third), or `fail --note …`; each writes the execution entry and the action's status. A task of an older direction must be carried before it is decided. A plain `agentop start` from a Projects Coordinator is refused unless `--untracked`, which review counts.

**Stop reasons** (`agentop coordinator yield`). Every Coordinator turn ends with one, and each is checked:

| Kind | Checked |
|------|---------|
| `done` | `--evidence`, `--next`; no open task; a review since the last task closed, with its reflection written |
| `waiting` | `--next`; at least one open task awaits a report |
| `owner` | `--question`, `--recommend`, `--default` (`--meanwhile` optional); it becomes a waiting-on-owner action |
| `blocked` | `--need`, `--tried` |

Every yield is also refused, with a message naming the fix, while:

- there is no `track/` yet, or the current direction has no direction text (except `owner`: asking for it is the fix);
- an open task has no action, or belongs to an older direction and wasn't carried;
- an action is done with no execution entry and no note;
- every action of a milestone is done or dropped, but the milestone isn't closed with a summary (`milestone close M1 --summary -`);
- a report waits for the Coordinator's decision.

A project still on `STATE.md` (no `track/`) is checked as before: refused while `STATE.md` is missing a section, still has no objective once tasks exist, or is older than the last closed task.

**Next** (`--next`). What the Coordinator will do next and why: the one thing a cleared context needs that the track doesn't already hold. `waiting` and `done` refuse without it, or with a placeholder (`TBD`, `…`, `<…>`, `continue`, fewer than eight letters); `owner` and `blocked` take it optionally. Each yield that has one replaces `track/<N>/NEXT.md` whole (a temporary file, then a rename), with the date and the yield's kind, and records it on the yield event. `agentop coordinator status` prints it first, and the prompt after a clear includes it verbatim. A project still on `STATE.md` keeps it in `.project/NEXT.md`.

**Clear on wake.** A Coordinator's context grew with every report and every check it ran: one that never cleared ran at 150–245k tokens for days, so each wake cost 1–4M input tokens and took most of a Codex Plus 5-hour limit, while the track already carries the plan across a clear. Clearing at turn end was fragile: the yield runs inside the turn, and Codex refuses a `/clear` sent then ("'/clear' is disabled while a task is in progress"), so it never cleared a Codex Coordinator. So the clear happens at the wake instead. When agentop is about to type a task event into the Coordinator (a report, a blocked report, a quiet-session notice), it first checks that clearing is safe:

- the Coordinator's last yield is `waiting`, `done` or `owner`, without `--keep`, and comes after the last thing agentop delivered to it (a report, a notice, an `agentop send`, the owner's prompt from the Input bar: each is logged as a `wake` event);
- no other report waits for its decision, and the track checks above pass;
- the attention plugin's status feed ([0_12_05_attention.md](0_12_05_attention.md#subscribe-to-status)) says, from the tool's own lifecycle events, that its turn has ended (`done`, or `idle` after a start or a clear) at least a second ago, since Codex can still refuse a `/clear` for a moment after its Stop. While the feed says `working`, agentop waits up to 5 seconds for the Stop (the feed is refreshed on the 2-second scan, so a report typed as the yield ends would otherwise always find it busy), then gives up with `the Coordinator is busy`. `input` (a permission prompt or a question) is `the Coordinator is waiting for input`. No status, `unknown`, or a state the feed took from the screen (no hooks: Antigravity, an unmanaged or older session; the plugin off or the service down; the screen fallback's guesses) is `status unknown`: the screen is never read for busy or idle;
- its input line holds no draft. This is the one check before a clear that still reads the screen, since only the screen shows what the owner is typing: the draft is read from a capture with its attributes, and what is drawn dim is the tool's placeholder or suggestion, not text.

If any fails, the event is typed as it is, and `clear.skipped` is logged with the reason. Otherwise agentop types `/clear` (`clear.sent`) and waits up to 20 seconds for a sign it landed: for Claude, `~/.claude/sessions/<pid>.json` naming a new session ID (the file belongs to the innermost process when a launcher wraps Claude Code); for either tool, a cleared screen: the tool's banner (`Claude Code v`, `>_ OpenAI Codex`) back on screen with no earlier prompt above the input line but `/clear` itself, and for Codex a footer that names no thread. On a sign it logs `clear.verified` with the sign and the seconds, waits for the screen to settle, and types one prompt: `[agentop] Your context was cleared. Run agentop coordinator status (it starts with your Next), then handle this:`, the Next, and the event. Without one it logs `clear.skipped` (`not verified within 20s`) and types the event after a line saying the clear may not have happened. Claude queues a `/clear` sent mid-turn and runs it when the turn ends, so the event then still lands after it; Codex refuses it and leaves the input empty. Two reports at once take turns (a lock per project), so the second sees the first's wake and isn't cleared over. This runs in the service, where it queues with the Input bar's prompts (`POST /plugins/projects/projects/{name}/wake`) and reads the feed directly; without the route, `task report` does the same itself and asks the attention plugin's socket for the status, and with no service at all it finds none and doesn't clear. Measured on this host with Haiku 5.5 and GPT-5.6 Luna, from the feed: to an idle Coordinator, `/clear` went 0.3–0.5 s after the report, was verified 2.0–2.3 s later, and `task report` took 2.6–3.5 s to the prompt typed (7–8.5 s when a 5-second still screen stood for idle); a report that came while the yield's turn was ending was cleared 1.9 s after its Stop; to a Coordinator still in a tool call it was typed without a clear after the 5-second wait.

**Safety net.** Some stretches have no wake, such as a long conversation with the owner. So at the end of each Coordinator turn (a `done` in the status feed, 2 seconds old, seen on the scan tick through a per-session subscription) agentop also clears, through the same safe check and verification, when its context holds more than 60% of the model's window (from the last turn's usage in the session's own transcript; Claude's window is taken as 200k, Codex records its own; `clear_context_at` in the project's `project.json` changes the share), or a milestone was closed since the last clear. It types nothing after such a clear: the next event or owner prompt reaches the Coordinator with the note to run `agentop coordinator status`. `yield --clear` is the Coordinator's own request, through the same check; after it, agentop types a prompt to carry on from the Next. A clear never happens while a turn runs, a question waits, the status is unknown or a draft is in the prompt, and once one is verified nothing clears again until something has reached the Coordinator. A Coordinator whose status turns unknown while a clear is due gets one `clear.skipped` (`status unknown`) in the log.

**Idle task sessions.** A task's session whose turn has been over (`done` in the status feed) for 2 hours with its round unreported is reminded once; if it ends another turn and is again quiet that long, the Coordinator is told to look at it, as a wake like a report. A session waiting for input, or with no status from its hooks, is never reminded. A Coordinator turn that ends without a yield is not counted: an idle screen changes too (Codex prints account-wide usage warnings into every pane at once, `/status`, a draft left in the prompt), and the count this once kept was mostly those ([7_coordinator-dogfooding.md](7_coordinator-dogfooding.md), 11-23 and 11-24).

**Reception** (Input bar). A Coordinator can't judge whether to clear its context before reading a request, and by then the request is in the long context. So with reception on, which is the default for a Projects Coordinator, the bar has a cheap model compare the owner's prompt with the recent conversation and the track's direction and current milestone (`STATE.md`'s Objective and Next in a project without a track), and when it looks like new work, offers to clear first; the owner decides ([0_12_04_input.md section 4](0_12_04_input.md#4-reception)).

**Review** (`agentop coordinator review`). It measures the Coordinator from `events.jsonl` and `tasks.json`, since the last review (`--all`: since the start), and writes `reviews/<time>.md`:

- stop reasons by kind (only recorded ones: turns that end without one aren't counted, see Idle task sessions);
- the share of stops handed back to the owner, with each question, recommendation and default listed, for the owner to judge whether it was needed;
- tasks opened, accepted, failed, rounds used, accepted in round 1, median time to a report, blocked and quiet reports, untracked sessions, tasks waiting over two hours;
- findings, each with what to do, and the trend of the main shares against the last review;
- a last section for the Coordinator's reflection, which asks the seven questions of 4.1.

The Coordinator runs it at each milestone, writes its reflection, keeps one change under Carried forward in `DIRECTION.md`, and tells the owner the path and top findings; `yield done` won't pass without it. It measures pursuit, the countable part of section 4.1, not result quality. `GET /projects/{name}/pursuit` returns the tasks, the all-time numbers and the latest report's path.

### 4.4 Other sessions work inside the project folder

A session other than the Coordinator usually works in a repository under `<project>/code/`, whose `AGENTS.md`/`CLAUDE.md` belong to the repository and aren't touched. Its first prompt starts with a short header: the project, the project folder's path, that the Coordinator is called `Coordinator`, its own name, and where code and project notes live. Then its task. Because the working directory is already inside the project folder, agentop doesn't pass an extra writable directory. A session explicitly started outside the project still receives the project folder through `--add-dir`.

## 5 Sending between sessions

`agentop send` already knows which tmux session it runs in (`Session.current()`); from that it finds the sender's project.

1. The target is looked up in the sender's project only: `agentop send dev "…"` from a project's Coordinator reaches that project's `dev`.
2. From a shell outside any session, it is `default`; `--project <name>` picks another.
3. Another project's session takes its full name: `agentop send other/dev "…"`.
4. The header is `[agentop send from <project>/<name>]`, so the receiver can answer.
5. A `readonly` session refuses it.

## 6 For plugins

Python, through the plugin API ([0_12_00_plugins.md](0_12_00_plugins.md#what-registerapi-gets)):

- `api.create_project(name, welcome, instructions)`: make a project the plugin owns, write its Coordinator's instructions, and start the Coordinator.
- `api.remove_project(name, delete_folder)`: stop its sessions and forget it; only the plugin's own.
- `api.start_coordinator(name)`: the project's Coordinator, running, or resumed or started now.
- Other sessions are started with the core's `ops.start(project=…, readonly=…, counted=…)`, and the plugin hears about them through the session events (`api.on`).

The UI: the core's Active sessions and History sections, and its session tabs, take a project, so a plugin's feature reuses them as they are. The core's feature for `default` is Sessions.

The core's commands work for every project: `agentop project list`, `agentop project show <name>`, and `agentop start --project <name> --name dev --cwd ~/.agentop/projects/<name>/code/<repo> [--tool claude] [--prompt "…"]` (from inside a session, `--project` defaults to its own). Creating and removing are the owning plugin's: `agentop project create | remove`, which act on the Projects plugin's projects and need it on, and `agentop dialogue start | remove`.

## 7 Features

### 7.1 Sessions (core)

The `default` project, without Started elsewhere and without a Coordinator. New session starts one (Claude with `--remote-control`); it gets no project header and no `--add-dir`.

### 7.2 Projects (plugin `projects`)

- **Left panel**: Projects (a name field and Create, then the list, the chosen one highlighted, each with a Remove icon), then for the chosen project Active sessions and History. There is no New session: the owner asks the Coordinator, which starts the project's other sessions.
- Commands: `agentop project create <name>` and `agentop project remove <name> [--delete-folder]`. The Coordinator starts other sessions with `agentop start` (section 6).

### 7.3 Dialogues (plugin `dialogue`)

A dialogue is a project with three sessions: Coordinator, and agents A and B (`readonly`, not `counted`, kept at 80×40). Its folder is the project folder, which A and B already share as their working directory, so `brief.md`, `meta.json`, `turns.jsonl` and `deliverables/` stay where they are.

- **Left panel**: Create new dialogue, then In progress and Finished. Picking a dialogue opens A and B side by side, as now.
- **New dialogue** (the wizard, or `agentop dialogue start`) creates the project and starts its agents and its Coordinator. To design a new dialogue, the Coordinator writes a brief and runs `agentop dialogue start`, which makes another dialogue with its own folder and Coordinator.
- When the dialogue ends, the orchestrator sends the Coordinator a note: done, why, and where the turns and deliverables are.
- The Coordinator can be opened from the right panel. It can be stopped, and is resumed when the dialogue is opened again, so old dialogues cost nothing.

## 8 When a plugin is off

Its feature, commands and routes go away. Its projects' sessions are stopped, after a warning, as Dialogues does today; their folders are kept, and turning the plugin on again shows them, resuming each Coordinator when its project is opened.

## 9 Moving existing data

- Every session and history entry in `~/.agentop/state/sessions.json` goes into `default`. A name with the date an earlier resume added (`fix-auth 2026-11-01 10:00`) loses it. Names only have to be unique among running sessions, so ended ones may share one.
- Each folder in `~/.agentop/plugins/dialogue/dialogues/` moves to `~/.agentop/projects/<its name>/`, owned by `dialogue`. Its Coordinator is created when it is first opened.

## 10 Open questions

- Whether review should also judge the transcripts (an LLM reading the turns that ended `owner` or `blocked`, and saying whether they were needed), beyond what the event log counts.
- Whether the Projects feature should show the tasks and the latest review beside the sessions (the route is there).
- Whether the draft check and the clear's verification can come from the tools too (a `SessionStart` after `/clear`, a prompt-typed event), so the Coordinator stops reading the screen altogether; for now a clear also needs a recorded yield, and a draft is read from the input line's attributes.
