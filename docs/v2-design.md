# Agentop v2 Design

Status: proposed. Implementation order is in [v2-plan.md](v2-plan.md).

## 1 Summary

v2 turns agentop from a dashboard into an application, laid out like the Codex desktop app: a session list on the left, the agent's live terminal in the middle, session details on the right.

The core does not change. Agentop opens an agent (Claude Code, Codex, Antigravity) in tmux, and the user works with it through that terminal. What changes is the shape around it:

- A **service** owns sessions and exposes an API. It runs independently of any UI.
- A **web app** and a **Tauri desktop app** are two shells around one UI codebase, with the same look and features.
- Both connect to the local service by default, can add and switch to any agentop service they can reach, and can see and control the status of the connected service.

There is no agent-level streaming, chat view, or message parsing. The terminal is a real `tmux attach` client, so all interaction is whatever tmux and the terminal library already support.

## 2 Goals and non-goals

### 2.1 Goals

- Start a session from the UI (tool, working directory, name), with a visible loading state until the terminal is ready.
- Show the attached tmux terminal in the main area.
- Three-column layout with collapsible left and right columns.
- Web and desktop look and behave the same.
- The service runs with no UI open, and sessions outlive both the UI and the service.
- The UI can read service status and stop, start, restart, and update it.
- The UI can add, connect to, and switch between services, on this machine or any other reachable machine.

### 2.2 Non-goals

- Replacing the terminal with a custom chat or agent-event UI.
- Running agents anywhere other than tmux.
- Windows as a host. tmux does not run there natively.
- Multi-user access. One service serves one OS user.
- A merged session list across services. One window shows one service at a time.
- Proxying one service through another. Clients talk directly to the service they select.
- Desktop app auto-update. The desktop app is built locally for now. This is separate from *service* update, which is a goal.
- Bundling the service inside the desktop app (see 4.2).

## 3 Current state

What v2 builds on, and what was found while checking it. "Measured" means tested on this machine on 2026-11-07, with tmux 3.7c, using a private tmux socket for all tmux tests.

### 3.1 What carries over unchanged

`AgentCli`, `Actor`, `AgentInstance`, the dialogue system, and `ops.py` stay as the domain layer. Dialogues later move out of the core into a plugin (12.9). `scanner.py` remains the source of process information. The FastAPI + WebSocket PTY approach is proven and stays.

The scan is cheap: `ops.get_sessions()` takes about 0.12s for 12 sessions (measured), so there is no need to rework it. Agent sessions already survive service restarts through `KillMode=process`.

### 3.2 Problems v2 must fix

| # | Problem | Evidence | Fixed by |
|---|---------|----------|----------|
| P1 | **Any website can reach the terminal WebSocket.** The handshake has no Origin check, and WebSockets are not protected by CORS. REST also ignores Host. A page in the user's browser could drive agents that run with `--dangerously-skip-permissions`. | Measured: handshake with `Origin: https://evil.example` was accepted against the live service. REST returned 200 with a forged `Host`. | 7.1 |
| P2 | Start blocks the request. Every Claude start takes about 10s or more, because `post_start_hook` polls the whole 40 × 0.25s window unless it sees a trust prompt. | `agentclis/claude.py`, `post_start_hook` | 5.2 |
| P3 | A failed start reports success. If no agent process appears, `_run_in_tmux` returns `ok: True, pid: None`, and the scanner never shows the session because it has no process. The tmux session leaks as `agentop_tmp_*`. | `agentcli.py`, `_run_in_tmux` | 5.1 |
| P4 | Session identity changes mid-start: temp name, then renamed to `{name}-{pid}`. A UI cannot track a loading row through that. | `agentcli.py` | 5.1 |
| P5 | A new session cannot be attached for up to 5s. The WebSocket resolves names from the refresh cache only, so it reports "Session not found". | `api.py`, `session_terminal` | 5.3 |
| P6 | tmux targets are prefix-matched. `-t general` matches `general-41185`, so one session can silently act on another. | Measured | 6.3 |
| P7 | PTY bridge defects: blocking `os.read` per chunk on the shared thread pool with a close-while-reading race, `proc.kill()` with no wait, and a fixed 220×50 start size that forces a visible reflow. | `api.py` | 6.1 |
| P8 | No `TERM` is set on attach; it inherits from the service. With `TERM` unset, attach fails outright, and the service currently gets plain `TERM=xterm`. | Measured: `open terminal failed: terminal does not support clear` | 6.1 |
| P9 | The registry only grows: 318 entries against 9 live tmux sessions. | Measured | 5.4 |
| P10 | `src/api.py` is outside the package, so an installed agentop has no service. | `pyproject.toml` | 4.3 |

Smaller stale items, fixed in passing: `html/index.html` renders a git branch badge but the backend sends no `git` field, `docs/architecture.md` names the registry `registry.json` (it is `sessions.json`), and `CLAUDE.md` still describes the `agentop` unit (the real one is `agentop-api`).

## 4 Architecture

### 4.1 Overview

```
 ┌──────────────┐   ┌───────────────────┐
 │   Web app    │   │  Tauri desktop    │
 │  (browser)   │   │  app              │     same ui/ codebase
 └──────┬───────┘   └───┬───────────┬───┘
        │ HTTP + WS     │ HTTP + WS │ Rust commands (local only):
        │               │           │ start/stop when service is down
 ┌──────▼───────────────▼──┐  ┌─────▼──────────────┐
 │  agentop service        │◄─┤ systemd --user     │
 │  127.0.0.1:9775         │  │ (service manager)  │
 │                         │  └────────────────────┘
 │  REST /api/v2           │
 │  WS   events, terminal  │
 │  static web app         │
 │  SessionStore           │
 │    ops / scanner /      │
 │    AgentCli / registry  │
 └──────┬──────────────────┘
        │ tmux new-session / attach-session
 ┌──────▼──────────────────┐        ┌─────────────────────┐
 │ user's tmux server      │◄───────┤ agentop CLI, plain  │
 │ agent sessions          │        │ `tmux attach`       │
 └─────────────────────────┘        └─────────────────────┘
```

Four rules hold this together:

1. **tmux owns the running state.** The service can restart at any time without touching an agent.
2. **The service owns the API.** UIs hold no business logic and never touch tmux, the filesystem, or `~/.claude` directly.
3. **The UI is disposable.** Closing it stops nothing.
4. **A UI is not tied to a service.** The diagram shows the default local case, but either client can point at any reachable service (4.5). This applies to the web app too, even though a service happens to serve it.

### 4.2 The service is not a sidecar

Tauri can ship a sidecar binary, but that ties the service's lifetime and version to one UI, which contradicts "the service is the core and independent from UI". The desktop app is a client. If no service is installed, it shows setup instructions.

### 4.3 Repository layout

```
src/agentop/
  service/
    app.py          assembles the service; `agentop serve` runs it
    config.py       config.toml
    identity.py     token, instance_id, and name, in service.json
    auth.py         token, Host/Origin checks, terminal tickets
    store.py        SessionStore: merged live state, change events
    terminal.py     PTY bridge
    manager.py      ServiceManager: systemd, self, or none
    update.py       update check and launch
    update.sh       the update itself, run apart from the service
    files.py        safe directory listing and git status
    models.py       Pydantic request/response models
    routes.py       the /api/v2 application
    webdist/        built web app (gitignored, shipped as package data)
  tmux.py           all tmux calls go through one wrapper
  ...               existing modules unchanged
ui/                 React + TypeScript + Vite; builds web and desktop frontends
  src/platform/     web.ts, tauri.ts: the only platform-specific code
  src/terminal/     TerminalView: the only code that imports xterm
desktop/            Tauri project (src-tauri/), loads the ui/ build
```

### 4.4 Technology choices

| Area | Choice | Why |
|------|--------|-----|
| Service | FastAPI + uvicorn | Already in use; Pydantic models give an OpenAPI schema |
| UI | React 19, TypeScript, Vite | Mature ecosystem for a stateful three-pane app. The 2,350-line single-file `index.html` will not stretch further |
| API client | Generated by `openapi-typescript` | One contract; a drifted client fails at compile time |
| UI state | Zustand, fed by the events socket | Small; the server is the source of truth |
| Terminal | xterm.js 6 | See 6.2 |
| Desktop | Tauri 2 | Requested; uses the system webview |

### 4.5 Connecting to services

A **connection** is a record kept by the client, never by a service:

```jsonc
{
  "id": "local-uuid",
  "label": "build-box",
  "url": "http://build-box:9775",   // base URL, may include a path such as /agentop
  "token": "…",                     // desktop: OS keychain; web: localStorage
  "instance_id": "service-uuid"     // pinned on first successful connect
}
```

Every service has a stable `instance_id` and a `name` (default: hostname), set on first run and returned by `/health`.

**Defaults.** The web app always lists the service that served it, under that service's name. The desktop app lists the local service when `service.json` exists. Each client starts on its last-used connection, or on that default.

