# Plugins

Optional features built on the agentop core. Where the line between core and plugins is drawn, and why, is in [0_00_design.md section 12](0_00_design.md#12-plugins). This page says how plugins work, then what each one does. Each plugin has a number; a plugin with a doc of its own has it as `0_12_<number>_<id>.md`. Projects (01) is also a core mechanism, so its doc is design section 13's, [0_13_projects.md](0_13_projects.md).

| # | Plugin | What it does | On by default | Doc |
|---|--------|--------------|---------------|-----|
| 01 | Projects (`projects`) | The owner's own projects: each a goal, pursued by a Coordinator session the owner talks to, which drives the project's other sessions | Yes | [0_13_projects.md](0_13_projects.md) |
| 02 | Dialogues (`dialogue`) | Two agents work together on one task; an orchestrator passes messages between them, following a scenario | Yes | [0_12_02_dialogue.md](0_12_02_dialogue.md) |
| 03 | Memory (`memory`) | agentop's own copy of every transcript on the host, with search, session summaries, and `agentop recall` | Yes | [0_12_03_memory.md](0_12_03_memory.md) |
| 04 | Input bar (`input`) | A box below a session's terminal to type prompts in, with key buttons; it keeps the draft out of tmux | Yes | [0_12_04_input.md](0_12_04_input.md) |
| 05 | Waiting for input (`attention`) | Marks a session whose agent was working and has stopped | Yes | [below](#waiting-for-input-attention) |
| 06 | File viewer (`viewer`) | Opens files, diffs, Markdown, images and transcripts from the right panel in a tab | Yes | [below](#file-viewer-viewer) |
| 07 | Email code (`email-code`) | Sign in with a one-time code sent by email | No | [below](#email-code-email-code) |

Handoff is not a plugin: `agentop send` and the `handoff` skill pass a side task to another session ([design 4.6](0_00_design.md#46-sending-between-sessions)). Plugins not built yet are in [9_backlog.md](9_backlog.md).

## How plugins work

### Layout and manifest

Each plugin is one folder under `src/agentop/plugins/`, with its UI in a matching folder under `ui/src/plugins/`:

```
src/agentop/plugins/
  attention/
    plugin.toml         manifest
    __init__.py         defines register(api)
    cli.py              optional: defines register_cli(cli)
ui/src/plugins/
  attention/
    index.tsx           the UI pieces, keyed by slot
```

The manifest declares what the plugin is, without running any of its code:

```toml
id = "attention"                 # folder name; [a-z0-9-]+
name = "Waiting for input"
version = "0.1.0"
description = "Marks sessions whose agent was working and has stopped."
plugin_api = 1                   # the core plugin API version it was written for
default_enabled = true
ui = ["session-badge", "settings-section"]   # UI slots it fills
cli = []                         # agentop commands it adds
```

The built-in folder, `src/agentop/plugins/`, is always scanned first. `AGENTOP_PLUGIN_DIRS` adds more folders, separated by `:`; when two folders hold a plugin of the same name, the first one found wins, and the other is skipped with a warning in the log. The tests use it to install broken plugins (`tests/broken_plugins.py`). It is read from the service's environment, which only someone who can already run code as the user can set. No folder outside the install is scanned by default: loading code from a user directory such as `~/.agentop/plugins/` would let anything that can write there run inside the service ([design D8](0_00_design.md#15-decisions)).

### Loader

The loader (`service/plugins.py`, `PluginHost`) is part of the core. At service start it:

1. Scans `*/plugin.toml` in `src/agentop/plugins/`, then in each folder in `AGENTOP_PLUGIN_DIRS`.
2. Reads each manifest (`plugin_manifest.py`). A missing or invalid manifest gives state `failed`, with the reason.
3. Checks `plugin_api` against the core's supported range. Outside it gives `incompatible`.
4. Reads the saved enabled flag, falling back to `default_enabled`.
5. For each enabled plugin, imports it and calls `register(api)`, each inside its own `try`, with a time limit (5s). An exception or timeout gives `failed`, and the error is logged and kept for the API.
6. Builds the **plugin list**: every plugin found, with its state.

States: `enabled`, `disabled`, `failed`, `incompatible`. A plugin that fails never stops the loader, the remaining plugins, or the service from starting. With no `plugins` folder at all, the service runs exactly as the core alone.

`register(api)` only declares routes, handlers, and tasks. The loader switches them on after `register` returns in time, so a plugin that fails part way through leaves nothing behind. A plugin's own modules import as `agentop.plugins.{id}.…`, so relative imports work. Repeated handler errors (5 in a minute) move the plugin to `failed`, so a broken plugin stops being called.

### What `register(api)` gets

A narrow object, not the service internals:

| Facility | Rule |
|----------|------|
| `api.router` | A FastAPI router mounted at `/api/v2/plugins/{id}/`. It inherits token auth. A plugin cannot add routes anywhere else |
| `api.public_router` | Routes with no client token, for signing in. Only a plugin whose manifest names the `login` slot may add them; any other that does fails to load ([0_06_security.md 4](0_06_security.md#4-ways-to-sign-in)) |
| `api.sign_in(name)` | Signs in an ordinary client and returns its token, for a login plugin's public routes |
| `api.on(event, handler)` | Core events: `session.started`, `session.exited`, `session.closed`, `scan`. The handler is a plain function, called with the session row (or, for `scan`, the session list). It runs in a thread off the event loop, with a time limit (10s), isolated: an exception is logged and counted, and never reaches the core, which never waits for its result |
| `api.task(fn)` | A supervised background task. `fn` is an async function, not a coroutine, so the task can be started again when the plugin is enabled again. If it raises, the plugin goes to `failed` |
| `api.on_disable(fn)` | Called, in a thread, when the user turns the plugin off, to wind down work it started. Not called when the service stops, so work that outlives the service (a process the plugin started) keeps running then |
| `api.sessions()` | A read-only snapshot of the session list |
| `api.session(id)` | One live session's row, or else a past one's (tool, agent session ID, directory) by its History ID, for reading its files |
| `api.create_project(name, welcome, instructions, tool=None)`, `api.check_coordinator_tool(name, tool)`, `api.start_coordinator(name)` | Projects the plugin owns, and their Coordinators ([0_13_projects.md 6](0_13_projects.md#6-for-plugins)) |
| `api.store` | A JSON file at `~/.agentop/plugins/{id}/store.json` for the plugin's own data |
| `api.data_dir` | The plugin's own folder, `~/.agentop/plugins/{id}/`, made when first written to |
| `api.offer_transcript_archive(root)` | Offers a copy of the tools' transcripts, a folder per tool in each tool's own layout. While the plugin is on, the app finds a transcript there when the tool has deleted its own |
| `api.emit(data)` | Sends `{"type": "plugin", "id": …, "data": …}` on the events socket for the plugin's UI |
| `api.log` | A logger named `agentop.plugins.{id}` |

There is no API for driving agents. A plugin that drives agents uses the core's own functions (`ops.start`, `AgentInstance`, `Actor`), and the sessions it starts are ordinary managed sessions, which it can make `readonly` or not `counted`. A dialogue's orchestrator is a separate process and receives session status through the attention plugin's local socket.

Isolation here is fault isolation, not protection: a timed-out handler's thread keeps running, and an async route or task that blocks stalls the service ([backlog](9_backlog.md#plugins-say-what-isolation-means)).

### CLI commands

A plugin may add `agentop` commands. Its manifest names them, `cli = ["recall"]`, and a `cli.py` beside its `__init__.py` defines `register_cli(cli)` (`plugin_cli.py`).

- **Names in the manifest.** A disabled or failed plugin's commands can only be listed if their names are known without running its code. The CLI reads manifests and saved flags without starting the service. A missing plugin's command does not exist; a disabled, failed, or incompatible plugin's command is still listed, and running it says so and how to enable it.
- **`cli.py`, not `__init__.py`.** The CLI imports only `cli.py`, under a bare `agentop.plugins.{id}` package, so its relative imports work while `__init__.py`, the service's side, never runs. A plugin command starts as fast as a core one.
- **`cli.command(name, handler, **options)`** adds a command the manifest names and returns its parser, for arguments; `handler(args)` runs it. `options` are ArgumentParser's (`help`, `description`, `formatter_class`). `cli.data_dir` is the plugin's folder. A command the manifest doesn't name is refused.
- **All or nothing.** The parsers stay detached until `register_cli` returns, so a plugin that fails part way adds none of its commands. An exception in `cli.py` or `register_cli` turns each of its commands into one that says it failed and why.
- **Names.** Plugin commands are added after the core's, so a core command keeps its name, and of two plugins naming one command, the first in folder order keeps it. The core commands never depend on a plugin.

### Enable and disable

Python cannot reliably unload a module, so disabling is a gate rather than an unload:

- **Disable**: requests to its routes return 404, its event handlers stop being called, its tasks are cancelled, and its UI disappears. Its `on_disable` handlers are called. Before turning a plugin off, Status asks its UI (`beforeDisable`), which may answer with a warning for the user to confirm, such as "A dialogue is running".
- **Enable** of a plugin that was loaded: the gate opens again, and its tasks are started again.
- **Enable** of a plugin that was never imported (disabled at start) imports it then. If that fails, the state is `failed` and nothing else changes.
- **Retry** of a `failed` plugin needs a service restart; the UI says so. This holds for a plugin that failed while running too (a crashed task, repeated handler errors). This keeps half-initialised plugin state out of the running service.

Flags live in `~/.agentop/state/plugins.json`: `{"attention": {"enabled": false}}`. They belong to the service, not the client, because a plugin is a service feature: disabling it affects every client of that service.

### Routes

```
GET   /api/v2/plugins          → [{id, name, version, description, state, error, ui}]
PATCH /api/v2/plugins/{id}     {enabled: bool} → the updated entry
*     /api/v2/plugins/{id}/…   the plugin's own routes, 404 unless enabled
GET   /api/v2/auth/methods     no token: the service token, then each enabled login plugin
```

All require the token, except a login plugin's public routes and `/auth/methods`. `GET /api/v2/service` carries a `plugins` count, and every change to the list is pushed on the events socket as `{"type": "plugins"}`, so open clients update without polling. `/api/v2/health` stays plugin-free, so a connection's checks never depend on a plugin.

### UI

The web and desktop builds are static, and the desktop app has a strict script CSP, so a service never sends UI code. Plugin UI ships in the app build under `ui/src/plugins/`, and the app turns it on per service:

- After connecting, the app reads `GET /api/v2/plugins`, and again each time its events socket opens and on every `plugins` event. Plugins differ by service, so the list is kept per connection.
- A plugin's UI shows only when **both** the service lists it as `enabled` **and** the app has a matching module. If either side is missing (an older app, an older service, a removed or failed plugin), the feature is hidden.
- Plugin UI goes into fixed **slots** (`ui/src/plugins/types.ts`). A manifest naming any other slot is invalid. Core layout code renders each slot from the plugin list and never names a plugin.
- Each slot is wrapped in an error boundary. A plugin component that throws hides itself; the rest of the page keeps rendering.
- Plugin UI calls its own routes only through a client scoped to `/api/v2/plugins/{id}/`.
- A plugin's UI is `ui/src/plugins/{id}/index.tsx`, exporting `plugin`. Each component gets a context with the plugin's info, its scoped client, and `onEvent` for its `api.emit` messages. The app finds these modules at build time (`ui/src/plugins/registry.ts`).

| Slot | `plugin` key | Where it draws |
|------|--------------|----------------|
| `feature` | `feature` | A feature in the feature nav: a label, left and right panels, a background, the views its tabs show, and a pinned home view ([0_10_ui.md](0_10_ui.md#rules-for-plugins)) |
| `right-panel` | `rightPanel` | A section in Sessions' right panel |
| `session-badge` | `sessionBadge` | A badge on a session row |
| `settings-section` | `settingsSection` | A section in the service's Status, under Plugins |
| `file-viewer` | `fileViewer` | The contents of a file tab. While one is enabled, files in the right panel open in a tab; without one, clicking copies the path |
| `login` | `login` | A way to sign in, on the sign-in screen ([0_06_security.md 4](0_06_security.md#4-ways-to-sign-in)) |
| `terminal-input` | `terminalInput` | A strip below a session's terminal ([0_12_04_input.md](0_12_04_input.md)) |

**Status → Plugins** lists every plugin the service reported, with its description, version, state, and any error, and a switch to enable or disable it. A `failed` plugin shows its error and that a restart is needed to retry it. An `incompatible` one says which side is too old. A plugin the service has but this app doesn't know is listed as "Not available in this app".

### Tests

- The whole core suite (pytest and Playwright) runs with no plugins present. This is the proof that the core does not depend on them.
- A set of broken test plugins: one with a bad manifest, one that raises on import, one that raises in `register`, one whose route raises, one whose event handler raises or hangs, one whose task crashes, and one whose UI component throws. With all of them installed, the core suite still passes and each one shows the right state in the list.
- Disabling a plugin from Status removes its UI at once and its routes return 404; enabling brings both back.

## The plugins

### Projects (`projects`)

The owner's own projects. Each has a goal and a Coordinator session the owner talks to, which pursues it by starting the project's other sessions and routing work between them; the sessions share a folder in `~/.agentop/projects/` (a core mechanism). The Projects feature has the Project button (its New project buffer takes a name and the Coordinator's tool), the project list, and for the chosen project Sessions' own sections and right panel; picking a project opens its existing running Coordinator. Browsing never starts one; the owner asks the Coordinator to close. `agentop project create | close` act on its projects and need it on. Its own commands, `agentop coordinator …` (with `agentop coordinator task report` for a task's session), hold its Coordinators to their pursuit, and on the scan tick it logs a Coordinator turn that ended without a reason, and tells a Coordinator that cleared its context to carry on ([0_13_projects.md 4.3](0_13_projects.md#43-pursuit-tools-projects-plugin)). Turning it off stops its projects' sessions and keeps their folders. Everything else is in [0_13_projects.md](0_13_projects.md).

Routes: `GET`, `POST /projects`, `DELETE /projects/{name}`, `POST /projects/{name}/coordinator`, `GET /projects/{name}/pursuit`.

### Dialogues (`dialogue`)

Two agents work together on one task. An orchestrator passes messages between them, following a scenario. The Dialogues feature starts them with a wizard that fills in the scenario's brief, and shows both agents side by side (live and read-only while running, as transcripts after), with stats and artifacts on the right. Each dialogue is a project with its own Coordinator. `agentop dialogue` starts and manages them too. See [0_12_02_dialogue.md](0_12_02_dialogue.md).

### Memory (`memory`)

Keeps agentop's own copy of every agent transcript on the host, never deleted, then builds a searchable record of prompts and replies from it, with a model's summary of each session. The Memory feature searches it, opens a conversation at the hit, and shows the session's summary; `agentop recall` and the `recall` skill search it from a session. Searches and misses are noted, to decide on embeddings. See [0_12_03_memory.md](0_12_03_memory.md).

### Input bar (`input`)

A box below a session's terminal to type prompts in, with buttons for Esc, the arrows, Enter, Tab, Shift-Tab and digits, and ^B for tmux's prefix. While it shows, the terminal is only watched. It holds the draft outside tmux, so a prompt another session sends (a Coordinator's reply from `agentop send`) doesn't join it, and its prompts queue with `agentop send`'s in the service. Each device chooses between the bar and typing in the terminal. With reception on (by default), a prompt that looks like new work is held, and the bar offers to clear the session's context first. See [0_12_04_input.md](0_12_04_input.md).

### Waiting for input (`attention`)

For the old and new detection methods, measured cost, timing, and limitations, see [Detecting agent status](0_12_05_attention.md).

Managed Claude Code and Codex sessions emit lifecycle events into per-session files under `state/signals/`. The watcher marks a session waiting when a turn ends or the tool requests input; a new prompt or tool activity clears the mark. Screen changes alone do not clear a hook-based mark. The Projects checker subscribes to the plugin’s per-session feed. Codex approvals are checked against the screen because automatic review can emit a permission event without a prompt.

For unmanaged sessions, Antigravity, or sessions with no hook events, the watcher falls back to the screen: a change followed by four seconds of quiet is waiting. For a hook-based working turn, disappearance of a previously observed `esc to interrupt` footer for two scans also ends the turn. The fallback cannot identify every error or interruption. The first screen look and a resize are baselines. `GET /state` reports the waiting set and scan costs; turning the plugin off stops its watcher.

Use `python tools/test_status.py --session <tmux-name>` to watch a managed session's events, or `--probe claude` / `--probe codex` to run a short prompt with temporary configuration and copied credentials. The probe uses the signed-in account and reports the raw events and exit status; it does not test an interactive approval dialog.

### File viewer (`viewer`)

Fills the `file-viewer` slot: a file from the right panel (the working directory, the agent tool's memory folder, or a transcript) opens in a tab.

- **Text** with line numbers, coloured by language: 20 highlight.js grammars registered by hand through lowlight (`ui/src/plugins/viewer/highlight.ts`), the file's extension or name picking the language (`languageFor` in `paths.ts`). They live in a chunk of their own, fetched when a file is first opened. Files over 512 KB stay plain. `rehype-highlight` was tried and dropped: it bundles highlight.js's 37 common grammars whatever it is given.
- **Diffs.** A file from the Changed list opens as its diff against HEAD (staged and unstaged together, deleted and new files too, `GET /diff`), with Open file to see its text. The diff view is not coloured yet.
- **Markdown** opens drawn (tables, task lists, fenced code coloured with the same grammars, its images, and links to files beside it opening in tabs), with Raw beside it. Images come from `GET /image` as data, since an `<img>` can't carry the token.
- **Images** (PNG, JPEG, GIF, WebP, AVIF, BMP, ICO or SVG, up to 5 MB) open drawn in the middle of the tab.
- **Transcripts** open as a conversation (`GET /conversation`, the latest 5000 messages), with Raw beside it; each tool's `AgentCli.conversation_reader` reads them, so this works without the Memory plugin.

Only files in the session's working directory, its memory folder, or its transcript list can be read (`GET /file`).

### Email code (`email-code`)

A way to sign in without the service token, for a phone ([0_06_security.md 4](0_06_security.md#4-ways-to-sign-in)). The sign-in screen gets "Send me a sign-in code".

- **Settings** are in `~/.agentop/plugins/email-code/config.toml`, mode `0600` since it holds the SMTP password: `to` (the one address codes go to), `smtp_host`, `smtp_port` (465 means SSL, anything else STARTTLS), `smtp_user`, `smtp_password`, and optionally `from` and `smtp_security` (`ssl`, `starttls`, or `none`, which is only allowed for a mail server on loopback). They are read on each send, so a change needs no restart. Off by default; `agentop email-code test` sends a test mail.
- **`POST /send {name}`** (public) mails a 6-digit code and answers `{request, expires_in}` and nothing about where it went. Sending is limited for all callers together, as one proxy makes them look alike: once a minute, 5 an hour. Not set up answers 503, a failed send 502, with the reason only in the service log.
- **`POST /verify {request, code, name}`** (public) signs in an ordinary client named `name`. Only one code is pending at a time, and a new one replaces it. A code is good for 10 minutes, once, and only with its request ID, which only the asking browser got. 5 wrong tries end it. Guessing therefore takes asking for codes, which the send limit holds to 25 tries an hour against a million codes.
- The mail names the browser that asked, and says that ignoring a code nobody asked for is safe. The app keeps the pending request ID in `sessionStorage`, so a phone that reloads the page while you read the mail can still use the code.
- Whoever reads that mailbox can sign in. That is the trade for not copying the service token, and the mailbox's own protection (two-factor sign-in) is what guards it.
