# Agentop Design

The first doc to read. Each section says what a part of agentop is and why; where a part has a doc of its own, the section sums it up and links there. What is not built yet is in [9_backlog.md](9_backlog.md).

| Section | Detail in |
|---------|-----------|
| [1 Summary](#1-summary), [2 Goals and non-goals](#2-goals-and-non-goals) | |
| [3 Architecture](#3-architecture) | [0_03_architecture.md](0_03_architecture.md) |
| [4 Sessions](#4-sessions) | [0_04_start.md](0_04_start.md) (starting from a prompt) |
| [5 Terminal](#5-terminal) | |
| [6 Security](#6-security) | [0_06_security.md](0_06_security.md) |
| [7 Service lifecycle](#7-service-lifecycle) | [1_setup.md](1_setup.md) |
| [8 CLI](#8-cli) | [0_08_cli.md](0_08_cli.md) |
| [9 API](#9-api) | |
| [10 UI](#10-ui) | [0_10_ui.md](0_10_ui.md) |
| [11 Desktop app](#11-desktop-app) | [1_setup.md](1_setup.md#desktop-app) |
| [12 Plugins](#12-plugins) | [0_12_00_plugins.md](0_12_00_plugins.md) and a `0_12_<number>_<id>.md` per large plugin |
| [13 Projects](#13-projects) | [0_13_projects.md](0_13_projects.md) |
| Proposal: direct communication with agent tools | [0_14_direct-agent-communication.md](0_14_direct-agent-communication.md) |
| [14 Risks](#14-risks), [15 Decisions](#15-decisions), [16 Sources](#16-sources) | |

## 1 Summary

Agentop is an application for working with coding agents (Claude Code, Codex, Antigravity) that run in tmux, laid out like the Codex desktop app: a session list on the left, the agent's live terminal in the middle, session details on the right.

Agentop opens an agent in tmux, and the user works with it through that terminal. Around that:

- A **service** owns sessions and exposes an API. It runs independently of any UI.
- A **web app** and a **Tauri desktop app** show one UI codebase, with the same look and features. Both show the UI that a service serves. The desktop app is a thin wrapper that runs this computer's service while it is open ([11](#11-desktop-app)).
- Both connect to the local service by default, can add and switch to any agentop service they can reach, and can see and control the status of the connected service.
- **Plugins** add features on top of the core ([12](#12-plugins)), and **projects** give a goal to a Coordinator session that drives other agents to reach it ([13](#13-projects)).

There is no agent-level streaming, chat view, or message parsing. The terminal is a real `tmux attach` client, so all interaction is whatever tmux and the terminal library already support.

## 2 Goals and non-goals

### 2.1 Goals

- Start a session from the UI (tool, working directory, name), with a visible loading state until the terminal is ready.
- Show the attached tmux terminal in the middle panel.
- Three-column layout with collapsible left and right panels.
- Web and desktop look and behave the same.
- The service runs with no UI open, and sessions outlive both the UI and the service.
- The UI can read service status and stop, start, restart, and update it.
- The UI can add, connect to, and switch between services, on this machine or any other reachable machine.
- On a PC, opening the desktop app is all it takes: it starts this computer's service on loopback and opens signed in. Closing it stops that service and leaves every session running in tmux.
- Both clients show when an update is available. The desktop app updates its copy of agentop in place, and a new major version of the app is a download from GitHub Releases ([7.4](#74-releases-and-the-update-indicator)).

### 2.2 Non-goals

- Replacing the terminal with a custom chat or agent-event UI.
- Running agents anywhere other than tmux.
- Windows as a host. tmux does not run there natively.
- Multi-user access. One service serves one OS user.
- A merged session list across services. One window shows one service at a time.
- Proxying one service through another. Clients talk directly to the service they select.
- Bundling the service's code inside the desktop app. The app runs its own installed copy of `agentop` ([11](#11-desktop-app)).

## 3 Architecture

The overview diagram, the repository layout, the technology choices, and every module are in [0_03_architecture.md](0_03_architecture.md).

Four rules hold it together:

1. **tmux owns the running state.** The service can restart at any time without touching an agent.
2. **The service owns the API.** UIs hold no business logic and never touch tmux, the filesystem, or `~/.claude` directly.
3. **The UI is disposable.** Closing it never stops an agent. Closing the web app stops nothing. Closing the desktop app stops the service only if the app started it, and tmux keeps every session.
4. **A UI is not tied to a service.** Either client can point at any reachable service ([3.2](#32-connecting-to-services)). This applies to the web app too, even though a service happens to serve it.

### 3.1 Two ways to run a service

The service is the same program in both.

- **On a server**, it is a service in its own right: `agentop serve` by hand, or a systemd unit. No UI is involved in its lifetime ([1_setup.md](1_setup.md)).
- **On a PC** (Linux, macOS), the desktop app runs it while the app is open ([11](#11-desktop-app)).

### 3.2 Connecting to services

A **connection** is a record kept by the client, never by a service:

```jsonc
{
  "id": "local-uuid",
  "label": "build-box",
  "url": "http://build-box:9775",   // base URL, may include a path such as /agentop
  "token": "…"                      // desktop: OS keychain; web: localStorage
}
```

Every service has a `name` (default: hostname), set on first run and returned by `/service`. A host runs one service, so the address is the identity: whatever answers there is the service the user wants. The app pins no ID to it. If a different service now answers, for example after a reinstall or a reused tunnel port, it doesn't know this app's client token, so the app asks to sign in again.

**Defaults.** Both clients always list the service that served the page, under that service's name. For the desktop app that is this computer's service, which it signs in to with `service.json`. Each client starts on its last-used connection, or on that default.

**Adding a service.** The user gives a URL. Testing it only asks `/health`, which needs no token, to check that an agentop service answers. The app then adds it signed out, so it shows the service's ways to sign in (login plugins, or the service token) like any signed-out connection. `agentop service connect-info` prints the URL and the service token as one pasteable string, `http://host:9775#token=…`. The app accepts it in the URL field and signs in with that token on the first connect. A fragment is never sent to a server, which keeps the token out of logs. `agentop open` uses the same format, and the app stores the token and clears it from the address bar.

Connecting runs these checks in order, and each failure has its own message:

| Step | Failure shown to the user |
|------|---------------------------|
| 1. `GET /api/v2/health` | "Can't reach this service" with likely causes. A browser reports network, CORS, and mixed-content failures as the same error, so the app cannot tell them apart |
| 2. Response is agentop | "This is not an agentop service" |
| 3. Sign in with the service token, if the app has no live client token | "Enter the token", "Token rejected", or "Refused for now" |
| 4. `GET /api/v2/service` with the token | "Signed out" |

**Versions.** The path is the compatibility promise: `/api/v2` only ever gains routes and fields, and an incompatible change gets a new path, `/api/v3`. So a client never refuses a v2 service over its version. `/service` returns `api_version`, the revision of `/api/v2`, which grows with each addition. When it is higher than the revision the app was built for, the header shows that the app is out of date: the web app offers a reload, which loads the newer app from the service. In the desktop app the message is that this computer's service is older than the one shown, and should be updated. A client newer than its service must cope with routes the service doesn't have yet, as the `/admin` fallback in [7.3](#73-update) does.

**Switching.** The app closes the events and terminal sockets for the old connection, shows a loading state, then runs the same checks on the new one. This only detaches clients, so every agent on the old service keeps running. Each connection remembers its selected session. All client state is keyed by `(connection id, session id)`, because session IDs are unique only within one service.

**Tabs.** The saved list is shared across a browser's tabs. The active connection is per tab, so two tabs can show two services.

**Reachability.** Both clients follow browser page rules, since both show a page from a service. The desktop app's page is always from `http://127.0.0.1`, the first row below, which can reach everything. For the web app, which targets work depends on where the page was loaded from:

| Page loaded from | Target `http://` remote | Target `https://` remote | Target loopback |
|------------------|-------------------------|--------------------------|-----------------|
| `http://127.0.0.1` | Works | Works | Works |
| `https://` host | Blocked as mixed content | Works | Chrome asks for Local Network Access permission; Safari has been reported to block it |
| `http://` non-loopback host | Works | Works | Blocked or unreliable: the permission can only be requested from a secure context |

So the page from a local service (or a tunnel to one) can reach everything, and that is the recommended way to use the web app. Check S4 ([backlog](9_backlog.md#to-check-by-hand)) confirms this table. When a target is likely to be blocked, the Connect to a service form says why before the user tries.

The web build has no hard-coded origin, so it can also be hosted as static files. A page served by a service still disappears when that service stops, and any other saved connection then becomes unreachable from that tab as well. The desktop app has the same limitation, but it can start this computer's service again.

## 4 Sessions

### 4.1 Identity and status

**A session's `id` is its tmux session name**, chosen before tmux is called and never changed. It has the form `{name}-{rand6}`, for example `fix-auth-a1b2c3`, so the loading row and the running session are the same object from the first moment. The human name is `display_name`, unique within the session's project ([13](#13-projects)) and editable without touching tmux.

`name` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`. It never contains `.` or `:`, which are tmux target separators.

```
              process found, startup done
  starting ──────────────────────────────► running ──┐
     │                                        │      │ agent process exits,
     │ tmux failed, or no process in time     │      │ tmux session remains
     ▼                                        │      ▼
   failed                        tmux gone    │   exited
     │                                        ▼      │
     │ dismissed                                     │
     └─────────────────────────────────────► ended ◄─┘
```

A failed session stays listed until it is dismissed, even if its tmux session has gone, so that its error is not lost.

| Status | Meaning | Middle panel |
|--------|---------|--------------|
| `starting` | Create and launch in progress; `step` gives detail | Loading state |
| `running` | Agent process alive | Terminal |
| `exited` | Agent quit, its shell remains in tmux | Terminal, with a banner offering Resume or Close |
| `failed` | Start did not succeed; `error` says why | Error message, plus the terminal if tmux exists, so the user sees `command not found` |
| `ended` | tmux session gone | Removed from the list; appears in History |

Sessions agentop started are **managed**. Agents found running outside agentop (`managed: false`) are in `GET /sessions` and `agentop list`, but the app doesn't show them. Only managed sessions can be stopped or sent prompts. The plugin that starts a session can make it `readonly` (watched only, takes no prompts) or not `counted` (outside `max_active_sessions`), as a dialogue's agents are.

### 4.2 Start flow

```
UI                        Service                       tmux
 │ POST /api/v2/sessions    │                             │
 │ {tool, cwd, name}        │ validate; record starting   │
 │◄── 202 {id, starting} ───┤                             │
 │ add row, show loading    │ background task:            │
 │                          │  new-session ───────────────►
 │◄ event step=launching ───┤  send launch command ───────►
 │◄ event step=waiting ─────┤  poll for agent process     │
 │◄ event step=confirming ──┤  post_start_hook            │
 │◄ event status=running ───┤                             │
 │ WS attach ───────────────► tmux attach ────────────────►
 │◄════ terminal bytes ═════╪═════════════════════════════╡
```

- Validation errors (unknown tool, missing directory, bad name) return 4xx synchronously. Everything slower happens after the 202.
- `AgentCli` takes an optional `on_progress(step)` callback and keeps its synchronous contract, so `agentop start` and dialogues use the same code.
- `post_start_hook` stops as soon as the agent is ready (its ready marker, the input prompt, is on screen), not only when it sees a trust prompt. A 10s timeout is the fallback. With real Claude Code, `POST` returns in about 40ms and the session is `running` at about 1.4s.
- Start timeout: 30s without an agent process sets `failed`, or within a second or so when the shell reports the command is missing. The tmux session stays open for inspection until the user closes it.
- Resume uses the same endpoint and flow: `POST /api/v2/sessions` with `{resume: {session_id}, tool, cwd, name}`.
- If the service restarts during a start, the record is marked `failed` on boot, unless reconciliation finds the agent alive.
- Every Claude session runs with `--dangerously-skip-permissions`, and in the `default` project also with `--remote-control` ([0_06_security.md 3.1](0_06_security.md#31-what-the-service-allows)).

### 4.3 SessionStore

`SessionStore` (`service/store.py`) merges three sources:

| Source | Provides |
|--------|----------|
| tmux | Whether a session exists |
| `scanner` | Agent process, PID, memory, runtime |
| Registry | Managed flag, project, tool, cwd, display name, description, status, flags, created time |

A background tick (2s; a scan of a dozen sessions costs about 0.12s) reconciles the sources and emits change events. `POST /sessions` writes to the store immediately, so a new session is attachable the moment it exists. The terminal endpoint checks tmux directly and never trusts a cached list. Sessions started by the CLI or a dialogue appear on the next tick. `AgentCli` metadata (token usage, AI title) is refreshed on a slower 10s tick.

### 4.4 Registry

One JSON file per project, `~/.agentop/projects/<project>/.project/sessions.json`, keyed by tmux session name, under one lock file, because the CLI and the service both write. Fields beyond the basics: `display_name`, `status`, `step`, `error`, `created_at`, `ended_at`, the agent's own session ID, and the plugin flags. The details are in [0_03_architecture.md 4.2](0_03_architecture.md#42-registry-registrypy).

### 4.5 History

The app's History lists only what is worth reopening: sessions agentop started, used recently.

- **What is listed.** A past session is a managed one, not `readonly`, no longer live, whose tool still has its transcript, last written within `history_window` (`config.toml`, default `"30d"`). The tools' own files don't say who started a session, so agentop's registry marks a session as agentop's, with the tool's own session ID (`agent_session_id`):
  - Claude is started with an ID agentop picks (`--session-id <uuid>`), recorded at launch.
  - Codex and Antigravity take no ID at launch. agentop records the agent's pid and start time; while it runs the scanner reports its ID, and if it ended unseen (the service was down), History finds it from the tool's own records, which outlive the process: Codex's `logs_2.sqlite`, and the Antigravity CLI log of that run.
  - A resume records the ID it resumes. The record keeps the latest ID the scanner reports, so `/clear` is followed.
- **Resuming keeps the name**: the session comes back under the same record and name. If a live session in the project has taken the name meanwhile, Resume asks for another.
- **What a transcript holds** (`GET /history/{id}/stats`): the prompts and replies of the main conversation, tokens summed over every file the tool keeps for it, subagents included, and the files' size on disk, from the Memory archive when the tool has deleted them. Each tool's `AgentCli.token_usage` reads its own records.
- **The active-session limit.** At most `max_active_sessions` (default 20) counted sessions agentop started are active at once, in every state until closed. The service refuses a start or resume past it (409), and the app disables Resume. A start from Sessions' start buffer first closes the `default` session idle longest ([4.7](#47-starting-from-a-prompt)).
- `agentop history` (the CLI) still lists every saved session of every tool; only the app narrows.

How History looks in the app is in [0_10_ui.md](0_10_ui.md#sessions).

### 4.6 Sending between sessions

`agentop send <target> "<prompt>"` types a prompt into another live managed session and submits it, as if the user had typed it there ([0_08_cli.md 1.4](0_08_cli.md#14-send)). The `handoff` skill uses it to pass a side task, such as a deploy, from a session with expensive context to another session, so the side task's logs and retries stay out of the first one. Nothing comes back by itself: the user switches to the target to follow it. When the sender needs a result, the prompt asks the target to reply with its own `agentop send`, in a line or two, and to ask for nothing back.

**Transport: tmux only.** The prompt is typed into the target's terminal the way each tool takes a paste (`AgentCli.deliver`, a bracketed paste then Enter). Agentop does not call any LLM API, and does not run work headless (`claude -p`, `codex exec`); the headless uses are short judgements about a session, never its work: the Memory plugin's summaries, the input bar's reception, and Sessions' start probe ([4.7](#47-starting-from-a-prompt)). Why:

- The agent CLIs are built to run in a terminal, and tmux is the native way to run them. Agentop's job is to bring those tmux sessions into one window, not to become a different agent runtime.
- Every handoff runs in an ordinary session: the user can see it, attach, answer questions, take over, or stop it. Nothing runs hidden.
- Billing, login, and permissions stay exactly as they are for interactive use. Calling an LLM API needs a separate API key, and headless runs follow their own rules: on Claude plans, `claude -p` usage is billed to a separate monthly Agent SDK credit ([16](#16-sources)).
- A new agent CLI works with no new integration, because it is just another terminal program.

**One queue.** `agentop send` goes through the service, `POST /api/v2/send` (it types the prompt itself only when no service answers), so it and the Input bar type in one process, where a lock per tmux session makes a prompt wait for the one before it and never land between another's paste and its Enter.

A prompt sent to a busy session is queued by the tool itself. Delivery only when the target is idle was considered and is on hold ([backlog](9_backlog.md#declined-or-on-hold)); a draft the user is typing is kept apart by the Input bar ([0_12_04_input.md](0_12_04_input.md)).

### 4.7 Starting from a prompt

Sessions starts a session from its first prompt. The owner types it in the **start buffer**, the pinned first tab of Sessions' left area (there is no New session), and a one-shot cheap-model call, the **probe**, gives a title and name, how hard the task is (a tier), the first prompt (the prompt less any words that only ask for the session), and the working directory, from the folders of recent sessions and the repositories under the usual roots. Code then picks the tool and model for that tier, by the owner's order and the quota each tool has left, unless the prompt names them. The row shows the choice for 3 seconds, to be changed before an agent starts working, then the session starts with the prompt and opens in a tab beside the buffer. On a timer, a `default` session idle for 24 hours is closed, and a start at the limit closes the one idle longest. Projects are untouched. It is also a trial of choosing a tool and model per task, for the Coordinator later, so every choice and the owner's changes to it are recorded. The detail is in [0_04_start.md](0_04_start.md).

## 5 Terminal

### 5.1 PTY bridge

One WebSocket equals one `tmux attach-session` client on a PTY the service owns.

```
tmux -u attach-session -t =<id>      TERM=xterm-256color, COLORTERM=truecolor
```

- `TERM` is set on attach: with it unset, attach fails (`terminal does not support clear`).
- The client passes `cols` and `rows` in the URL, so the PTY starts at the right size and the screen doesn't reflow.
- The master fd is non-blocking and read through `loop.add_reader`, so no thread blocks per read.
- Disconnect closes the PTY and awaits the client process. Detaching never affects the session.
- Resize sends `SIGWINCH` to the client directly. The kernel only signals a tty's controlling process group, which the attach client is not, so setting the size alone does nothing.
- Backpressure: the bridge stops reading while a send is pending, and tmux handles a slow client by discarding output and redrawing, so the web layer needs no ack protocol. With `yes` running and the client not reading, `/health` stays responsive, and after Ctrl-C the terminal catches up in about 0.1s.
- A `readonly` session, or `readonly=true` in the URL, attaches with `-f read-only,ignore-size`, drops what the client sends, and reports the window's size in a `{"type": "size"}` frame; the terminal then scales its font to show the whole window.

| Direction | Frame | Meaning |
|-----------|-------|---------|
| Both | Binary | Raw terminal bytes |
| Client to server | Text `{"type":"resize","cols":N,"rows":N}` | Resize the PTY |
| Server to client | Text `{"type":"closed","reason":"session_ended"}` | tmux session ended, or `detached` if the client detached |
| Server to client | Text `{"type":"size","cols":N,"rows":N}` | A read-only attach: the window's size |

Close codes: `4401` invalid ticket, `4403` client revoked or expired, `4404` no such session. Any other close triggers reconnect with backoff (0.5s up to 10s), which also carries the terminal through a service restart.

### 5.2 Terminal library: xterm.js 6

| Library | Assessment |
|---------|------------|
| **xterm.js 6** with `addon-webgl` | **Chosen.** Largest production track record (it powers VS Code); full addon set |
| ghostty-web | **Fallback candidate.** Pre-1.0, xterm-compatible API, WASM parser; does not depend on WebGL, which matters for WebKitGTK |
| wterm | Not suitable as the primary choice: younger, and DOM rendering is not aimed at heavy TUI repainting |

Addons: `webgl`, `fit`, `search`, `web-links`, `unicode11` (CJK and emoji width), `clipboard` (OSC 52).

- **xterm 6 has no canvas renderer**, so the only fallback from WebGL is DOM.
- **WebKitGTK is the weak spot.** Tauri's Linux graphics documentation warns that WebGL can silently land on a slow path, and recommends a non-WebGL fallback on Linux.

So all terminal code sits behind one `TerminalView` component with a `renderer: auto | webgl | dom` setting (Settings → terminal drawing), and check S1 ([backlog](9_backlog.md#to-check-by-hand)) decides the Linux desktop default. If neither xterm renderer is acceptable there, `TerminalView` makes ghostty-web a contained swap.

### 5.3 tmux integration

- **Exact targets everywhere.** All tmux calls go through one wrapper (`tmux.py`). Session commands use `=<id>`, pane commands `=<id>:` (`send-keys` and `set-option` reject the form without the colon). tmux prefix-matches a plain target, so `-t general` would match `general-41185` and act on another session.
- **The user's own tmux server is used**, so `tmux attach` from any terminal still works and the session appears there too. The wrapper also supports a private socket, so tests never touch live sessions.
- **Scrolling.** With the global `mouse` off, the wheel cannot reach tmux scrollback, so agentop sets `mouse on` per session at creation, leaving the global option and other sessions unchanged. The cost is that selection is captured by tmux, so plain-text selection uses Shift+drag, and tmux copies reach the system clipboard over OSC 52 and `addon-clipboard`. Check S3 tests this path.
- **Sizing.** With tmux's default `window-size latest`, the window follows the most recently active client: the size follows whoever is working.
- **Scrollback is tmux's.** xterm's own buffer is small, because tmux replays the screen on attach.
- **Few live terminals.** Each tab group keeps its last 3 sessions attached but hidden, so going back to one is instant; each holds a WebGL context and a socket, and a browser allows about 16 contexts ([0_10_ui.md](0_10_ui.md#tabs)).

## 6 Security

The rules, how each client signs in, the ways to reach a service from elsewhere, and the ways to sign in are in [0_06_security.md](0_06_security.md).

The terminal socket is remote code execution as the user, with agents running in skip-permissions mode, and any origin must be able to connect. So:

1. **Loopback by default.** Exposing a service is an explicit choice, and plain HTTP on any other address is refused ([D6](#15-decisions)).
2. **The token decides, not the origin.** Every route but a few for signing in needs a client's token. The service token only signs clients in; each client's token expires and can be revoked ([D7](#15-decisions)). There are no cookies, so CORS is open and there is no `Origin` allowlist.
3. **Reach is a layer of its own.** For one person's own devices, the recommended way in from elsewhere is Tailscale with `tailscale serve`, which keeps the service on loopback and lets only those devices reach it ([0_06_security.md 3.2](0_06_security.md#32-choosing-a-route)).
4. **Claude remote control is a way in outside agentop**, on purpose, for Claude sessions in `default` ([0_06_security.md 3.1](0_06_security.md#31-what-the-service-allows)).

## 7 Service lifecycle

How to run, expose, and update a service is in [1_setup.md](1_setup.md); the commands are in [0_08_cli.md](0_08_cli.md#3-service-commands).

agentop provides the program, and `agentop serve` runs it. Running it as a system service, behind a proxy, or with TLS is up to the machine's owner, and nothing in agentop requires it. A systemd unit should set `KillMode=process`, so that stopping the service never takes tmux with it, and `TERM=xterm-256color`.

### 7.1 ServiceManager

```python
class ServiceManager(Protocol):
    def status(self) -> ManagerStatus: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...
```

- `SystemdManager` is used when the service is its unit's main process. It runs `systemctl --user`. Restart uses `--no-block`, so the service does not wait on its own death.
- `SelfManager` is used when the service was started with `agentop serve` by hand or by the desktop app. Stop ends the process. Restart sends it `SIGUSR1`: it shuts down cleanly and `exec`s the same command line, so it keeps its PID, terminal, and arguments, and runs whatever code is installed now. Not `SIGHUP`, which closing its terminal sends.
- `NoManager` is for a process that isn't `agentop serve`, such as tests. It reports the capabilities as false, and the UI disables those buttons.
- `LaunchdManager` would be added if macOS hosts without the desktop app become a target.

### 7.2 Who can do what

Stop, restart, and update are admin actions on the service's own API: `POST /api/v2/admin/stop`, `/admin/restart`, and `/admin/update`. Every client calls them the same way, the web app, the desktop app, and the CLI, whether the service runs under systemd or not. The service knows which (7.1) and runs the right command on itself.

A stopped service has no API to answer it, so no client can start a service through it. The desktop app is the one exception, for this computer's service only: it runs that service, so it shows Start. Otherwise, when a service is stopped or can't be reached, the main window shows a red dot and says how to start it on that machine: `agentop serve`, or the system service that runs it.

Stopping the service does not stop agents. The confirmation dialog says so and names the service being stopped, and the sessions are there on restart.

### 7.3 Update

An install is a git checkout, the only install kind update supports. `GET /api/v2/service` reports `install_kind`; other kinds report update as unsupported.

Update must outlive the service it restarts, so it runs apart from it: in a transient unit under systemd (`systemd-run --user --unit=agentop-update --collect`), since stopping a unit can take its children with it, and otherwise as a detached process whose PID is kept in `update.pid`. The service passes it the command that restarts it: `systemctl --user restart` for a unit, or a signal for a service started by hand. That command waits until the old process stops answering, and if the process is gone because the new version failed to start, starts it again in the background with the same command line, logging to `serve.log`.

`src/agentop/service/update.sh` follows `update_from` (`main` or `release`, [7.4](#74-releases-and-the-update-indicator)) and writes progress to `~/.agentop/state/update-status.json`:

1. Refuse if the tree is dirty, has commits not on the remote, or, following `main`, is not on `main`.
2. Fetch, then fast-forward `main`, or check out the newest `vX.Y.Z` tag (never backwards: an install ahead of the newest tag waits for a newer one).
3. `pip install -e .`, then build the web app, but only when `ui/` changed between the two commits, and `npm ci` only when its lockfile did.
4. Restart the service, then poll `/api/v2/health` for 30s.
5. On any failure: check out the recorded commit, reinstall, rebuild, restart, and record the failure.

`GET /api/v2/admin/update` reports commits available and the status of the last run. Terminals drop briefly and reconnect automatically; agents are not interrupted.

`/health`, `/service` and `/admin/update` stay compatible across versions, so that an older service can always be updated from a newer client. A service from before `/admin` has these actions under `/service/`, and the client falls back to them on a 404.

### 7.4 Releases and the update indicator

agentop has one release series: `v*` tags, such as `v2.1.0`, made by `scripts/release.sh` ([1_setup.md](1_setup.md#building-and-releasing)). A tag marks the code, and is all a release is. Only for a new major version, `vX.0.0`, does `.github/workflows/desktop.yml` also publish the desktop app built from it as a GitHub release, **agentop-desktop vX.0.0**: AppImage (Linux), `.app` (macOS). The tag must match the version in `pyproject.toml`, `desktop/src-tauri/tauri.conf.json` and `Cargo.toml`, which the script sets together.

**Versions say what an update needs.** In `vX.Y.Z`:

| Part | Changed | The desktop app | An open page |
|------|---------|-----------------|--------------|
| X | The wrapper (`desktop/`) | **App update** opens the release page: a download and a reinstall | Shows **New version: reload** |
| Y | The UI (`ui/`) | **Update agentop** updates this computer's agentop in place | Shows **New version: reload** |
| Z | Only the service | **Update agentop**, as for Y | Shows **New version: reload** |

`scripts/release.sh` with no argument picks the part from the paths changed since the last tag: `desktop/`, then `ui/`, then anything else.

**What a service follows.** `update_from` in `config.toml`, or `agentop serve --update-from`:

- **`main`**, the default, for development and servers: Update fast-forwards `main`.
- **`release`**, which the desktop app's own copy uses: Update checks out the newest `vX.Y.Z` tag (never a pre-release), only when it is newer than what runs.

**Who finds an update.** The UI asks only the service that served the page: in the desktop app this computer's service, in a browser the one at the page's own address. Other connected services are never asked; each one's Status has its own Update.

The service answers `GET /admin/version`: `current`, what it runs, and `latest`, what Update would bring, in the same form, so that equal means nothing to do. Following releases they are `X.Y.Z` (`current` from the checkout's `pyproject.toml`, `latest` the newest tag on the git remote); following `main` they are 10-character commits (`HEAD`, and `main` on the remote). `current` is read once, when the service starts; the remote is asked at most once an hour. The answer also carries `ui_build`, the entry script of the app the service serves now.

The UI keeps the first answer it gets as the page's own version, and asks again hourly and whenever the service restarts. Then, in this order:

1. **The page is out of date** when the service's `current` or `ui_build` differs from the page's. Both clients show **New version: reload**; the page never reloads by itself.
2. **In the desktop app, on a service following releases**, the UI asks the wrapper for its own version (`desktop_info`): a `latest` with a higher X than the wrapper is **App update**, which opens the release page; a `latest` above `current` is **Update agentop**, which updates this computer's agentop in place ([11](#11-desktop-app)).
3. **Otherwise**, a `latest` that differs from `current` shows **agentop X.Y.Z is out** (or **New commits on main**), which opens that service's Status, where Update is.

The web app's header has no one-click Update yet ([backlog](9_backlog.md#service-and-ui-updates-in-the-header)).

## 8 CLI

Every command and option is in [0_08_cli.md](0_08_cli.md). The CLI is a peer of the app, not a client of it:

- **Session commands** (`list`, `start`, `stop`, `send`, `history`) call the same `ops` functions the service does, and work with no service running. `send` goes through the service when one answers, so its prompts queue with the app's ([4.6](#46-sending-between-sessions)).
- **Service commands** (`serve`, `open`, `service status | stop | restart | update | token | clients | connect-info`) run the service or ask it. They sign in with the service token from `service.json`, for 60 seconds per command ([0_06_security.md 2](0_06_security.md#2-how-each-client-signs-in)).
- **Project commands** (`project list | show | create | close`): create and close act on the Projects plugin's projects, and need it on. Only a project's Coordinator closes it, once no task is open: it marks the track, drops the entry and ends its own session, keeping everything else, which create loads again; agentop never deletes a project's folder ([13](#13-projects)).
- **Plugin commands** (`dialogue`, `recall`, `email-code`) come from plugins and never start the service; a disabled plugin's command says so ([0_12_00_plugins.md](0_12_00_plugins.md#cli-commands)).
- Agents are a main user of the CLI: a Coordinator starts and reaches its project's sessions with `agentop start` and `agentop send`, and the skills `agentop serve` installs (`handoff`, `recall`) run it ([src/agentop/skills/](../src/agentop/skills/README.md)).

## 9 API

All routes are under `/api/v2`. Plugin routes are under `/api/v2/plugins/{id}/`, listed in each plugin's doc.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | `{"status": "ok"}` and nothing else (no auth) |
| `GET` | `/service` | Name, version, `api_version`, commit, uptime, PID, tmux version, manager, capabilities, `install_kind`, whether TLS is on, plugin count |
| `POST` | `/admin/restart`, `/admin/stop` | Restart or stop this service ([7.2](#72-who-can-do-what)) |
| `GET`, `POST` | `/admin/update` | Check and status; start an update |
| `GET` | `/admin/version` | What the service runs and what Update would bring ([7.4](#74-releases-and-the-update-indicator)) |
| `GET`, `POST` | `/auth/methods`, `/auth/clients` | Ways to sign in (no auth); sign a client in with the service token ([0_06_security.md 1](0_06_security.md#1-rules)) |
| `POST` | `/auth/renew` | A new client token |
| `GET`, `DELETE` | `/auth/clients`, `/auth/clients/{id}` | List signed-in clients; revoke one |
| `POST` | `/tickets` | Issue a WebSocket ticket |
| `GET` | `/tools` | Supported tools, and whether each is installed |
| `GET` | `/fs/dirs?path=` | Directory listing for the working-directory picker |
| `GET` | `/sessions` | List |
| `POST` | `/sessions` | Create or resume; returns 202 |
| `GET`, `PATCH` | `/sessions/{id}` | Detail; set `display_name`, `description` |
| `POST` | `/sessions/{id}/stop` | Graceful stop, as `agentop stop` |
| `DELETE` | `/sessions/{id}` | Kill tmux and remove an `exited` or `failed` session |
| `GET` | `/sessions/{id}/files?path=` | One directory of the working tree |
| `GET` | `/sessions/{id}/changes` | Git branch, dirty state, changed files |
| `GET` | `/sessions/{id}/memory`, `/sessions/{id}/transcripts` | The agent tool's notes for the project; its transcript files |
| `POST` | `/send` | Type a prompt into a session, as `agentop send` ([4.6](#46-sending-between-sessions)) |
| `GET` | `/history` | Past sessions agentop started, used within `history_window`, and the active-session limit ([4.5](#45-history)) |
| `GET` | `/history/{id}/stats`, `/history/{id}/conversation` | What a past session's transcript holds; its main conversation |
| `POST`, `DELETE` | `/history/{id}/resume`, `/history/{id}` | Resume a past session (409 at the limit), or delete its transcript from its tool |
| `GET` | `/projects`, `/projects/{name}` | Projects ([13](#13-projects)) |
| `POST` | `/projects/{name}/coordinator` | The project's Coordinator: running, or resumed or started now |
| `GET`, `PATCH` | `/plugins`, `/plugins/{id}` | The plugin list; enable or disable one ([0_12_00_plugins.md](0_12_00_plugins.md#routes)) |
| `WS` | `/ws/events?ticket=` | Change stream |
| `WS` | `/ws/sessions/{id}/terminal?ticket=&cols=&rows=` | Terminal ([5.1](#51-pty-bridge)) |

The working-directory picker uses `/fs/dirs` rather than a native dialog, because the path is on the service's machine, which may not be the machine showing the UI. It also keeps web and desktop identical.

The events socket sends a full `snapshot` on connect, then `session.upserted`, `session.removed`, `service.updated`, `plugins`, and a plugin's own `plugin` events. Reconnecting simply yields a new snapshot, so the client needs no replay logic. Events of a type the app doesn't know are ignored.

Session object:

```jsonc
{
  "id": "fix-auth-a1b2c3",
  "display_name": "fix-auth",
  "description": "",
  "tool": "claude",
  "status": "running",            // starting | running | exited | failed
  "step": null,                   // creating | launching | waiting | confirming
  "error": null,
  "managed": true,
  "project": "default",           // null for an agent started elsewhere
  "readonly": false,
  "counted": true,
  "attachable": true,
  "cwd": "/home/me/project",
  "created_at": 1794012345,
  "pid": 12345,
  "runtime_seconds": 300,
  "memory_mb": 412.5,
  "ai_title": "Fix auth token refresh",
  "session_id": "…",
  "token_usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  },
  "bridge_url": null,
  "tmux": { "session": "fix-auth-a1b2c3", "clients": 1 }
}
```

## 10 UI

The layout, the name of each part, where tabs open, the keys, and the rules for plugins are in [0_10_ui.md](0_10_ui.md).

- **One fixed layout.** A header (left panel toggle, connection manager, feature nav, Settings, right panel toggle), then a left panel, a middle panel, and a right panel. The middle panel shows a feature's background, or one or two areas of tabs; each tab shows a buffer (a session's terminal, a file, a past session, a service's Status, a plugin's view). Plugins fill this layout and never add parts to it.
- **Features.** Each connection offers a set of top-level features: Sessions (core, the `default` project), and from plugins Projects, Dialogues, and Memory. Each has its own panels, background and tabs, and keeps its tabs while another feature shows.
- **Keys.** Ctrl+Shift with a physical key reaches every part by position, ahead of the terminal; they avoid anything a TUI needs, Ctrl+B (tmux's prefix) included.
- **Settings and Status.** Settings is the app's own (theme, terminal font size and drawing). Status belongs to a service and opens as a tab: what it is, Stop, Restart, Update, Forget, signed-in clients, and its plugins.
- **One codebase, two shells.** `ui/src/platform/` is the only code that differs between web and desktop ([0_03_architecture.md](0_03_architecture.md#7-ui-layer)).

## 11 Desktop app

How to install, run and build it is in [1_setup.md](1_setup.md#desktop-app).

The desktop app is a thin wrapper around this computer's service:

1. **On open**, it checks whether a service answers `GET /api/v2/health` on the configured port (`config.toml`, default 9775).
   - If one does (a unit, or `agentop serve` by hand), it uses it and **leaves it running** when it closes.
   - Otherwise it starts its own copy, `~/.agentop-desktop/venv/bin/agentop serve --host 127.0.0.1 --port <port> --update-from release`, through the user's login shell (`$SHELL -ilc 'exec … serve …'`), because an app opened from the desktop gets a short `PATH` that may lack tmux and the agent tools. Without the copy, the start page runs first-run setup. `AGENTOP_BIN` overrides it and skips setup. Output goes to `~/.agentop/logs/desktop-serve.log`. It always binds loopback.
   - The app shows a start page, bundled in the app, until the service answers. If it doesn't start, the page says why and offers Try again and Run setup again.
2. **Once the service answers**, the window opens the service's own page, `http://127.0.0.1:<port>/`, signed in with the token from `service.json`.
3. **On close**, it sends the service it started `SIGTERM`. The service closes its terminals, which has a 5-second limit, and exits; `SIGKILL` follows after 10 seconds. tmux owns the sessions, so they keep running, and the next open finds them.

The app keeps the PID of a service it started in `~/.agentop/state/desktop-serve.pid`. A copy of the app that finds a service answering and that PID alive adopts it, and stops it on close: an app that replaced itself, or one that crashed.

Restart and update from Status work as for `agentop serve` by hand (`SelfManager`, [7.1](#71-servicemanager)). A restart `exec`s in place, so the PID, and the app's hold on it, stay the same. Stop shows "The service on this computer stopped", with a Start button.

Because the UI comes from the service, it is always the service's own version, and a service update updates the desktop UI with it. Only the wrapper, a few hundred lines of Rust, is built into the app, and it rarely changes. A new wrapper is a new major version and a download.

- **Rust commands**, each doing one fixed thing, with no shell or file permission: `read_service_info`, `desktop_info`, `startup_status`, `service_start`, `update_open`, `update_run`, `service_version`, and the keychain commands for tokens. Stop, restart, and update of a running service go through the service's API, as in the web app. `build.rs` declares the commands, so each gets an `allow-…` permission.
- **Permissions.** A page from another origin than the app's own can call only what a capability grants it. The bundled start page has one for its commands. The service's page gets one at run time, once the port is known, for that exact origin (`http://127.0.0.1:<port>/*`): the commands, writing text to the clipboard, restarting the app, and opening this project's release pages in the browser. Any other page on loopback gets nothing.
- **CSP.** The start page's is the app's (`script-src 'self'`, IPC only). The service's page has the web app's own ([0_06_security.md 1](0_06_security.md#1-rules)).
- **Connections** are saved in the window's storage, with tokens in the OS keychain, or a 0600 file where there is none.
- **Native API revision.** The UI can be newer than the wrapper, so the wrapper reports `native_api`, the revision of its commands. When the UI needs a higher one, it says **App update needed** and opens the release page.

**Updating in place (Y, Z).** **Update agentop** calls `update_open`: the window shows the start page in its update mode, which checks the tools as setup does, then runs the copy's `agentop service update` (the newest tag, install, UI build, restart in place, health check, and going back if any of it fails, [7.3](#73-update)). Each step is a row on the start page. **Launch** then opens the service's page. A new wrapper that finds its copy of agentop older than itself, as after a reinstall, opens the update mode on start, once per run.

**Platforms.** Linux ships as an AppImage only: one file on any distribution, and no package manager to fight over it. With NVIDIA's driver loaded, the app sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` before opening a window, because WebKitGTK's DMA-BUF renderer can give a blank window there; only then, since setting it everywhere made the terminal visibly slow on Mesa, and the user's own value always wins. macOS builds are unsigned for now, so the first open needs Open Anyway. Windows isn't built: the service needs tmux, so a Windows app would have to run the service in WSL ([backlog](9_backlog.md#platforms-and-install)).

Check S2 ([backlog](9_backlog.md#to-check-by-hand)) confirms that WebKitGTK treats the `http://127.0.0.1` page as a browser does.

## 12 Plugins

How plugins work (manifest, loader, the `register(api)` API, CLI commands, enable and disable, UI slots, tests) and what each plugin does are in [0_12_00_plugins.md](0_12_00_plugins.md). The larger plugins have their own docs: [0_12_02_dialogue.md](0_12_02_dialogue.md), [0_12_03_memory.md](0_12_03_memory.md), [0_12_04_input.md](0_12_04_input.md).

### 12.1 Core and plugins

**Where the line comes from.** The core is the owner's manual workflow from before agentop, automated. Back then they ran each agent CLI in a tmux window, Claude Code with `--remote-control` so the work could go on from a phone. `tmux ls` was the session list. Which session did which task was kept in their head. To hand work to another session, they copied the context and pasted it in. agentop's core does the same things: it starts agents in tmux (Claude with remote control in Sessions), lists them with names and summaries, attaches their terminals, resumes them from History, and delivers a prompt from one session to another (`agentop send`). Because that workflow is how the owner works, the core must always work: with no plugins, or with any plugin broken.

Plugins are the features built on top that save effort beyond what the manual workflow did: having a Coordinator run a project, dialogues between agents, waiting-for-input marks, a searchable memory. None of them is needed to work the old way.

The two top features follow the same split. **Sessions** (the `default` project) is the old usage: on-demand, occasional, unsure development or investigation that the owner drives themselves. **Projects** is for work with a clear high-level goal that a Coordinator can pursue ([13](#13-projects)).

**Core**, always loaded with no off switch:

- Sessions: scan, start, stop, close, resume, rename and describe; `agentop send`.
- The terminal bridge and tmux integration.
- The middle panel's tabs, and the right panel: details, token usage, changed and all files.
- History, in Sessions' left panel.
- Projects and their Coordinators, as a mechanism ([13](#13-projects)).
- Service status, stop, restart, update, and sign-in.
- Connections and service switching, settings, and the plugin loader itself.

**A plugin** is an optional, additive feature. It may add API routes, react to core events, add CLI commands, and add UI in defined slots. It may not change or remove core behaviour, and core code never imports a plugin.

Plugins can fail, be removed, or be disabled without stopping sessions, terminals, or service control. Status-driven Projects checks and dialogue turn completion depend on the attention plugin; they pause when its feed is unavailable.

### 12.2 The plugins

| # | Plugin | What it does | Doc |
|---|--------|--------------|-----|
| 01 | Projects (`projects`) | The owner's own projects, each with a Coordinator | [0_13_projects.md](0_13_projects.md) |
| 02 | Dialogues (`dialogue`) | Two agents work together on one task, relayed by an orchestrator | [0_12_02_dialogue.md](0_12_02_dialogue.md) |
| 03 | Memory (`memory`) | agentop's own record of every conversation, with search and summaries | [0_12_03_memory.md](0_12_03_memory.md) |
| 04 | Input bar (`input`) | A box below the terminal to type prompts in, for phones and for keeping drafts apart | [0_12_04_input.md](0_12_04_input.md) |
| 05 | Waiting for input (`attention`) | Marks a session whose agent is waiting | [0_12_00_plugins.md](0_12_00_plugins.md#waiting-for-input-attention) |
| 06 | File viewer (`viewer`) | Opens files, diffs, Markdown and transcripts in a tab | [0_12_00_plugins.md](0_12_00_plugins.md#file-viewer-viewer) |
| 07 | Email code (`email-code`) | Sign in with a code sent by email | [0_12_00_plugins.md](0_12_00_plugins.md#email-code-email-code) |

Handoff is not a plugin: `agentop send` and the `handoff` skill are enough ([4.6](#46-sending-between-sessions)).

## 13 Projects

The full design is in [0_13_projects.md](0_13_projects.md).

The goal of projects is an intelligent **Coordinator**: a session that listens to the owner's requirement, pursues the best approach, and drives agents through agentop's session operations (`agentop start`, `agentop send`, reading and stopping sessions) until the project is done. That goal is not reached yet; the mechanism around it is built.

So a project is a goal with a Coordinator pursuing it. Each project has its own topic, goal, or dream, and its Coordinator owns it: it decides what to do next, starts the sessions the work needs, routes tasks between them, and goes back to the owner only for what only the owner can decide. Its job is **pursuit**: choosing and taking the next best action until the goal is met or the owner is really needed ([0_13_projects.md 4.1](0_13_projects.md#41-pursuit-philosophy)). Written guidance alone gave uneven pursuit, so a Projects Coordinator's checkable duties are enforced by commands (`agentop coordinator`: a direction, plan and execution record in `track/` that the commands write, delegating tasks on the plan's actions, ending every turn with a checked `yield` and a `--next`, and clearing its context at the next wake once a clear is safe and verified), and `agentop coordinator review` measures the rest for it and the owner ([0_13_projects.md 4.3](0_13_projects.md#43-pursuit-tools-projects-plugin)).

Sessions is the `default` project, the one without a goal or a Coordinator: the owner drives it by hand, for random tasks, as before projects.

What a project looks like: a group of sessions and a folder they share, `~/.agentop/projects/<name>/`. Every agentop session belongs to exactly one project, and projects are kept apart: a session sees and sends to its own project's sessions, and each project has its own Active sessions and History. Projects are core; plugins make and use their own:

| Project | Owner | Shown in | Coordinator |
|---------|-------|----------|-------------|
| `default` | core | Sessions | None: the owner starts and runs its sessions |
| One per project of the owner's | `projects` plugin | Projects | Pursues the project's goal: requirements, plan, routing work to sessions it starts |
| One per dialogue | `dialogue` plugin | Dialogues | Designs a dialogue, starts and stops it, collects what it made |

## 14 Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Terminal is slow or glitchy in WebKitGTK | Medium | Check S1; `TerminalView` boundary; ghostty-web fallback |
| Japanese IME composition fails in a webview | Medium | Tested explicitly in S1; the Input bar types outside xterm |
| WebKitGTK treats the `http://127.0.0.1` page differently from a browser | Low | Check S2; the page follows the same rules as the web app from a local service |
| The app can't find `agentop`, tmux, or the agent tools | Medium | Setup checks each through the login shell and says how to fix a missing one; it starts the service through that shell too; `AGENTOP_BIN`; the start page names the log |
| A lost updater signing key | Low | Back it up; a lost key means reinstalling by hand once |
| Browser rules block a web app connection | Medium | Check S4; reachability guidance in the form; recommend the page from a local service or the desktop app |
| Tokens or terminal traffic exposed on plain HTTP | Low | The service refuses a non-loopback bind without TLS, and the app refuses plain HTTP to another machine |
| Token stolen from `localStorage` | Low | Strict `script-src`, no third-party scripts, short-lived client tokens, revocation |
| Acting on the wrong machine after a switch | Medium | Service name always visible; stop and update confirmations name the service; the desktop app finds the local service from `service.json` |
| Mouse mode makes copy awkward | Medium | Check S3; Shift+drag, OSC 52 |
| A bad update leaves the service down | Low | Pre-checks, health poll, automatic rollback; agents unaffected |
| Agent CLIs change their TUI: ready markers, paste, screen parsing | Medium | The start timeout means only slower starts, never failed ones; CI against real CLIs is in the [backlog](9_backlog.md#ci-on-every-push) |

## 15 Decisions

The choices that shape agentop, with what was chosen and what it was weighed against. Ideas declined since are in the [backlog](9_backlog.md#declined-or-on-hold).

| # | Decision | Chosen | Alternative weighed |
|---|----------|--------|---------------------|
| D1 | Token sign-in for the web app | Required for every client; `agentop open` signs a browser in once | Trusting the network or the origin, which connecting to any reachable service rules out |
| D2 | Starting a stopped service | Only the desktop app can, for this computer's service; elsewhere the app shows the command | A separate always-on helper process |
| D3 | Scrolling | `mouse on` per managed session | Leave it off and lose wheel scrollback |
| D4 | Dialogues | A plugin, not core ([0_12_02_dialogue.md](0_12_02_dialogue.md)) | CLI-only, or part of the core |
| D5 | Host platforms | Linux and macOS; systemd optional. On a PC the desktop app runs the service | `LaunchdManager` for macOS without the app, later |
| D6 | Plain HTTP on a non-loopback address (2026-11-14) | Refused: the service won't start without TLS, and the app won't add such an address. Use a tunnel or HTTPS | Allowing it with a warning: easier on a trusted network, but the token and terminals are readable to anyone on it |
| D7 | Tokens (2026-11-14) | The service token only signs a client in. Each client gets its own token, which expires after `client_ttl` unused and `client_max_age` at most, is renewed by the app while in use, and can be revoked on its own ([0_06_security.md 1](0_06_security.md#1-rules)) | One shared token for everything, where rotation resets every client |
| D8 | Where plugins come from | Only folders inside the agentop install (and `AGENTOP_PLUGIN_DIRS`), with their UI in the app build | A user plugin directory, or UI code served by the service: both let more parties run code in the service or the app. Open until a security review |
| D9 | Handoff (2026-11-13) | `agentop send` and the `handoff` skill, on one host; the user watches the target | A handoff plugin with queues, idle-only delivery, reports, approval and an audit log, across hosts: it assumed nobody watches the target |
| D10 | Busy and idle state | Lifecycle hooks for managed Claude Code and Codex sessions, with screen fallback | Screen changes alone cannot reliably distinguish a turn from typing or redraws; hooks and their fallback need live checks after CLI updates |
| D11 | Desktop app (2026-11-16) | A thin wrapper that runs this computer's service and shows its page | A client with its own bundled UI, which fell behind the service after every update |
| D12 | Releases (2026-11-20) | One `v*` series; a GitHub release only for a new major version, which is a new wrapper | A release per tag, with the app replacing itself on each |
| D13 | Starting a Sessions session (2026-11-24) | From its first prompt: a one-shot probe judges the task (tier, folder, title), code picks the tool and model by tier and quota ([0_04_start.md](0_04_start.md)) | A long-running reception session every prompt goes to, which piles up unrelated context; or the probe naming the model itself, which weighs quota badly |

## 16 Sources

External claims above were checked against:

- [Tauri: Linux graphics issues](https://v2.tauri.app/develop/debug/linux-graphics/)
- [xterm.js 6.0.0 release](https://newreleases.io/project/npm/@xterm/xterm/release/6.0.0)
- [ghostty-web](https://github.com/coder/ghostty-web)
- [wterm overview](https://betterstack.com/community/guides/scaling-nodejs/w-term-dom/)
- [Claude support: Agent SDK and `claude -p` usage credit](https://support.claude.com/articles/15036540) (4.6)
- [OpenAI Codex: non-interactive mode](https://developers.openai.com/codex/noninteractive.md) (4.6)
- [Chrome: Local Network Access](https://developer.chrome.com/blog/local-network-access)
- [Safari blocking ws://127.0.0.1 from an HTTPS page](https://discussions.apple.com/thread/254087609)
- [Firefox: localhost as potentially trustworthy](https://bugzilla.mozilla.org/show_bug.cgi?id=903966)
- [Codex app documentation](https://developers.openai.com/codex/app)
- [tmux attach-session source](https://www.openssh.com/cgi-bin/cvsweb/src/usr.bin/tmux/cmd-attach-session.c?f=c)