**Adding a service.** The user gives a URL and a token. `agentop service connect-info` prints both as one pasteable string, `http://host:9775#token=…`. The app accepts it in the URL field. A fragment is never sent to a server, which keeps the token out of logs. `agentop open` uses the same format, and the app stores the token and clears it from the address bar.

Connecting runs these checks in order, and each failure has its own message:

| Step | Failure shown to the user |
|------|---------------------------|
| 1. `GET /api/v2/health` | "Can't reach this service" with likely causes. A browser reports network, CORS, and mixed-content failures as the same error, so the app cannot tell them apart |
| 2. Response is agentop | "This is not an agentop service" |
| 3. `instance_id` matches the pin | "A different service is now at this address", with a confirm. This stops a token going to the wrong service when a tunnel port is reused. It is checked before the token is sent. It guards against accidents, not impersonation |
| 4. `api_version` is supported | "Incompatible version", offering an update when the service is the older side |
| 5. `GET /api/v2/service` with the token | "Token rejected" |

**Switching.** The app closes the events and terminal sockets for the old connection, shows a loading state, then runs the same checks on the new one. This only detaches clients, so every agent on the old service keeps running. Each connection remembers its selected session. All client state is keyed by `(connection id, session id)`, because session IDs are unique only within one service. Two services may both have a `general-41185`.

**Tabs.** The saved list is shared across a browser's tabs. The active connection is per tab, so two tabs can show two services.

**Reachability.** The desktop app has no browser page rules to satisfy, subject to Spike S2. The web app does, and which targets work depends on where the page was loaded from:

| Page loaded from | Target `http://` remote | Target `https://` remote | Target loopback |
|------------------|-------------------------|--------------------------|-----------------|
| `http://127.0.0.1` | Works | Works | Works |
| `https://` host | Blocked as mixed content | Works | Chrome asks for Local Network Access permission; Safari has been reported to block it |
| `http://` non-loopback host | Works | Works | Blocked or unreliable: the permission can only be requested from a secure context |

So the page from a local service (or a tunnel to one) can reach everything, and that is the recommended way to use the web app. Spike S4 confirms this table. When a target is likely to be blocked, the Add Service form says why before the user tries.

The web build has no hard-coded origin, so it can also be hosted as static files. A page served by a service still disappears when that service stops, and any other saved connection then becomes unreachable from that tab as well. The desktop app does not have this limitation.

## 5 Session model

### 5.1 Identity and status

**A session's `id` is its tmux session name**, chosen before tmux is called and never changed. It has the form `{slug}-{rand6}`, for example `fix-auth-a1b2c3`.

The current rename exists only to put the PID in the name. Dropping it removes the need for a second identifier and solves P4: the loading row and the running session are the same object from the first moment. The human name becomes `display_name`, editable at any time without touching tmux. Existing `{name}-{pid}` sessions are valid IDs as they are, and the `general-` prefix check in Eiger's `agentop-ensure-general-session.sh` keeps working.

`name` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`. The slug never contains `.` or `:`, which are tmux target separators.

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

| Status | Meaning | Main area |
|--------|---------|-----------|
| `starting` | Create and launch in progress; `step` gives detail | Loading state |
| `running` | Agent process alive | Terminal |
| `exited` | Agent quit, its shell remains in tmux | Terminal, with a banner offering Resume or Close |
| `failed` | Start did not succeed; `error` says why | Error message, plus the terminal if tmux exists, so the user sees `command not found` |
| `ended` | tmux session gone | Removed from the list; appears in history |

External sessions (detected, not started by agentop) have `managed: false`. They can be attached if they are in tmux, and show details only if not. Only managed sessions can be stopped, as today.

### 5.2 Start flow

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
- `AgentCli` gains an optional `on_progress(step)` callback and keeps its synchronous contract, so `agentop start` and dialogues behave as before.
- `post_start_hook` must stop as soon as the agent is ready, not only when it sees a trust prompt. Each agent gains a ready marker (its input prompt on screen). The 10s timeout stays as a fallback.
- Measured with real Claude Code on a private tmux server: `POST` returned in 40ms, and the session was `running` at 1.4s. The old hook always took at least 10s here.
- Start timeout: 30s without an agent process sets `failed`, or within a second or so when the shell reports the command is missing. The tmux session stays open for inspection until the user closes it.
- Resume uses the same endpoint and flow: `POST /api/v2/sessions` with `{resume: {tool, session_id}, cwd, name}`.
- If the service restarts during a start, the record is marked `failed` on boot, unless reconciliation finds the agent alive.

### 5.3 SessionStore

`SessionStore` replaces the `_cache` dict in `api.py`. It merges three sources:

| Source | Provides |
|--------|----------|
| tmux | Whether a session exists |
| `scanner` | Agent process, PID, memory, runtime |
| Registry | Managed flag, tool, cwd, display name, description, status, created time |

A background tick (2s, since a scan costs about 0.12s) reconciles the sources and emits change events. `POST /sessions` writes to the store immediately, so a new session is attachable the moment it exists (P5). The terminal endpoint checks tmux directly and never trusts a cached list. Sessions started by the CLI or a dialogue appear on the next tick.

`AgentCli` metadata (token usage, AI title) is refreshed on a slower 10s tick.

### 5.4 Registry

The file stays at `~/.agentop/state/sessions.json`, with additive fields: `display_name`, `status`, `step`, `error`, `created_at`, `ended_at`. Records for ended sessions are pruned after 30 days. The old `flock` was held on the temp file, so it protected nothing. Writes now hold a separate lock file, because the CLI and service both write here.

## 6 Terminal

### 6.1 PTY bridge

One WebSocket equals one `tmux attach-session` client on a PTY the service owns.

```
tmux -u attach-session -t =<id>      TERM=xterm-256color, COLORTERM=truecolor
```

- The client passes `cols` and `rows` in the URL, so the PTY starts at the right size.
- The master fd is non-blocking and read through `loop.add_reader`, which removes the per-read thread and the close race (P7).
- Disconnect closes the PTY and awaits the client process. Detaching never affects the session.
- Resize sends `SIGWINCH` to the client directly. The kernel only signals a tty's controlling process group, which the attach client is not, so setting the size alone does nothing.
- Backpressure: the bridge stops reading while a send is pending, and tmux handles a slow client by discarding output and redrawing, so the web layer needs no ack protocol. The flood test confirms it: with `yes` running and the client not reading, `/health` stayed responsive, and after Ctrl-C the terminal caught up in about 0.1s.

Protocol, kept close to today's:

| Direction | Frame | Meaning |
|-----------|-------|---------|
| Both | Binary | Raw terminal bytes |
| Client to server | Text `{"type":"resize","cols":N,"rows":N}` | Resize the PTY |
| Server to client | Text `{"type":"closed","reason":"session_ended"}` | tmux session ended, or `detached` if the client detached |

Close codes: `4401` invalid ticket, `4404` no such session. Any other close triggers reconnect with backoff (0.5s up to 10s), which also carries the terminal through a service restart.

### 6.2 Terminal library: xterm.js 6

| Library | Status | Assessment |
|---------|--------|------------|
| **xterm.js 6** with `addon-webgl` | 6.0.0, powers VS Code | **Chosen.** Largest production track record; full addon set (fit, search, web-links, unicode, clipboard); already used here |
| ghostty-web | 0.4.0, pre-1.0, xterm-compatible API, WASM parser | **Fallback candidate.** Strong parser fidelity, and it does not depend on WebGL, which matters for WebKitGTK |
| wterm | 0.5.x, DOM renderer | Not suitable as the primary choice: younger, and DOM rendering is not aimed at heavy TUI repainting |

Addons: `webgl`, `fit`, `search`, `web-links`, `unicode11` (CJK and emoji width), `clipboard` (OSC 52).

Two consequences of xterm 6 matter here:

- **The canvas renderer is gone.** `@xterm/addon-canvas` peers on xterm 5, so the only fallback from WebGL is DOM.
- **WebKitGTK is the weak spot.** Tauri's Linux graphics documentation warns that WebGL can silently land on a slow path, that the real GPU cannot be detected, and it recommends a non-WebGL fallback on Linux.

So all terminal code sits behind one `TerminalView` component with a `renderer: auto | webgl | dom` setting, and Spike S1 in the plan decides the Linux desktop default. If neither xterm renderer is acceptable there, `TerminalView` makes ghostty-web a contained swap. Browsers are the lower risk, since Chromium's WebGL path is what VS Code ships on.

### 6.3 tmux integration

- **Exact targets everywhere.** All tmux calls go through one wrapper. Session commands use `=<id>`, pane commands use `=<id>:` (measured: `send-keys` and `set-option` reject the form without the colon). This fixes P6.
- **The user's own tmux server is used**, so `tmux attach` from any terminal still works and the session appears there too. The wrapper also supports a private socket, so tests never touch live sessions.
- **Scrolling.** The user's global `mouse` is off, so the wheel cannot reach tmux scrollback. Agentop sets `mouse on` per session at creation. Measured: this leaves the global option and other sessions unchanged. The cost is that selection is captured by tmux, so plain-text selection uses Shift+drag, and tmux copies reach the system clipboard over OSC 52 and `addon-clipboard`. tmux's default `terminal-features` already advertises clipboard support for `xterm*`. Spike S3 tests this path end to end.
- **Sizing.** Measured, with tmux's default `window-size latest`: the window follows the most recently active client. A second client attaching at a different size resizes the window, and typing in the first takes it back. This is acceptable: the size follows whoever is working. `attach -f ignore-size` behaves as expected if a future "watch only" mode is wanted.
- **Scrollback is tmux's.** xterm's own buffer is small, because tmux replays the screen on attach.
- **One live terminal at a time.** Switching sessions closes the socket and attaches the next. tmux repaints immediately, so nothing is lost, and this avoids the browser's limit on WebGL contexts.

## 7 Security

The terminal socket is remote code execution as the user, with agents running in skip-permissions mode. This is the most important section of this design, and the requirement to connect to any reachable service raises the stakes: a service can now be exposed beyond loopback, and it must accept requests from origins it has never seen.

### 7.1 Rules

1. **Bind to loopback by default.** Exposing a service is an explicit choice: `agentop serve --host`, or `host` in `~/.agentop/config.toml`.
2. **Require a bearer token on every request**, with no exception for loopback or for any client, except `GET /api/v2/health`, which returns only status, version, `api_version`, `instance_id`, and `name`.
   - A random 256-bit token lives in `~/.agentop/service.json`, mode `0600`, and is rotated with `agentop service token --rotate`.
   - Rotating invalidates every saved connection; clients then show "Token rejected".
3. **No ambient credentials.** There are no cookies. A token is only ever sent when the app attaches it deliberately, so a malicious page has nothing to ride on. This closes P1 and removes CSRF as a class of problem.
4. **WebSockets use single-use tickets.** Browsers cannot set headers on WebSocket requests, and a token in a URL ends up in logs. `POST /api/v2/tickets` returns a ticket valid for 30s, bound to one purpose (events, or terminal for one session).
5. **File APIs are confined.** Paths are resolved, including symlinks, and must stay inside the session's working directory.

Because any origin must be able to connect, the origin no longer decides access; the token does. So:

- **CORS is open.** `/api/v2` answers any origin, without credentials mode, and the CORS layer sits outside auth so preflights succeed. A foreign page can read `/health` and nothing else.
- **There is no `Origin` or `Host` allowlist in v2.** An allowlist would require configuring every service for every place the UI might be loaded from, which defeats "connect if reachable". Cookies were the thing those checks protected, and there are none. The old `/api` routes, which have no token, keep the Phase 0 checks until they are removed.
- **The web app sets a strict CSP**: `script-src 'self'` with no inline script. It stores tokens in `localStorage`, so this is the main defence for them. `connect-src` has to stay open to user-chosen hosts.

### 7.2 How each client gets the token

| Client | Method |
|--------|--------|
| Web | `agentop open` passes it in the URL fragment, stored in `localStorage`. A visitor with no token sees a screen that explains this and accepts a pasted one. |
| Web, other services | Pasted through Add Service |
| Desktop, local | A Rust command reads `service.json` |
| Desktop, other services | Pasted through Add Service, kept in the OS keychain |
| CLI | Calls `ops` directly, as today; needs no token |

### 7.3 Remote access

A service can be reached from elsewhere in any of these ways:

| Route | Encrypted | Notes |
|-------|-----------|-------|
| Tunnel, such as `ssh -L` or a VPN | Yes | Recommended; the service stays loopback-bound |
| Reverse proxy with TLS | Yes | Base paths are supported in the connection URL |
| Direct bind, with `--tls-cert` and `--tls-key` | Yes | Certificate must be trusted by the client |
| Direct bind, plain HTTP | **No** | Allowed. The token and all terminal traffic are readable on the network |

The service logs a warning at startup when it listens on a non-loopback address without TLS. The app shows a "Not encrypted" label for any non-loopback `http://` connection, in the Add Service form and the switcher. It does not block the connection, since the network may be trusted.

