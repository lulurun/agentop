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
- Web and desktop look and behave the same, except where section 8.3 says a browser cannot.
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
    manager.py      ServiceManager: systemd or none
    update.py       update check and launch
    files.py        safe directory listing and git status
    models.py       Pydantic request/response models
    routes.py       the /api/v2 application
    legacy.py       the old dashboard's routes, until cutover
    webdist/        built web app (gitignored, shipped as package data)
  tmux.py           all tmux calls go through one wrapper
  ...               existing modules unchanged
ui/                 React + TypeScript + Vite; builds web and desktop frontends
  src/platform/     web.ts, tauri.ts: the only platform-specific code
  src/terminal/     TerminalView: the only code that imports xterm
desktop/            Tauri project (src-tauri/), loads the ui/ build
deploy/             systemd unit, update.sh
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

**Defaults.** The web app always lists the service that served it, labelled "This service". The desktop app lists the local service when `service.json` exists. Each client starts on its last-used connection, or on that default.

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

The current rename exists only to put the PID in the name. Dropping it removes the need for a second identifier and solves P4: the loading row and the running session are the same object from the first moment. The human name becomes `display_name`, editable at any time without touching tmux. Existing `{name}-{pid}` sessions are valid IDs as they are, and the `general-` prefix check in `deploy/ensure-general-session.sh` keeps working.

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

The file stays at `~/.agent-dashboard/sessions.json`, with additive fields: `display_name`, `status`, `step`, `error`, `created_at`, `ended_at`. Records for ended sessions are pruned after 30 days. The old `flock` was held on the temp file, so it protected nothing. Writes now hold a separate lock file, because the CLI and service both write here.

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

1. **Bind to loopback by default.** Exposing a service is an explicit choice: `agentop serve --host`, or `host` in `~/.agent-dashboard/config.toml`.
2. **Require a bearer token on every request**, with no exception for loopback or for any client, except `GET /api/v2/health`, which returns only status, version, `api_version`, `instance_id`, and `name`.
   - A random 256-bit token lives in `~/.agent-dashboard/service.json`, mode `0600`, and is rotated with `agentop service token --rotate`.
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

- A loopback bind does not prove a route is private. The old `/api` routes have no token, so until they are removed their only protection on that path is whatever the proxy on `pisces-01` enforces.
- The proxy has no authentication and no host name, so it stays out of `AGENTOP_ALLOWED_ORIGINS`: the guard refuses any request with forwarding headers on the old routes, and only `/api/v2` and `/app` work through the proxy.

## 8 Service lifecycle

### 8.1 CLI

```bash
agentop serve                 # run in foreground (what the unit executes)
agentop service status        # or start, stop, restart, update, token
agentop open                  # open the web app, logged in
```

`deploy/agentop-api.service` keeps its name, `KillMode=process`, and `ExecStartPost`. Its `ExecStart` becomes `agentop serve`, and it sets `TERM=xterm-256color`.

### 8.2 ServiceManager

```python
class ServiceManager(Protocol):
    def status(self) -> ManagerStatus: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...
```

- `SystemdManager` runs `systemctl --user`. Restart uses `--no-block`, so the service does not wait on its own death.
- `NoManager` is used when the service was started by hand. It reports the capabilities as false, and the UI disables those buttons.
- `LaunchdManager` is added if macOS hosts become a target.

### 8.3 Who can do what

A stopped service has no API, so something else must start it. Only the desktop app can, and only for the service on its own machine. This is the one unavoidable difference between web and desktop.

| Action | Desktop, service on this machine | Any other connection, or web |
|--------|----------------------------------|------------------------------|
| Status | Rust + API | API; when unreachable, shows "disconnected" |
| Start | Rust: `systemctl --user start` | Not possible; shows the command to run on that host |
| Stop | Rust | API, with a warning that it must be started again from that host |
| Restart | Rust | API |
| Update | API | API |

"On this machine" is decided by matching `instance_id` against the local `service.json`, never by the URL. A tunnel to a remote service has a loopback URL, and treating it as local would run `systemctl` against the wrong machine.