The clipboard fallback for non-secure contexts stays, because of the following.

**The current deployment is already remote.** `eiger-tunnel` forwards local port 9775 to `pisces-01:7775`, and the access log shows requests from a public address. So the service is loopback-bound and still reachable from outside. This has two consequences:

- A loopback bind does not prove a route is private. Every route must carry its own protection. Since the old dashboard was removed (2026-11-14), every route but `/api/v2/health` and the app's static files needs the token, so there is nothing a proxy can expose by mistake.
- The proxy has no authentication of its own, so the token is the only thing in front of the agents (security review, in [TODO.md](../TODO.md)).

## 8 Service lifecycle

### 8.1 CLI

```bash
agentop serve                 # run in foreground (what the unit executes)
agentop service status        # or start, stop, restart, update, token
agentop open                  # open the web app, logged in
```

agentop provides the program, and `agentop serve` runs it. Running it as a system service, behind a proxy, or with TLS is up to the machine's owner, and nothing in agentop requires it. The devhost's systemd unit and its nginx setup live in the Eiger repo, which runs the devhost's services. A unit should set `KillMode=process`, so that stopping the service never takes tmux with it, and `TERM=xterm-256color`.

### 8.2 ServiceManager

```python
class ServiceManager(Protocol):
    def status(self) -> ManagerStatus: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...
```

- `SystemdManager` is used when the service is its unit's main process. It runs `systemctl --user`. Restart uses `--no-block`, so the service does not wait on its own death.
- `SelfManager` is used when the service was started by hand with `agentop serve`. Stop ends the process. Restart sends it `SIGUSR1`: it shuts down cleanly and `exec`s the same command line, so it keeps its PID, terminal, and arguments, and runs whatever code is installed now. Not `SIGHUP`, which closing its terminal sends. Nothing can start it again after a stop.
- `NoManager` is for a process that isn't `agentop serve`, such as tests. It reports the capabilities as false, and the UI disables those buttons.
- `LaunchdManager` is added if macOS hosts become a target.

### 8.3 Who can do what

Stop, restart, and update are admin actions on the service's own API: `POST /api/v2/admin/stop`, `/admin/restart`, and `/admin/update`. Every client calls them the same way, the web app, the desktop app, and the CLI, whether the service runs under systemd or was started with `agentop serve`. The service knows which (8.2) and runs the right command on itself.

There is no start, in any client. A stopped service has no API to answer it. When a service is stopped or can't be reached, the main window shows a red dot and says how to start it on that machine: `agentop serve`, or the system service that runs it.

In every case, stopping the service does not stop agents. The confirmation dialog says so and names the service being stopped, and the sessions are there on restart.

### 8.4 Update

The current install is an editable git checkout, so v2 implements that install kind. `GET /api/v2/service` reports `install_kind`; other kinds report update as unsupported.

Update needs a git checkout, not systemd. It must outlive the service it restarts, so it runs apart from it: in a transient unit under systemd (`systemd-run --user --unit=agentop-update --collect`), since stopping a unit can take its children with it, and otherwise as a detached process whose PID is kept in `update.pid`. The service passes it the command that restarts it: `systemctl --user restart` for a unit, or a signal for a service started by hand. That command waits until the old process stops answering, and if the process is gone because the new version failed to start, starts it again in the background with the same command line, logging to `serve.log`.

`src/agentop/service/update.sh` writes progress to `~/.agentop/state/update-status.json`:

1. Refuse if the tree is dirty or not on `main`.
2. Record the current commit, then `git pull --ff-only`.
3. `pip install -e .`, then build the web app.
4. Restart the service, then poll `/api/v2/health` for 30s.
5. On any failure: check out the recorded commit, reinstall, rebuild, restart, and record the failure.

`GET /api/v2/admin/update` reports commits available and the status of the last run. Terminals drop briefly and reconnect automatically (6.1); agents are not interrupted.

Any client can now talk to a service of a different version, including the web app when it connects to another host. `/api/v2/health` returns an integer `api_version`, and clients declare the range they support. Outside that range, the connect check (4.5) stops with an explanation. `/health`, `/service`, and `/admin/update` must stay compatible across versions, so that an older service can always be updated from a newer client. A service from before `/admin` has these actions under `/service/`, and the client falls back to them on a 404.

## 9 API

All routes are under `/api/v2`. The existing `/api` routes stay until cutover, so the current dashboard keeps working during development.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Status, version, `api_version`, `instance_id`, `name` (no auth) |
| `GET` | `/service` | Name, version, commit, uptime, PID, tmux version, manager, capabilities, `install_kind`, whether TLS is on |
| `POST` | `/admin/restart`, `/admin/stop` | Admin: restart or stop this service (8.3) |
| `GET`, `POST` | `/admin/update` | Admin: check and status; start an update |
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
| `GET`, `DELETE` | `/saved-sessions`, `/saved-sessions/{tool}/{id}` | History |
| `GET`, `POST`, `DELETE` | `/dialogues`, `/dialogues/{id}`, `/dialogues/{id}/stop` | Dialogues. Moves to `/plugins/dialogue/…` (12.9) |
| `WS` | `/ws/events?ticket=` | Change stream |
| `WS` | `/ws/sessions/{id}/terminal?ticket=&cols=&rows=` | Terminal |