In every case, stopping the service does not stop agents. The confirmation dialog says so and names the service being stopped, and the sessions are there on restart.

### 8.4 Update

The current install is an editable git checkout, so v2 implements that install kind. `GET /api/v2/service` reports `install_kind`; other kinds report update as unsupported.

An update must outlive the service it restarts, so it runs in its own transient unit:

```bash
systemd-run --user --unit=agentop-update --collect deploy/update.sh
```

`deploy/update.sh` writes progress to `~/.agent-dashboard/update-status.json`:

1. Refuse if the tree is dirty or not on `main`.
2. Record the current commit, then `git pull --ff-only`.
3. `pip install -e .`, then build the web app.
4. Restart the service, then poll `/api/v2/health` for 30s.
5. On any failure: check out the recorded commit, reinstall, rebuild, restart, and record the failure.

`GET /api/v2/service/update` reports commits available and the status of the last run. Terminals drop briefly and reconnect automatically (6.1); agents are not interrupted.

Any client can now talk to a service of a different version, including the web app when it connects to another host. `/api/v2/health` returns an integer `api_version`, and clients declare the range they support. Outside that range, the connect check (4.5) stops with an explanation. `/health`, `/service`, and `/service/update` must stay compatible across versions, so that an older service can always be updated from a newer client.

## 9 API

All routes are under `/api/v2`. The existing `/api` routes stay until cutover, so the current dashboard keeps working during development.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Status, version, `api_version`, `instance_id`, `name` (no auth) |
| `GET` | `/service` | Name, version, commit, uptime, PID, tmux version, manager, capabilities, `install_kind`, whether TLS is on |
| `POST` | `/service/restart`, `/service/stop` | Lifecycle |
| `GET`, `POST` | `/service/update` | Check and status; start an update |
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

The switcher is a menu at the top of the left column:

```
┌──────────────────────────────────┐
│ SERVICES                         │
│ ✓ ● build-box        This service│
│   ● laptop           3 sessions  │
│   ● gpu-1  ⚠ Not encrypted       │
│   ○ office-mini      Unreachable │
│ ──────────────────────────────── │
│ + Add service…                   │
│ ⚙ Manage services…               │
│ ⓘ build-box status…              │
└──────────────────────────────────┘
```

- Selecting a row switches immediately (4.5).
- Rows show live reachability from `/health`, polled every 15s only while the menu is open. The session count appears only for services with a saved token.
- **Add service**: URL (or pasted `connect-info` string), token, and optional label. A Test button runs the 4.5 checks and shows the service's own name and version before saving. The form warns about unencrypted URLs and likely browser blocks.
- **Manage services**: rename, change URL or token, and remove. Removing a connection deletes the client's record and token. It never affects the service or its sessions.

The status item opens the service panel for the selected service: name, address, version and commit, uptime, tmux version, encryption state, and Stop, Start, Restart, and Update buttons, enabled according to section 8.3. Update shows commits available and live step progress.

### 10.6 Platform layer

`ui/src/platform/` is the only place the two shells differ:

```ts
interface Platform {
  connections: ConnectionStore                // saved list; keychain or localStorage
  defaultConnection(): Promise<Connection | null>
  localControl(c: Connection): Promise<LocalServiceControl | null>
                                              // null on web, and unless instance_id
                                              // matches this machine's service
  copyText(text: string): Promise<void>
}
```

Everything else is shared. A CI check forbids importing `@tauri-apps/*` outside this directory.

## 11 Desktop app

- Tauri 2 window that loads the bundled `ui/` build, not a URL from the service. It has to render when the service is down, which is precisely when its Start button matters.
- A small Rust command set, with no general shell permission: `read_service_info` and `service_control(action)`, which allow-lists `status`, `start`, `stop`, and `restart`, and calls `systemctl --user` directly. It does not rely on `agentop` being on `PATH`, because GUI apps do not inherit shell setup such as pyenv.
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
- The right panel: details, token usage, changed and all files.
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