The working-directory picker uses `/fs/dirs` rather than a native dialog, because the path is on the service's machine, which may not be the machine showing the UI. It also keeps web and desktop identical.

The events socket sends a full `snapshot` on connect, then `session.upserted`, `session.removed`, and `service.updated`. Reconnecting simply yields a new snapshot, so the client needs no replay logic.

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
  "tmux": { "session": "fix-auth-a1b2c3", "clients": 1 }
}
```

## 10 UI

### 10.1 Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [◧]  fix-auth · claude · ~/project                       ● Running  [◨]  │
├───────────────┬────────────────────────────────────────┬─────────────────┤
│ ▾ ● build-box │                                        │ DETAILS         │
│ [+ New]       │                                        │                 │
│ 🔍 Filter     │  $ claude                              │ Name   fix-auth │
│               │  ╭──────────────────────────────────╮  │ Tool   claude   │
│ ● fix-auth    │  │  Claude Code                     │  │ Dir  ~/project ⧉│
│ ◌ docs-pass   │  ╰──────────────────────────────────╯  │ PID  12345      │
│ ● general     │  > refactor the token refresh…         │ Up   1h 12m     │
│ ○ old-review  │                                        │ Mem  412 MB     │
│ ✕ bad-start   │                                        │ USAGE           │
│               │        tmux attached terminal          │ In   12.1k      │
│               │                                        │ Out  48.2k      │
│               │                                        │ Cache 1.2M      │
│               │                                        │ FILES           │
│               │                                        │ [Changed] [All] │
│ History       │                                        │ M src/auth.py   │
│ Dialogues     │                                        │ A tests/test_…  │
│               │                                        │ [Stop]          │
└───────────────┴────────────────────────────────────────┴─────────────────┘
  ● running  ◌ starting  ○ exited  ✕ failed
```

- **Left**, default 260px: service switcher at the very top, then new-session button, filter, session rows (status, name, tool), History, and Dialogues. Rows sort newest first. Everything below the switcher belongs to the selected service.
- **Header**: when the left column is collapsed, the header shows the service name as well, so it is always clear which machine a terminal is on.
- **Middle**: takes all remaining width. Refits the terminal on every panel toggle or resize.
- **Right**, default 320px: details, usage, files, actions. It has four groups: identity (editable name, description, tool, status, directory, tmux attach command), process (PID, uptime, memory), usage (tokens by type), and files (Changed and All tabs).
- Both side columns are resizable and collapsible, with state remembered per client. Below 900px they become overlays and start collapsed.

### 10.2 Main area states

| State | Shows |
|-------|-------|
| No session selected | Prompt to start one, with recent directories |
| `starting` | Spinner, session name, and current step text |
| `running` | Terminal, focused |
| `exited` | Terminal with a banner: Resume, Close |
| `failed` | Error, retry with the same input, and the terminal if tmux exists |
| Reconnecting | Terminal dimmed, "Reconnecting…" |
| External, not in tmux | Explanation that it cannot be attached |
| Service unreachable | Disconnected view with Retry, Switch service, and Start on local desktop. Other saved services stay available |
| Switching | Spinner with the target service name |

### 10.3 New session

A modal with Tool (only installed tools enabled), Working directory (picker, recent list), and Name (optional, derived from the directory). Submitting closes it at once, selects the new row, and shows the loading state.

### 10.4 Keyboard

Terminal focus means keys belong to the terminal. Ctrl+B is the tmux prefix, so it must not be the sidebar shortcut that apps commonly use. App shortcuts avoid anything a TUI needs:

| Shortcut | Action |
|----------|--------|
| Ctrl+Shift+[ and Ctrl+Shift+] | Toggle left and right columns |
| Ctrl+Shift+N | New session |
| Ctrl+Shift+K | Focus session filter |
| Ctrl+Shift+S | Open service switcher |
| Ctrl+Shift+F | Search terminal buffer |

Browsers keep a few keys for themselves: Ctrl+W closes the tab rather than deleting a word. The web app shows a `beforeunload` prompt while a terminal is open. The desktop app does not have this problem.

### 10.5 Service switcher and panel

The switcher is a menu in the top bar, after the selected session's name:

```
┌──────────────────────────────────┐
│ ● build-box                Status│
│ ● gpu-1  Not encrypted     Status│
│ ○ office-mini  Unreachable Status│
│ ──────────────────────────────── │
│       Connect to a service       │
└──────────────────────────────────┘
```

- Selecting a row switches immediately (4.5).
- Rows show live reachability from `/health`, polled every 15s only while the menu is open.
- **Connect to a service**: URL (or pasted `connect-info` string), token, and an optional name. A Test button runs the 4.5 checks and shows the service's own name and version before saving. The form warns about unencrypted URLs and likely browser blocks.
- **Status**, on each row, opens the service panel for that service, selected or not.

The service panel shows a name field, address, version and commit, uptime, tmux version, encryption state, and Stop, Restart, and Update buttons, enabled according to section 8.3. Update shows commits available and live step progress. The name is an alias kept only by this client; left empty, the service's own name shows. Forget removes a saved service from this client, with its token. It never affects the service or its sessions, and the service that served the web app can't be forgotten.

The web app keeps saved services, with their tokens and names, in the browser's `localStorage`. The desktop app keeps them there too, but its tokens go in the OS keychain.

### 10.6 Platform layer

`ui/src/platform/` is the only place the two shells differ:

```ts
interface Platform {
  connections: ConnectionStore                // saved list; keychain or localStorage
  defaultConnection(): Promise<Connection | null>
  copyText(text: string): Promise<void>
}
```

Everything else is shared. A CI check forbids importing `@tauri-apps/*` outside this directory.

## 11 Desktop app

- Tauri 2 window that loads the bundled `ui/` build, not a URL from the service. It has to render when the service is down, to say so and how to start it.
- A small Rust command set, with no shell permission: `read_service_info`, and the keychain commands for tokens. Stop, restart, and update go through the service's API (8.3), as in the web app.
- CSP: strict for scripts. Tauri's CSP is fixed at build time, so `connect-src` cannot list services the user adds later and has to allow `http:`, `https:`, `ws:`, and `wss:`. If S2 moves traffic through plugins, their URL scope needs the same breadth.
- Connections are saved locally, with tokens in the OS keychain. Default: the service from `service.json`.
- Build prerequisite on this machine: Rust is not installed. WebKitGTK 2.52.6 is.

`tauri://localhost` reaching `ws://127.0.0.1` and plain `ws://` on a remote host has not been verified, and the available documentation does not settle it. The remote case is the harder one, since the loopback exemption from mixed-content rules does not apply. Spike S2 tests both. If it is blocked, the fallback is to route through Tauri's HTTP and WebSocket plugins inside `platform/tauri.ts`, which leaves the rest of the UI unchanged.

## 12 Plugins

Agentop grows from here through plugins. The features that exist today are the **core**: they are what agentop is, and they cannot be turned off. Anything new on the service side is built as a plugin in its own folder, and the core must stay fully usable whatever happens to a plugin.

### 12.1 Core and plugins

**Core** is every feature that exists at v2.0. It is always loaded and has no off switch:

- Sessions: scan, start, stop, close, resume, rename and describe, managed and external sessions.
- The terminal bridge and tmux integration.
- The main area's tabs: one or two groups side by side, each tab holding a session's terminal or a file (drawn by a `file-viewer` plugin).
- The right panel: details, token usage, changed and all files, and the session's transcripts.
- History.
- Service status, stop, restart, update, and token handling.
- Connections and service switching, settings, and the plugin loader itself (12.3).

Dialogues exist today but are **not** core: they are one way of using agents, not what an agent is. They become the first plugin (12.9).

**A plugin** is an optional, additive feature. It may add API routes, react to core events, and add UI in defined places. It may not change or remove core behaviour, and core code never imports a plugin.

The rule that everything else follows: **a plugin can fail, be removed, or be disabled, and the only visible effect is that its feature disappears.** Sessions, terminals, and service control keep working.

### 12.2 Layout

Each plugin is one folder under `src/agentop/plugins/`, with its UI in a matching folder under `ui/src/plugins/`:

```
src/agentop/plugins/
  attention/
    plugin.toml         manifest
    __init__.py         defines register(api)
    ...
ui/src/plugins/
  attention/
    index.ts            the UI pieces, keyed by slot
```

The manifest declares what the plugin is, without running any of its code:

```toml
id = "attention"                 # folder name; [a-z0-9-]+
name = "Waiting for input"
version = "0.1.0"
description = "Marks sessions whose agent is waiting for you."
plugin_api = 1                   # the core plugin API version it was written for
default_enabled = true
ui = ["session-badge", "settings-section"]   # UI slots it fills (12.5)
```

Only folders inside the agentop install are scanned. Loading code from a user directory such as `~/.agentop/plugins/` would let anything that can write there run inside the service, so it is left for the security review.

### 12.3 Loader

The loader is part of the core. At service start it:

1. Scans `src/agentop/plugins/*/plugin.toml`.
2. Reads each manifest. A missing or invalid manifest gives state `failed`, with the reason.
3. Checks `plugin_api` against the core's supported range. Outside it gives `incompatible`.
4. Reads the saved enabled flag, falling back to `default_enabled`.
5. For each enabled plugin, imports it and calls `register(api)`, each inside its own `try`, with a time limit. An exception or timeout gives `failed`, and the error is logged and kept for the API.
6. Builds the **plugin list**: every plugin found, with its state.

States: `enabled`, `disabled`, `failed`, `incompatible`. A plugin that fails never stops the loader, the remaining plugins, or the service from starting. With no `plugins` folder at all, the service runs exactly as the core alone.

**What `register(api)` gets.** A narrow object, not the service internals:

| Facility | Rule |
|----------|------|
| `api.router` | A FastAPI router mounted at `/api/v2/plugins/{id}/`. It inherits token auth. A plugin cannot add routes anywhere else |
| `api.on(event, handler)` | Core events: `session.started`, `session.exited`, `session.closed`, `scan`. The handler is a plain function, called with the session row (or, for `scan`, the session list). Each handler runs isolated: an exception is logged and counted, and never reaches the core. Handlers run in a thread off the event loop, with a time limit (10s), and the core never waits for their result |
| `api.task(fn)` | A supervised background task. `fn` is an async function, not a coroutine, so the task can be started again when the plugin is enabled again. If it raises, the plugin goes to `failed` and the task is not restarted |
| `api.sessions()` | A read-only snapshot of the session list |
| `api.on_disable(fn)` | Called, in a thread, when the user turns the plugin off, to wind down work it started: the Dialogues plugin stops running dialogues. Not called when the service stops, so work that outlives the service (a process the plugin started) keeps running then. A failure is logged; the plugin is off either way |
| `api.store` | A JSON file at `~/.agentop/plugins/{id}/store.json` for the plugin's own data |
| `api.data_dir` | The plugin's own folder, `~/.agentop/plugins/{id}/`, for files that don't fit in one JSON file. It is made when first written to |
| `api.emit(data)` | Sends `{"type": "plugin", "id": …, "data": …}` on the events socket for the plugin's UI |
| `api.log` | A logger named `agentop.plugins.{id}` |

`register(api)` only declares routes, handlers, and tasks. The loader switches them on after `register` returns in time (5s), so a plugin that fails part way through leaves nothing behind. A plugin's own modules import as `agentop.plugins.{id}.…`, so relative imports work.

Repeated handler errors (for example 5 in a minute) move the plugin to `failed` automatically, so a broken plugin stops being called.

**No `api.agents`.** It was planned so that plugins would drive agents through the service rather than the core modules. Dropped (2026-11-14) when dialogues, its only user, moved: their orchestrator is a process of its own, started by `agentop dialogue start` with or without a service, so a service-side API can't reach it. A plugin that drives agents uses the core's own functions (`ops.start`, `AgentInstance`, `Actor`), and the sessions it starts are ordinary managed sessions.

**CLI.** A plugin may add `agentop` commands. Its manifest names them, `cli = ["recall"]`, and a `cli.py` beside its `__init__.py` defines `register_cli(cli)`. The CLI reads manifests and saved flags without starting the service. A missing plugin's command does not exist. A disabled, failed, or incompatible plugin's command is still listed, and running it says so and how to enable it. The core commands never depend on a plugin.

Built (2026-11-13) in `agentop/plugin_cli.py`, with manifest reading and flags in `agentop/plugin_manifest.py`, which the service's loader uses too:

- **Names in the manifest, not `cli = true`.** A disabled or failed plugin's commands can only be listed if their names are known without running its code.
- **`cli.py`, not `__init__.py`.** The CLI imports only `cli.py`, under a bare `agentop.plugins.{id}` package, so its relative imports work while `__init__.py`, the service's side, never runs. Importing FastAPI alone takes 0.14s against 0.03s for the whole CLI, so a plugin command starts as fast as a core one: `agentop recall` runs in 0.08s.
- **`cli.command(name, handler, **options)`** adds a command the manifest names and returns its parser, for arguments; `handler(args)` runs it. `options` are ArgumentParser's (`help`, `description`, `formatter_class`). `cli.data_dir` is the plugin's folder, as the service gives it. A command the manifest doesn't name is refused.
- **All or nothing.** The parsers stay detached until `register_cli` returns, so a plugin that fails part way adds none of its commands. An exception in `cli.py` or `register_cli` turns each of its commands into one that says it failed and why. Other plugins and the core are untouched.
- **Names.** Plugin commands are added after the core's, so a core command keeps its name, and of two plugins naming one command, the first in folder order keeps it.
- The Memory plugin's `agentop recall` is the first: it moved from the core into `plugins/memory/cli.py`.

### 12.4 Enable and disable

Python cannot reliably unload a module, so disabling is a gate rather than an unload:

- **Disable**: requests to its routes return 404, its event handlers stop being called, its tasks are cancelled, and its UI disappears. Its `on_disable` handlers are called, so it can wind down work it started. The flag is saved. Before turning a plugin off, Settings asks its UI (`beforeDisable`), which may answer with a warning for the user to confirm, such as "A dialogue is running".
- **Enable** of a plugin that was loaded: the gate opens again, and its tasks are started again.
- **Enable** of a plugin that was never imported (disabled at start) is imported then. If that fails, the state is `failed` and nothing else changes.
- **Retry** of a `failed` plugin needs a service restart; the UI says so. This holds for a plugin that failed while running too (a crashed task, repeated handler errors): enabling it again leaves it `failed`, with the restart note in its error. This keeps half-initialised plugin state out of the running service.

Flags live in `~/.agentop/state/plugins.json`: `{"attention": {"enabled": false}}`. They belong to the service, not the client, because a plugin is a service feature: disabling it affects every client of that service.

### 12.5 API

All require the token.

```
GET   /api/v2/plugins          → [{id, name, version, description, state, error, ui}]
PATCH /api/v2/plugins/{id}     {enabled: bool} → the updated entry
*     /api/v2/plugins/{id}/…   the plugin's own routes, 404 unless enabled
```

`GET /api/v2/service` gains a `plugins` count, and every change to the list is pushed on the events socket as `{"type": "plugins"}`, so open clients update without polling. `/api/v2/health` stays plugin-free, so a connection's checks (4.5) never depend on a plugin.

### 12.6 UI

The web and desktop builds are static, and the desktop app has a strict script CSP, so a service never sends UI code. Plugin UI ships in the app build under `ui/src/plugins/`, and the app turns it on per service:

- After connecting, the app reads `GET /api/v2/plugins`. Plugins differ by service, so the list is kept per connection.
- A plugin's UI shows only when **both** the service lists it as `enabled` **and** the app has a matching module in `ui/src/plugins/`. If either side is missing (an older app, an older service, a removed or failed plugin), the feature is hidden.
- Plugin UI goes into fixed **slots**: a row in the right panel (`right-panel`), a view in the left column's footer, beside History and Dialogues (`left-view`), a badge on a session row (`session-badge`), a section in Settings (`settings-section`), and the contents of a file tab in the main area (`file-viewer`). While a `file-viewer` plugin is enabled, files in the right panel open in a tab when clicked; without one, clicking copies the path. A manifest naming any other slot is invalid. Core layout code renders each slot from the plugin list and never names a plugin.
- Each slot is wrapped in an error boundary. A plugin component that throws hides itself; the rest of the page keeps rendering.
- Plugin UI calls its own routes only through a client scoped to `/api/v2/plugins/{id}/`.
- A plugin's UI is `ui/src/plugins/{id}/index.tsx`, exporting `plugin`: an object with any of `sessionBadge`, `leftView` (a label and a component), `rightPanel`, `settingsSection`, and `fileViewer` (a component given the file: session, source `workdir` or `transcript`, and path). Each component gets a context with the plugin's info, its scoped client, and `onEvent` for its `api.emit` messages. The app finds these modules at build time (`ui/src/plugins/registry.ts`).
- The app fetches the plugin list each time its events socket opens, and again on every `plugins` event. Events of a type the app doesn't know are ignored, so an older app keeps working against a newer service.

**Settings → Plugins** lists every plugin the service reported, with its description, version, state, and any error, and a switch to enable or disable it. A `failed` plugin shows its error and that a restart is needed to retry it. An `incompatible` one says which side is too old. A plugin the service has but this app doesn't know is listed as "Not available in this app".

### 12.7 Tests