Only folders inside the agentop install are scanned. Loading code from a user directory such as `~/.agent-dashboard/plugins/` would let anything that can write there run inside the service, so it is left for the security review.

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
| `api.agents` | For plugins that drive agents: start a managed session, send text, read its output, stop it. It goes through the same core code as the UI (`ops.start`, `Actor`, `AgentInstance.stop`), and each session it starts is tagged with the plugin's id. The sessions are ordinary core sessions: they show in the list and can be attached, stopped, and closed whether or not the plugin is still running |
| `api.store` | A JSON file at `~/.agent-dashboard/plugins/{id}.json` for the plugin's own data |
| `api.emit(data)` | Sends `{"type": "plugin", "id": …, "data": …}` on the events socket for the plugin's UI |
| `api.log` | A logger named `agentop.plugins.{id}` |

`register(api)` only declares routes, handlers, and tasks. The loader switches them on after `register` returns in time (5s), so a plugin that fails part way through leaves nothing behind. A plugin's own modules import as `agentop.plugins.{id}.…`, so relative imports work.

Repeated handler errors (for example 5 in a minute) move the plugin to `failed` automatically, so a broken plugin stops being called.

**CLI.** A plugin may add an `agentop` subcommand by defining `register_cli(subparsers)`, declared with `cli = true` in its manifest. The CLI reads manifests and saved flags without starting the service. A missing plugin's command does not exist. A disabled or failed plugin's command is still listed, and running it says so and how to enable it. The core commands never depend on a plugin.

### 12.4 Enable and disable

Python cannot reliably unload a module, so disabling is a gate rather than an unload:

- **Disable**: requests to its routes return 404, its event handlers stop being called, its tasks are cancelled, and its UI disappears. The flag is saved.
- **Enable** of a plugin that was loaded: the gate opens again, and its tasks are started again.
- **Enable** of a plugin that was never imported (disabled at start) is imported then. If that fails, the state is `failed` and nothing else changes.
- **Retry** of a `failed` plugin needs a service restart; the UI says so. This holds for a plugin that failed while running too (a crashed task, repeated handler errors): enabling it again leaves it `failed`, with the restart note in its error. This keeps half-initialised plugin state out of the running service.

Flags live in `~/.agent-dashboard/plugins.json`: `{"attention": {"enabled": false}}`. They belong to the service, not the client, because a plugin is a service feature: disabling it affects every client of that service.

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
- Plugin UI goes into fixed **slots**: a row in the right panel (`right-panel`), a view in the left column's footer, beside History and Dialogues (`left-view`), a badge on a session row (`session-badge`), and a section in Settings (`settings-section`). A manifest naming any other slot is invalid. Core layout code renders each slot from the plugin list and never names a plugin.
- Each slot is wrapped in an error boundary. A plugin component that throws hides itself; the rest of the page keeps rendering.
- Plugin UI calls its own routes only through a client scoped to `/api/v2/plugins/{id}/`.
- A plugin's UI is `ui/src/plugins/{id}/index.tsx`, exporting `plugin`: an object with any of `sessionBadge`, `leftView` (a label and a component), `rightPanel`, and `settingsSection`. Each component gets a context with the plugin's info, its scoped client, and `onEvent` for its `api.emit` messages. The app finds these modules at build time (`ui/src/plugins/registry.ts`).
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
| File preview and diff view | Yes: routes for file content and diffs, a right-panel slot |
| Delegation: one session asks another to do a task and reply | Yes: `agentop ask` and `reply`, a request queue, a Requests view (12.10) |
| Grouping sessions by project | Yes: stores groups, a left-column view |
| A merged session list across services, a window per service | No: client-only. It stays a non-goal (2.2) |
| Offline app shell for the web app | No: client-only |
| Desktop auto-update | No: desktop-only, and a non-goal (2.2) |
| macOS host support (`LaunchdManager`) | No: the core needs it to run at all |
| Non-git install kinds for update | No: part of core service update |

The security review and retiring the old dashboard (both in [TODO.md](../TODO.md)) are core work, not plugins.

### 12.9 Dialogues as a plugin