- The whole core suite (pytest and Playwright) runs with no plugins present. This is the proof that the core does not depend on them.
- A set of broken test plugins: one with a bad manifest, one that raises on import, one that raises in `register`, one whose route raises, one whose event handler raises or hangs, one whose task crashes, and one whose UI component throws. With all of them installed, the core suite still passes and each one shows the right state in the list.
- Disabling a plugin from Settings removes its UI at once and its routes return 404; enabling brings both back.

### 12.8 First plugins

The later features (plan 9.2) become plugins one at a time, where they are service features:

| Feature | Plugin? |
|---------|---------|
| Dialogues | Yes: the first plugin, moved out of the core (12.9) |
| Attention indicator for agents waiting on input | Yes: watches screens on `scan`, emits per-session state, fills the session-badge slot |
| File preview and diff view | Yes: the File viewer plugin (`viewer`) serves file text and fills the `file-viewer` slot. It draws JSONL transcripts as conversations and Markdown as a page (built 2026-11-13). Syntax highlighting and diffs come next, in the same plugin |
| Handoff: pass a side task, such as a deploy, to another session | No: `agentop send` and the `handoff` skill are enough (12.10) |
| Grouping sessions by project | Yes: stores groups, a left-column view |
| A merged session list across services, a window per service | No: client-only. It stays a non-goal (2.2) |
| Offline app shell for the web app | No: client-only |
| Desktop auto-update | No: desktop-only, and a non-goal (2.2) |
| macOS host support (`LaunchdManager`) | No: the core needs it to run at all |
| Non-git install kinds for update | No: part of core service update |

The security review and retiring the old dashboard (both in [TODO.md](../TODO.md)) are core work, not plugins.

### 12.9 Dialogues as a plugin

Status: built 2026-11-14. Done when (below) holds: the core suites pass with both dialogue folders deleted.

Dialogues are built already, so moving them is also the test of the plugin system: if the loader and its API can carry dialogues, they can carry the features after it.

**What moves into `src/agentop/plugins/dialogue/`:** today's `src/agentop/dialogue/` package (model, orchestrator, parser, status, scenarios), the `/api/v2/dialogues…` routes, which become `/api/v2/plugins/dialogue/…`, and the `agentop dialogue` CLI commands, through `register_cli`. The orchestrator keeps driving its two agents through the core's `ops` and `AgentInstance` (no `api.agents`, 12.3).

**What moves into `ui/src/plugins/dialogue/`:** the Dialogues view, filling the left-column view slot, and the "dialogue" label on session rows, filling the session-badge slot. Core files stop naming dialogues: the session list, the store, and the API client.

**What stays in the core:** nothing dialogue-specific. The core keeps the generic pieces dialogues needed: starting a session under a given short name, `Actor` send and receive, and stop. A dialogue's agents (`dia{id}a`, `dia{id}b`) remain ordinary managed sessions.

**Data.** Saved dialogues are in `~/.agentop/plugins/dialogue/dialogues/` (moved there from `~/.agent-dashboard/dialogues/` on 2026-11-11, ahead of the code), in the same format. Removing or disabling the plugin never deletes them, and enabling it again shows them all.

**When the plugin is disabled, removed, or fails:**

- The Dialogues view, the session labels, and the `/plugins/dialogue/` routes disappear. `agentop dialogue` says the plugin is disabled.
- A running dialogue is stopped as if its Stop button were pressed: the relay ends and the dialogue is saved as stopped. Settings warns before disabling while any dialogue is running.
- The dialogue's agent sessions, if still open, stay in the session list as plain sessions that can be attached, stopped, and closed.
- Sessions, terminals, history, and service control are unaffected.

**Done when:** the core test suites pass with the `dialogue` folder deleted, the dialogue tests pass with the plugin enabled, and disabling it from Settings hides every trace of dialogues in the UI without touching other sessions.

### 12.10 Handoff: pass a side task to another session

Status: built 2026-11-13 as `agentop send` and the `handoff` skill. Not a plugin: the handoff plugin designed earlier was dropped (12.10.3).

**The problem.** A development session builds up expensive context: the change, why it was made, what is left to do. Part way through, the work needs a side task that belongs elsewhere, typically a deploy to a non-production environment. Doing it in the development session, with the deploy project's skills, piles the deploy's logs and retries into the development context. Opening a session in the deploy project keeps the contexts apart, but the user has to explain the change there by hand.

**The workflow.** In the development session the user asks for it in plain words, from Claude Code or Codex:

```
please tell the akimoto change and ask Eiger to deploy akimoto and watch for next fulfillment run
```