Dialogues are built already, so moving them is also the test of the plugin system: if the loader and its API can carry dialogues, they can carry the features after it.

**What moves into `src/agentop/plugins/dialogue/`:** today's `src/agentop/dialogue/` package (model, orchestrator, parser, status, scenarios), the `/api/v2/dialogues…` routes, which become `/api/v2/plugins/dialogue/…`, and the `agentop dialogue` CLI commands, through `register_cli`. The orchestrator drives its two agents through `api.agents` instead of calling `ops` and `AgentInstance` directly.

**What moves into `ui/src/plugins/dialogue/`:** the Dialogues view, filling the left-column view slot, and the "dialogue" label on session rows, filling the session-badge slot. Core files stop naming dialogues: the session list, the store, and the API client.

**What stays in the core:** nothing dialogue-specific. The core keeps the generic pieces dialogues needed: starting a session under a given short name, `Actor` send and receive, and stop. A dialogue's agents (`dia{id}a`, `dia{id}b`) remain ordinary managed sessions.

**Data.** Saved dialogues stay where they are, in `~/.agent-dashboard/dialogues/`, in the same format. Removing or disabling the plugin never deletes them, and enabling it again shows them all.

**When the plugin is disabled, removed, or fails:**

- The Dialogues view, the session labels, and the `/plugins/dialogue/` routes disappear. `agentop dialogue` says the plugin is disabled.
- A running dialogue is stopped as if its Stop button were pressed: the relay ends and the dialogue is saved as stopped. Settings warns before disabling while any dialogue is running.
- The dialogue's agent sessions, if still open, stay in the session list as plain sessions that can be attached, stopped, and closed.
- Sessions, terminals, history, and service control are unaffected.

**Done when:** the core test suites pass with the `dialogue` folder deleted, the dialogue tests pass with the plugin enabled, and disabling it from Settings hides every trace of dialogues in the UI without touching other sessions.

### 12.10 Delegation: one session asks another

Status: designed, not started. Plugin id `delegate`.

**The problem.** Context stays where it was built. A qate development session knows a change in detail but not how to deploy it. An eiger session knows how to deploy, and which hosts and environments exist, but not the change. Today there are two ways to bridge them, and both are poor:

1. Load eiger's skills into the qate session. This only works if everything eiger knows is written down, and it breaks across hosts: the qate machine may have no access to production at all.
2. Summarise the change, switch to the eiger session, explain it there, and come back to verify. The user does the copying by hand.

**The workflow.** In the qate session the user writes:

```
@agentop #eiger please deploy k2 for env QIN_TENGFEI
```

The qate agent knows neither how to deploy nor what `QIN_TENGFEI` is. A skill tells it to hand the request over with `agentop ask`, adding what the target needs to know (the change, the branch, what to check). Agentop delivers the request to the eiger session. Eiger deploys and answers with `agentop reply`. Agentop delivers the answer back to the qate session, which still holds the full context of the change and goes on to verify it.

This is not a dialogue (12.9). A dialogue is a loop the orchestrator runs between two new sessions, following a scenario. Delegation is a single request and reply, started by an agent, often to a session that already exists. The two share only the core pieces: starting a session, `Actor` send and receive.

#### 12.10.1 Transport: tmux only

Requests and replies are typed into the agents' terminals through tmux, like every other message agentop sends. Agentop does not call any LLM API, and does not run agents headless (`claude -p`, `codex exec`).

Why:

- The agent CLIs (claude, codex, agy) are built to run in a terminal, and tmux is the native way to run them. Agentop's job is to bring those tmux sessions into one window, not to become a different agent runtime.
- Every delegated task is an ordinary session: the user can see it, attach, take over, or stop it. Nothing runs hidden.
- Billing, login, and permissions stay exactly as they are for interactive use. Interactive sessions are covered by the user's subscription plan. Calling the LLM API directly would need a separate pay-as-you-go API key, and headless runs follow their own rules: on Claude plans, `claude -p` usage moved to a separate monthly Agent SDK credit in June 2026. (See section 16.)
- A new agent CLI works with no new integration, because it is just another terminal program.