1. The `handoff` skill writes a prompt that stands on its own: the task, what changed and why, the repo and commit, what counts as done, and what to ask the user before doing.
2. It runs `agentop send eiger`, which types the prompt into the live session called Eiger and submits it ([cli.md 1.4](cli.md#14-send)). The prompt starts with `[agentop send from <sender>]`.
3. That is all. Nothing comes back to the sender, which carries on with the change. The user switches to Eiger, in the agentop app or with tmux, to watch it and answer it there.

The target must be a live managed session. With no match, `agentop send` says `I don't see an active session called 'Eiger'`; it never starts one. A match ignores case and the `-<suffix>` agentop adds, so `eiger` finds `Eiger-2953204`.

#### 12.10.1 Existing tools

- **Claude Code's `SendMessage`.** Claude sessions on one machine can list each other (`ListAgents`) and send messages. The first version of the skill (2026-11-09) used it, with a target skill that reported back. It worked between Claude sessions, but Codex has no `SendMessage`, so a Codex session could neither send nor get the report. `agentop send` works for every tool.
- **Subagents.** A Claude subagent keeps its work out of the caller's context, but it does not run in a session the user can attach to, and cannot stop and ask the user part way through.

#### 12.10.2 Transport: tmux only

The prompt is typed into the target's terminal through tmux, the way each tool takes a paste (`AgentCli.send_text`), like every other message agentop sends. Agentop does not call any LLM API, and does not run agents headless (`claude -p`, `codex exec`).

Why:

- The agent CLIs (claude, codex, agy) are built to run in a terminal, and tmux is the native way to run them. Agentop's job is to bring those tmux sessions into one window, not to become a different agent runtime.
- Every handoff runs in an ordinary session: the user can see it, attach, answer questions, take over, or stop it. Nothing runs hidden. This is the main requirement of the workflow.
- Billing, login, and permissions stay exactly as they are for interactive use. Interactive sessions are covered by the user's subscription plan. Calling the LLM API directly would need a separate pay-as-you-go API key, and headless runs follow their own rules: on Claude plans, `claude -p` usage moved to a separate monthly Agent SDK credit in June 2026. (See section 16.)
- A new agent CLI works with no new integration, because it is just another terminal program.

#### 12.10.3 Dropped: the handoff plugin (2026-11-13)

The earlier design was a plugin: `agentop handoff send/ask/report/show/list`, a queue per target, delivery only when the target is idle, a report typed back into the sender, a `needs_you` status, timeouts, an allowlist, per-target approval, a hop limit, an audit log, a Handoffs view, and handoff across hosts (D9). All of it assumed nobody watches the target, so agentop would have to track and report for the user. The user does watch it: the session list shows every session, and switching to the target is one click. So the concerns it answered are resolved without it:

| Concern | Resolution |
|---------|------------|
| Safety: one agent can make another act, and a prompt injection could make a session deploy | Accepted for now. agentop is a development tool, and the skill only does what the user would type in the target themselves. Only managed sessions take prompts |
| Typing into a busy session, or over a draft | The tool queues a prompt sent while it works. The user sees the target in the session list and can switch to it to check and take control |
| Getting a report back to the sender | Not needed: the user watches the target and acts on the result there. If a report is ever wanted, the target can `agentop send` one back |

### 12.11 Memory: agentop's own record of conversations

Status: phases 1 to 5 (archive, messages table, conversation view, recall, session summaries) built. Plugin id `memory`.

**The problem.** Conversations with agents carry exact details that matter later: a person's name, a place, a link someone sent. At the time they are on hand, so nobody notes them, and days or months later the keyword itself is forgotten. The agent tools' own memory features don't help: Claude Code writes memory only when it judges a fact worth keeping, per project folder, and Codex's is off by default. Neither records what was said.

**Source: the tools' transcripts, not the screen.** Reading the tmux buffer and picking out prompts and replies is fragile: colour codes, wrapping, redraws, limited scrollback, and only sessions in tmux. Claude Code and Codex already write every session as structured JSONL, with each record marked as the user, the assistant, or a tool. Reading those is easy and exact, and it covers sessions agentop did not start.

**Agentop keeps its own copy.** The tools delete old transcripts (Claude Code after 30 days by default, `cleanupPeriodDays`). Agentop copies them into its own area and never deletes, so the tools' retention stops mattering once the copy runs more often than they clean up.

#### 12.11.1 Decisions (2026-11-10)

- **Keep the raw files, and derive everything else.** The archive is a plain copy of each transcript file. The prompt-and-reply table, the index, and any summaries are built from it and can be rebuilt when the extraction improves. Nothing that a later parser might want is thrown away.
- **Simple first.** Plain files, no compression. All of the transcripts on the devhost are about 330MB today.
- **One host for now.** The archive covers the machine the service runs on. Sessions on other machines, and claude.ai or ChatGPT chats, are left out. With one host working, aggregating several later is a matter of shipping archives to one place.
- **`cleanupPeriodDays` is left alone.** Agentop does not change the tools' settings. The sweep runs every few minutes, far inside 30 days; the risk is only a service that stays down for weeks. Raising the setting stays a TODO.
- **History already deleted is gone.** On the devhost the oldest Claude transcript left is from 2026-10-09.

#### 12.11.2 The archive

`~/.agentop/plugins/memory/archive/{tool}/…`, mirroring each tool's own layout under its root:

| Tool | Source root | What is copied |
|------|-------------|----------------|
| `claude` | `~/.claude/projects/` | Every `*.jsonl` and the files in the session folders beside them (subagents, tool results). Not the `memory/` folders |
| `codex` | `~/.codex/` | `sessions/**` and `archived_sessions/**` |
| `antigravity` | `~/.gemini/antigravity-cli/brain/` | Every conversation folder |

Each tool's module in `agentop/agentclis/` says where its root is and what to copy (`transcripts_root`, `archive_files`), and how to read a transcript (`conversation_reader`, `searchable`); the archive and the messages table name no tool themselves.

Antigravity conversations from before its rename (from Gemini, up to 2026-06-13 on the devhost) have no `transcript.jsonl`: they are only in its binary store, `~/.gemini/antigravity-cli/conversations/` (`.pb`, or SQLite with protobuf steps). They are left out on purpose.

A **sweep** walks the source roots and brings the archive up to date. It runs at start, every 5 minutes, and when a session exits, in a thread off the event loop.

- **New file**: copied.
- **Grown file**: the tools only append, so only the new bytes are appended. The sweep checks that the archived bytes are still the start of the source (a sample at the start and just before the archived length) before appending.
- **Rewritten or shrunk file**: the old copy is kept as `{name}.{timestamp}` and the file is copied afresh.
- **Removed from the source**: kept. This is the point of the archive.

Per-file state (size, modification time) is kept in `~/.agentop/plugins/memory/state.json`, so an unchanged file costs one `stat`. The first sweep imports everything on disk.

#### 12.11.3 Phases

1. **Archive.** The sweep above, a status route (files, bytes, last sweep, errors), and a Settings section showing it with a "Sweep now" button.
2. **Messages.** A SQLite table built from the archive: tool, project folder, session, turn, time, role, text, and the record's own ID so nothing is stored twice. Only the user's prompts and the assistant's text; no tool calls, tool output, or thinking.

   Built as `plugins/memory/messages.sqlite`. After each sweep it reads each archived transcript from where it last stopped (a whole line at a time, so a line still being written waits), and stores each record once by its own ID (`UNIQUE (tool, record)`), so a rewritten file's old copy or a resumed session adds nothing. `turn` is the ID of the prompt that started it, so a prompt and the replies to it group together. Deleting the file rebuilds it. On the devhost the first build reads 307 files into 13,108 messages from 186 sessions in 1.5s.

   | Tool | Prompts | Replies | Left out |
   |------|---------|---------|----------|
   | `claude` | `user` records with text, not tool results; a slash command as typed (`/handoff deploy eiger`) | `assistant` text blocks | `isMeta`, `isSidechain`, compaction summaries, API errors, `<system-reminder>` blocks, local command output, interrupts, subagent and tool-result files |
   | `codex` | `response_item` messages from `user` | `output_text` from `assistant`, both commentary and final answer | AGENTS.md and other whole tagged blocks Codex injects (`<environment_context>`, app events), `developer` messages, reasoning, tool calls. Older rollouts are `.jsonl.zst` and are read whole |
   | `antigravity` | `USER_INPUT`, the text inside `<USER_REQUEST>` | `PLANNER_RESPONSE` content | Tool steps (`RUN_COMMAND`, `VIEW_FILE`, …), thinking. No project folder is recorded |

   "Final text" turned out to be the wrong cut: the text an assistant writes between tool calls ("Yamada's link is …, checking it") often holds the details worth finding, so all of the assistant's text is kept, and only its tool calls and thinking are left out.

3. **Conversation view.** The File viewer shows a transcript as a conversation, from the same extraction. The Transcripts list falls back to the archived copy when the tool has deleted the original.

   Built. The extraction moved out of the plugin, to each tool's `AgentCli.conversation_reader` with the shared parts in `agentop/conversation.py`, so the viewer reads a live transcript without the Memory plugin, and the messages table uses the same rules. The viewer's `/file` says whether a file reads as a conversation, and `GET /api/v2/plugins/viewer/conversation?session=&path=` returns its messages (the latest 5000). The tab opens on Conversation, with Raw beside it; links are clickable, and it opens at the latest message. `AgentCli.transcript_files` takes the archive as its root, and the listing says `archived: true` when that is where the files came from. Codex's own record of a rollout's path goes stale once it compresses or archives the file, so a rollout is also found by name.
4. **Recall.** Full-text search (SQLite FTS5) over the messages, a route and an `agentop recall` command, and a skill so a session can ask "what was the link Xxx sent about …". Embeddings only if keyword search misses too often.

   Built. `messages_fts` is an FTS5 index over the text with the `trigram` tokenizer, kept up to date by a trigger, and built from the table when it is first added. Trigrams match any part of a word, ignoring case, so a partial name, a domain, or Japanese text (which has no spaces) is found without word splitting; a word under three characters is matched with `LIKE`. Every word must match; quotes keep a phrase. Results are best match first (bm25), each with its message ID, date, tool, project, session, a snippet, and, for a reply, the prompt it answered. On the devhost a search over 13,000 messages takes about a millisecond.

   - Routes: `GET /api/v2/plugins/memory/search?q=&tool=&project=&since=&limit=` and `GET /api/v2/plugins/memory/conversation?session=&tool=&around=&radius=`.
   - `agentop recall <words>` and `agentop recall --session <id> --around <#id>` ([cli.md 1.6](cli.md#16-recall)). It reads the database directly, so it works with the service down. It is the plugin's command, in `plugins/memory/cli.py` (12.3, CLI).
   - The `recall` skill (`src/agentop/skills/recall/`) tells a Claude session how to search: distinctive words rather than "link" or "name", variants when nothing matches, read around a hit before answering, quote the detail exactly, and never guess.
5. **Session summaries.** A title, a few sentences, and keywords for each session, written by a model and searched beside the messages, so a session is found by what it was about even when no message has the words (12.11.4, shortfall 1).
   Built, in `summaries.py`. The `summaries` table is in `messages.sqlite`, with its own FTS5 trigram index over title, summary, and keywords; like the messages it is derived, and deleting it makes it again.
   - **When.** A session with at least two messages is due once nothing has been said in it for 30 minutes, and again once it has grown by half (at least 10 messages) since its summary. After each sweep up to 8 are made, newest first, so a backlog is worked off over hours. A session the model failed on waits 6 hours; two failures in a run end the run, since that is usually the model itself (signed out, over quota).
   - **Who writes them.** The first installed tool with a one-shot prompt, `AgentCli.ask`: for now only Claude Code, `claude --print --model haiku --no-session-persistence --tools ""` with the conversation on stdin. No session file is written, so a summary never becomes a session to archive, and the scanner doesn't list print-mode processes. It runs in `plugins/memory/ask/`, an empty folder, so no project's CLAUDE.md is read. A restart stops a call in progress. agentop has no API key and needs none: the call uses the user's own Claude Code sign-in.
   - **What the model reads.** Prompts and replies only, each cut to 1,500 characters, the whole to 40,000: the first third from the start and the rest from the end, where a long session's topic and its outcome are. The system prompt says the conversation is data, not instructions. The answer is JSON: a title under 80 characters, 2 to 5 sentences, and up to 25 keywords: every proper noun as written, then search words in English and in the conversation's language (romaji for Japanese names).
   - **Privacy.** Every session goes to the summarizing tool's provider, whichever tool it was with: a Codex or Antigravity conversation is sent to Anthropic when Claude Code writes the summaries. Settings → Memory says so and turns summaries off (`PATCH /api/v2/plugins/memory/summaries`, kept in the plugin's store); off keeps the summaries already made.
   - **Search.** `GET /api/v2/plugins/memory/search/sessions?q=&tool=&project=&since=&limit=` and `GET /api/v2/plugins/memory/summary?session=&tool=`. `agentop recall` lists matching sessions above the messages, and `--session` starts with the summary. The skill uses the sessions for vague memories and whole-session questions, then reads the messages for the exact detail: a summary is a paraphrase, never quoted.
   - **First run on the devhost (2026-11-12).** 183 sessions were due. Haiku took about 4 seconds each, with titles, decisions, and open items right, and keywords that included commit hashes, file names, and host names.

#### 12.11.4 How other agents remember, and whether keyword search is enough (2026-11-12)

A survey of how agents keep memory, made before deciding what recall needs next. Most agents combine two or three of these:

| Pattern | What it is | Who does it |
|---|---|---|
| **Curated notes** | The agent decides what is worth keeping and writes short facts to a file that goes into every prompt | Hermes Agent (`MEMORY.md`, `USER.md`), Claude Code (memory files, `CLAUDE.md`), ChatGPT's saved memories |
| **Raw history search** | Keep every session, and search it when the agent thinks the past is relevant | Hermes Agent (`~/.hermes/state.db`, SQLite FTS5, a `session_search` tool, with an LLM summarizing what it finds), agentop's recall |
| **Semantic retrieval** | Embed messages or facts and find them by meaning | mem0, Letta/MemGPT's archival memory, Zep (adds a knowledge graph of facts with time), Honcho's `honcho_search` |
| **User model** | Build a profile of the person: preferences, style, goals | Honcho's peer cards, an optional layer for Hermes Agent |
| **Procedural memory** | Remember how to do things, as reusable skills | Hermes Agent writes and refines its own skills; Claude Code skills |

Hermes Agent (Nous Research), known for its memory and personalization, gets that mostly from curated notes, the Honcho user model, and skills it writes itself. Its recall of old conversations is keyword search over FTS5, like agentop's: by default it remembers only what it wrote down or can find again by keyword. The Hermes details come from third-party write-ups and Honcho's docs, not from Nous Research's own documentation.

**Keyword search is the right base for this plugin.** The goal is an exact detail: a name, a link, a place. A URL or a rare surname embeds into nothing meaningful, while a trigram index finds part of it in a millisecond. And the agent does the searching: a miss is not the end, since the recall skill has it try other languages, romaji, fragments, and likely domains, then read around the hits. That loop covers much of what semantic search would add, the way Claude Code searches code with grep.

**Where it falls short:**

1. You remember the meaning but none of the words: "the restaurant my friend recommended", when the message says only "Sushi Dai, go before 6am".
2. Questions across many conversations: "what did I decide about X?" is not one message, and needs a summary or a stored fact.
3. Facts that change: search returns the old answer beside the new one, with nothing marking which is current. Zep's facts with dates are for this.
4. Cost: each recall is several rounds of searching and reading, where a short profile in the prompt costs nothing.

**What follows, in order:**

1. Measure misses: note when a recall finds nothing, or the user says it found the wrong thing. Embeddings come in only if keyword search misses too often (phase 4), and today nothing records misses.
2. Summarize each session: a title, a short summary, and keywords, indexed for search. Cheap, and it fixes much of shortfall 1, since a summary uses ordinary words ("restaurant recommendation") that the messages may lack. This is phase 5.
3. Hybrid search, if misses show up: local embeddings in the same SQLite file (sqlite-vec, say), with their ranking merged with the keyword ranking. Keyword search still wins for exact strings; embeddings catch paraphrases.
4. Leave curated profiles to the tools. Claude Code already keeps its own memory files; agentop's part is the complete record those notes lack. A profile shared across tools may come later, but it is the most work for the least sure gain.

Sources: [mem0 on Hermes Agent's memory](https://mem0.ai/blog/how-memory-works-in-hermes-agent-(and-how-to-improve-it)), [Better Stack's Hermes Agent guide](https://betterstack.com/community/guides/ai/hermes-agent/), [Hermes memory: five layers, one learning loop](https://dev.to/crabtalk/hermes-memory-five-layers-one-learning-loop-39gd), [Honcho's Hermes integration](https://docs.honcho.dev/v3/guides/integrations/hermes).

## 13 Migration

- Existing tmux sessions are not touched. They appear as `running` with their current names and are attachable at once.
- `sessions.json` is extended, not replaced.
- `/api` and `html/` remained until the new UI reached parity. Removed 2026-11-14, with the app moved from `/app/` to `/` (`/app/…` redirects there).
- `agentop start` keeps working with no service running. New session names become `{name}-{rand6}`, so `docs/cli.md` needs updating.
- Dialogue short names (`dia{id}a`, `dia{id}b`) continue to go through `ops.start`.

Parity list for the old dashboard, so nothing was lost by accident:

| Old feature | v2 location |
|-------------|-------------|
| Session table with metrics | Left list plus right panel |
| In-browser terminal | Main area |
| History, resume, delete | History view |
| Dialogues list, detail, stop, remove | Dialogues view, from the dialogue plugin (12.9) |
| Session description | Right panel |
| Bridge URL, AI title | Right panel |
| Copy tmux command | Right panel |
| Light and dark theme | Settings |

## 14 Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Terminal is slow or glitchy in WebKitGTK | Medium | Spike S1 before UI work; `TerminalView` boundary; ghostty-web fallback. Tauri's CEF runtime exists at alpha stage and is not a dependency |
| Japanese IME composition fails in a webview | Medium | The system locale is partly `ja_JP`, so this is tested explicitly in S1 |
| `ws://` blocked from the Tauri origin | Medium | Spike S2, covering remote hosts; plugin fallback |
| Browser rules block a web app connection | Medium | Spike S4; reachability guidance in the form; recommend the page from a local service or the desktop app |
| Tokens or terminal traffic exposed on plain HTTP | Medium, if a user binds directly | Loopback default, startup warning, "Not encrypted" label, tunnel recommended |
| Token stolen from `localStorage` | Low | Strict `script-src`, no third-party scripts, rotation |
| Acting on the wrong machine after a switch | Medium | Service name always visible; stop and update confirmations name the service; `instance_id` decides what is local |
| Mouse mode makes copy awkward | Medium | Spike S3; `mouse` is a config option if it proves worse than the scroll benefit |
| A bad update leaves the service down | Low | Pre-checks, health poll, automatic rollback; agents unaffected |
| Ready-marker detection breaks when agents change their TUI | Medium | The timeout fallback means only slower starts, never failed ones |

## 15 Open decisions

Each has a recommended default, which the plan assumes. D6 and D7 are decided for v2.0, and both are to be revisited in the security review in [section 9 of the plan](v2-plan.md#9-after-v20).

| # | Decision | Default | Alternative |
|---|----------|---------|-------------|
| D1 | Token login for the web app | Required for every client, one-time per browser through `agentop open` | None. Connecting to any reachable service rules out trusting the network or the origin |
| D2 | Web cannot start a stopped service | Accept the difference; show the command | None is possible without a separate always-on helper process |
| D3 | Scroll through `mouse on` per session | On for new managed sessions | Leave off, losing wheel scrollback |
| D4 | Dialogues in the new UI | Ported in the final phase, then moved into a plugin (12.9) | Drop from the UI and keep them CLI-only |
| D5 | Host platforms | Linux; systemd optional | Add macOS, as later work |
| D6 | Plain HTTP on a non-loopback address | **Decided for now:** allowed, with a warning and a label. TODO: revisit | Refuse to start without TLS. Safer, but breaks "connect if reachable" on trusted networks |
| D7 | One shared token per service | **Decided for now:** yes; rotation resets every client. TODO: revisit | Per-client tokens with individual revoke |
| D8 | Where plugins come from (12.2) | Only folders inside the agentop install, with their UI in the app build | Also a user plugin directory, or UI code served by the service. Both let more parties run code in the service or the app, so they wait for the security review |
| D9 | Handoff across hosts (12.10) | Dropped with the handoff plugin (12.10.3). `agentop send` reaches only sessions on its own host | Links from one service to another, each with its own token, after the security review |

## 16 Sources

External claims above were checked against:

- [Tauri: Linux graphics issues](https://v2.tauri.app/develop/debug/linux-graphics/)
- [xterm.js 6.0.0 release](https://newreleases.io/project/npm/@xterm/xterm/release/6.0.0)
- [ghostty-web](https://github.com/coder/ghostty-web)
- [wterm overview](https://betterstack.com/community/guides/scaling-nodejs/w-term-dom/)
- [tauri-runtime-cef](https://docs.rs/crate/tauri-runtime-cef/latest/source/src/runtime.rs)
- [Claude support: Agent SDK and `claude -p` usage credit](https://support.claude.com/articles/15036540) (12.10.2)
- [OpenAI Codex: non-interactive mode](https://developers.openai.com/codex/noninteractive.md) (12.10.2)
- [Chrome: Local Network Access](https://developer.chrome.com/blog/local-network-access)
- [Safari blocking ws://127.0.0.1 from an HTTPS page](https://discussions.apple.com/thread/254087609)
- [Firefox: localhost as potentially trustworthy](https://bugzilla.mozilla.org/show_bug.cgi?id=903966)
- [Codex app documentation](https://developers.openai.com/codex/app)
- [tmux attach-session source](https://www.openssh.com/cgi-bin/cvsweb/src/usr.bin/tmux/cmd-attach-session.c?f=c)

Package versions came from the npm registry on 2026-11-07.