tmux is weak at two things, and the design deals with both: knowing when an agent has finished, and getting its answer back reliably. The rest of this section is mostly about those two.

#### 12.10.2 Commands

Agents use these through their shell. Every supported tool can run shell commands, so one CLI serves all of them, and no MCP setup is needed for each tool. An MCP wrapper can be added later if it proves useful.

| Command | Does |
|---------|------|
| `agentop ask <target> "<message>"` | Queues a request and prints its id (`r-7f3a`) at once. It does not wait |
| `agentop reply <id> "<message>"` | Run by the target: answers the request |
| `agentop request <id>` | Status and reply of one request, for the sender to check |
| `agentop requests` | Recent requests on this service |

The sender knows its own session from the environment agentop sets when it starts a managed session. Only managed sessions can send or receive requests.

**Targets.**

| Form | Meaning |
|------|---------|
| `#eiger-a1b2c3` or `#eiger` (a display name) | A live session. Its conversation state matters, or the user wants to watch the work in it |
| `#project:eiger` | A new session started in the eiger project's directory for this one request, closed after the reply. It still has the repo's CLAUDE.md and skills, but does not fill up, or get in the way of, the session the user is working in. Probably the better default for tasks like deploys |

A name that matches more than one session is an error that lists the matches. Cross-host targets (`service/session`) come in phase 2 (12.10.6).

**The skill.** A short skill file is installed for each tool. It teaches two things: when the user writes `@agentop #X …`, send it with `agentop ask`, including the context the target lacks; and when a message arrives that starts with an agentop request header, do the work, then answer with `agentop reply`.

#### 12.10.3 Delivery

A delivered request looks like this in the target's terminal:

```
[agentop request r-7f3a from qate-dev-91c2e0]
please deploy k2 for env QIN_TENGFEI. The change is on branch fix-k2-timeout …
When done, run: agentop reply r-7f3a "<result>"
```

The reply arrives in the sender's terminal the same way, marked `[agentop reply r-7f3a from eiger-a1b2c3]`.

Rules:

- **Deliver only when idle.** Text is typed only when the agent is idle at its input prompt and no client has typed in the session in the last few seconds. Typing while an agent is in the middle of a turn, or while the user is typing, would mangle both. Requests queue per session and go in order.
- **One idle check, in the core.** The same check is needed by the start flow (the ready marker in 5.2) and by the attention plugin (12.8). It belongs in the core and is offered to plugins through `api.sessions()`, so it is written once.
- **Explicit replies.** The answer comes from `agentop reply`, not from reading the screen. Reading the scrollback, using the dialogue delimiter parser (nonce and all), is only a fallback for when an agent finishes without calling `reply`.
- **Status.** `queued` → `delivered` → `replied`, or `timed_out`, `cancelled`, `rejected`. Each request has a timeout (default 30 minutes, settable per request). A timeout is reported back to the sender like a reply, so the sender is never left waiting.
- **Stored.** Requests and replies are kept in `api.store` and shown in a Requests view, with cancel and approve buttons.

#### 12.10.4 Safety

Without delegation, making a session do something takes the token or the keyboard. With it, one agent can make another act, and some agents run with skip-permissions flags and can reach production. A prompt injection into one session (for example from a web page it read) could then make another session deploy. So:

- **Allowlist.** Which sessions or projects may send to which targets. Default: same service only, and every target must be allowed explicitly.
- **Approval.** A target can be marked "approve each request". Its requests wait in the UI until the user clicks Approve. Suggested for anything that touches production.
- **Hop limit.** A request carries its chain of senders. A request made while handling another request may go one hop further (default limit 2). A loop such as A → B → A is refused.
- **Audit log.** Every request, delivery, reply, and refusal, with times and sessions, is kept in the plugin store.

These go into the security review (TODO.md) together with D6 and D7.

#### 12.10.5 Other uses

The same mechanism also covers:

- **Questions.** "#infra, what is QIN_TENGFEI and where are its logs?" or "#frontend, what reads this field?" A mode that only answers questions has much lower risk, so it is a good first feature to ship.
- **Changes across repos.** The backend session changes an API, then asks the frontend or SDK session to update its client.
- **Second opinion from another model.** A claude session asks a codex session to review its diff. That is cheaper than starting a full dialogue.
- **Work tied to a machine.** Benchmarks or slow test suites in a session on a GPU or build host (phase 2).
- **Fan-out.** Ask sessions on several hosts to check logs or versions, then gather the answers (phase 2).
- **Verify, then follow up.** If the qate check fails after the deploy, qate asks eiger for logs. That is a short dialogue the agent starts itself, with no scenario file.

#### 12.10.6 Phases

1. **Same service.** Commands, skill, queue and idle delivery, explicit replies, timeouts, allowlist, approval, hop limit, audit log, the Requests view. Targets are live sessions on the same service.
2. **New sessions for each request.** `#project:<name>` targets: start a session in the project's directory, deliver, close it after the reply or the timeout.
3. **Across hosts.** Today connections belong only to clients (4.5), and services never talk to each other. Cross-host delegation needs links from one service to another, each with its own token, held by the service. That is a core change, and it waits for the security review (per-client tokens, D7) and D9.

## 13 Migration

- Existing tmux sessions are not touched. They appear as `running` with their current names and are attachable at once.
- `sessions.json` is extended, not replaced.
- `/api` and `html/` remain until the new UI reaches parity, then are removed.
- `agentop start` keeps working with no service running. New session names become `{name}-{rand6}`, so `docs/cli.md` needs updating.
- Dialogue short names (`dia{id}a`, `dia{id}b`) continue to go through `ops.start`.

Parity list for the old dashboard, so nothing is lost by accident:

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
| D5 | Host platforms | Linux with systemd | Add macOS with launchd, as later work |
| D6 | Plain HTTP on a non-loopback address | **Decided for now:** allowed, with a warning and a label. TODO: revisit | Refuse to start without TLS. Safer, but breaks "connect if reachable" on trusted networks |
| D7 | One shared token per service | **Decided for now:** yes; rotation resets every client. TODO: revisit | Per-client tokens with individual revoke |
| D8 | Where plugins come from (12.2) | Only folders inside the agentop install, with their UI in the app build | Also a user plugin directory, or UI code served by the service. Both let more parties run code in the service or the app, so they wait for the security review |
| D9 | Delegation across hosts (12.10.6) | Not in phase 1. Later, through links from one service to another, each with its own token, after the security review | Let the sending service use a client connection's token. Simpler, but any service then holds full control of its peers |

## 16 Sources

External claims above were checked against:

- [Tauri: Linux graphics issues](https://v2.tauri.app/develop/debug/linux-graphics/)
- [xterm.js 6.0.0 release](https://newreleases.io/project/npm/@xterm/xterm/release/6.0.0)
- [ghostty-web](https://github.com/coder/ghostty-web)
- [wterm overview](https://betterstack.com/community/guides/scaling-nodejs/w-term-dom/)
- [tauri-runtime-cef](https://docs.rs/crate/tauri-runtime-cef/latest/source/src/runtime.rs)
- [Claude support: Agent SDK and `claude -p` usage credit](https://support.claude.com/articles/15036540) (12.10.1)
- [OpenAI Codex: non-interactive mode](https://developers.openai.com/codex/noninteractive.md) (12.10.1)
- [Chrome: Local Network Access](https://developer.chrome.com/blog/local-network-access)
- [Safari blocking ws://127.0.0.1 from an HTTPS page](https://discussions.apple.com/thread/254087609)
- [Firefox: localhost as potentially trustworthy](https://bugzilla.mozilla.org/show_bug.cgi?id=903966)
- [Codex app documentation](https://developers.openai.com/codex/app)
- [tmux attach-session source](https://www.openssh.com/cgi-bin/cvsweb/src/usr.bin/tmux/cmd-attach-session.c?f=c)

Package versions came from the npm registry on 2026-11-07.
