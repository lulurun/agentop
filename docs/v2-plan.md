# Agentop v2 Implementation Plan

This plan implements [v2-design.md](v2-design.md). It assumes the defaults in the design's section 14. Changing D1 or D4 changes Phases 1 and 6.

## 1 Overview

| Phase | Outcome | Needs | Size |
|-------|---------|-------|------|
| 0 | Security fix shipped; unknowns resolved | — | S |
| 1 | Service core with the v2 API | 0 | L |
| 2 | Web app: sessions, loading, terminal | 1 | L |
| 3 | Right panel: details, usage, files | 2 | M |
| 4 | Service control and update | 2 | M |
| 5 | Tauri desktop app | 2, 4 | M |
| 6 | Parity and cutover | 3, 4, 5 | M |

```
0 ──► 1 ──► 2 ──┬──► 3 ───────────┐
                └──► 4 ──► 5 ─────┴──► 6
```

Phases 3 and 4 can run in parallel. Sizes are relative: S is a day or less of focused work, M a few days, L about a week.

Rules for every phase:

- The current dashboard keeps working until Phase 6.
- Tests never touch the live tmux server; they use a private socket.
- Each phase ends in a state that can be merged and deployed.

## 2 Phase 0: Security fix and spikes

### 2.1 Ship the Origin and Host fix now

The WebSocket exposure (design P1) exists in production today and should not wait for v2.

- [ ] Add Host and Origin checks to the current `src/api.py`, as HTTP middleware and on the WebSocket handshake.
- [ ] Accept loopback hosts on any port, plus entries from an `AGENTOP_ALLOWED_ORIGINS` environment variable.
- [ ] Treat this as a stopgap for the old routes only. The v2 API replaces it with tokens (design 7.1).
- [ ] Before deploying, set `AGENTOP_ALLOWED_ORIGINS` to the public host name of the proxy on `pisces-01`, so that access is not cut off. The dashboard reaches it through `eiger-tunnel` (design 7.3).
- [ ] Check that the proxy on `pisces-01` requires authentication. Until cutover, it is the only thing protecting the old routes on that path.

**Done when:** the handshake with `Origin: https://evil.example` that was accepted on 2026-11-07 is rejected, and the dashboard still works through every access path in use.

### 2.2 Spikes

Each spike is throwaway code that outputs a decision, recorded back in the design doc. S1 and S2 need Rust: `rustup` and `cargo tauri`.

**S1: Terminal in WebKitGTK.** Minimal Tauri window with xterm.js attached to a real session through the existing WebSocket.

- [ ] Compare WebGL, DOM, and ghostty-web on the desktop session, not over SSH.
- [ ] Test a real Claude Code session, a `yes`-style flood, and a resize during output.
- [ ] Test Japanese IME composition, CJK width, and emoji.
- [ ] Repeat in Chromium as the baseline.

*Decides:* the default renderer on Linux desktop, and whether xterm.js remains the choice. If input lag or dropped frames make it visibly worse than the Chromium baseline with every option, stop and review the Tauri decision before Phase 2.

**S2: Tauri networking.** From `tauri://localhost`, connect with a strict script CSP to:

- [ ] `http://127.0.0.1` and `ws://127.0.0.1`
- [ ] Plain `http://` and `ws://` on another machine
- [ ] `https://` and `wss://` on another machine

*Decides:* direct connection, or the plugin fallback in `platform/tauri.ts`.

**S3: tmux mouse and clipboard.** With `mouse on` for one session, in xterm.js:

- [ ] Wheel scroll enters tmux scrollback, and still reaches agents that request mouse input.
- [ ] Tmux selection reaches the system clipboard over OSC 52, on web and desktop.
- [ ] Shift+drag gives a plain xterm selection.

*Decides:* D3.

**S4: Browser reachability.** Check the table in design 4.5 in Chrome and Firefox, including WebSockets.

- [ ] Page on `http://127.0.0.1`, connecting to another machine over `http` and `https`.
- [ ] Page through `ssh -L`, connecting to a second tunnel.
- [ ] Page on a plain `http://` LAN address, connecting to loopback.
- [ ] Page on `https://`, connecting to `http://` and to loopback, including the Local Network Access prompt.

*Decides:* the guidance shown in Add Service, and any correction to the table.

**Phase done when:** all four decisions are written into the design doc.

## 3 Phase 1: Service core

Goal: a complete v2 API, usable with curl and a test client, running alongside the old routes.

### 3.1 Packaging and tmux wrapper

- [ ] Move `src/api.py` to `src/agentop/service/app.py` as an app factory. Add `agentop serve`.
- [ ] Change the unit to `ExecStart=agentop serve`, and add `Environment=TERM=xterm-256color`.
- [ ] Add `~/.agent-dashboard/config.toml`: `host`, `port`, `name`, `tls_cert`, `tls_key`, with matching `agentop serve` flags. Loopback stays the default.
- [ ] Route every tmux call through one wrapper in `tmux.py`, including those in `scanner.py` and `agentcli.py`. It adds a socket option and exact targets: `=id` for sessions, `=id:` for panes.
- [ ] Add `pytest` as a dev dependency, a `tests/` directory, and a fixture that provides a private tmux server.

### 3.2 Session lifecycle

- [ ] Choose `{slug}-{rand6}` up front in `_run_in_tmux`. Remove the temp name and rename.
- [ ] Add `on_progress` to `start_session` and `resume_session`. Keep the synchronous return value for `ops.start` and dialogues.
- [ ] Report a real failure when no agent process appears, replacing today's `ok: True, pid: None`.
- [ ] Add agent ready markers, and stop `post_start_hook` as soon as one is visible. Keep the timeout.
- [ ] Set `mouse on` per session at creation, according to S3.
- [ ] Add the registry fields, and prune ended records after 30 days.
- [ ] Update `docs/cli.md` for the new name format.

### 3.3 SessionStore and API

- [ ] Build `SessionStore`: merged state, 2s and 10s ticks, the state machine, change events.
- [ ] On boot, reconcile `starting` records.
- [ ] Define Pydantic models in `service/models.py`.
- [ ] Implement `/api/v2`: health, tools, fs/dirs, sessions (with 202 async create and resume), stop, delete, saved sessions.
- [ ] Add the events WebSocket: snapshot, then deltas.

### 3.4 Terminal bridge

- [ ] Rewrite the bridge per design 6.1: `add_reader`, `cols` and `rows` at connect, awaited cleanup, `-u` and `TERM`, direct tmux existence check.
- [ ] Add close codes and the `closed` control frame.

### 3.5 Auth

- [ ] Generate `service.json` at mode `0600` on first run, with `token`, `instance_id`, and `name`.
- [ ] Add bearer middleware on `/api/v2`, with open CORS and no credentials mode, outside auth so preflights pass.
- [ ] Add tickets: single use, 30s, bound to a purpose.
- [ ] Add `agentop open`, and `agentop service connect-info` with a `--host` option for the address other machines use.
- [ ] Add TLS through uvicorn, and a startup warning for non-loopback plain HTTP.
- [ ] Keep the old `/api` routes behind the Phase 0 checks, and do not register them when bound to a non-loopback address. They have no token, so they must never be exposed.

### 3.6 Tests and acceptance

- [ ] State machine: success, missing binary, timeout, agent exit, tmux killed, service restart during start.
- [ ] Prefix safety: with `foo-1` and `foo-10` both present, each action hits only its target.
- [ ] Auth matrix: no token, wrong token, reused ticket, expired ticket, a ticket for a different session, and a foreign Origin without a token (rejected) and with one (accepted).
- [ ] CORS: a preflight from an arbitrary origin succeeds, and a foreign origin can read `/health` and nothing else.
- [ ] Old routes are absent on a non-loopback bind.
- [ ] Path confinement, including symlinks that leave the directory.
- [ ] PTY leak: 100 connect and disconnect cycles leave no extra file descriptors, zombies, or tmux clients.
- [ ] Flood: `yes` in a session with a slow client. The service stays responsive, and the terminal recovers.

**Done when:**

- `POST /sessions` returns within 100ms, and events go `starting`, then `running`, with the terminal attachable on the first try.
- A start with a missing binary ends as `failed`, with the shell visible.
- Restarting the service leaves all sessions intact.
- `agentop start` and a dialogue both still work with the service stopped.
- A second service on another port or machine answers a test client with its own `instance_id`, and the first service's token is rejected there.

## 4 Phase 2: Web app

Goal: the main workflow from the design, in a browser.

### 4.1 Foundation

- [ ] Scaffold `ui/` with Vite, React, and TypeScript. Generate the API client from OpenAPI, with a CI check for drift.
- [ ] Serve the build from the service at `/app` during development, moving to `/` at cutover. Ship `webdist/` as package data.
- [ ] Add theme tokens for light and dark, and the `Platform` interface with `web.ts`.
- [ ] Add a CSP with `script-src 'self'`.
- [ ] Add the connection layer: saved list, active connection per tab, token from the URL fragment, API and socket clients built per connection, not global.
- [ ] Add the Zustand store, keyed by `(connection id, session id)` from the start and fed from the events socket with automatic reconnect.
- [ ] Add the first-visit screen for a missing token.

### 4.2 Shell and terminal

- [ ] Three-column layout with resizable, collapsible sides, remembered per client, and the overlay mode below 900px.
- [ ] Session list with status, filter, and selection.
- [ ] `TerminalView`: all xterm imports, addons, renderer setting, fit on resize, reconnect with backoff.
- [ ] All main-area states from design 10.2.
- [ ] Keyboard shortcuts from design 10.4, and the `beforeunload` guard.

### 4.3 New session

- [ ] Modal with tool list, directory picker from `/fs/dirs`, and optional name.
- [ ] Optimistic row, loading state with step text, then automatic attach.
- [ ] Failed state with retry.
- [ ] Stop, Close, and Resume actions.

### 4.4 Service switching

- [ ] Add the connect checks from design 4.5, with a distinct message for each failure.
- [ ] Add the switcher menu, with live reachability while open.
- [ ] Add the Add Service form: Test, `connect-info` parsing, encryption and browser-block warnings.
- [ ] Add Manage Services: rename, edit, and remove.
- [ ] Switching flow: close sockets, loading state, restore that connection's selected session.
- [ ] Show the service name in the header when the left column is collapsed.

### 4.5 Tests and acceptance

- [ ] Vitest for store logic, the status-to-view mapping, and connection checks, including a changed `instance_id`.
- [ ] Playwright with a fake agent script in a private tmux server: create, loading, attach, type, see output, stop.
- [ ] Playwright with two services on different ports: add, switch, and confirm each one's sessions stay separate, including two sessions with the same ID.

**Done when:**

- A new session goes from form to working terminal with no manual refresh.
- Closing the tab, then reopening it, returns to the same session with its screen intact.
- Restarting the service while attached reconnects the terminal without user action.
- Ctrl+B reaches tmux, and collapsing a column refits the terminal with no leftover wrapping.
- External tmux sessions can be attached, and non-tmux ones explain why not.
- Adding a second service with a `connect-info` string and switching to it shows that service's sessions and terminal, with no reload.
- Switching away and back returns to the same session, and agents on the first service kept running throughout.
- Stopping the selected service leaves the app able to switch to another one.
- A port reused by a different service triggers the warning before any token is sent to it.

## 5 Phase 3: Right panel

- [ ] Identity group: inline-editable name and description, tool, status, directory, copyable tmux attach command, bridge URL.
- [ ] Process group: PID, uptime, memory.
- [ ] Usage group: tokens by type, using the existing `token_usage`; shows "not available" for tools that do not report it.
- [ ] `files.py` and routes: lazy directory listing, git branch, dirty state, and changed files. Respect `.gitignore` where the directory is a repository, and handle non-repositories.
- [ ] Files UI with Changed and All tabs, and copy-path. Refresh on open, and every 10s only while the panel is visible.

File preview and diff views are deferred until after v2.0.

**Done when:**

- All fields update live with no reload.
- Large repositories stay responsive, since only opened directories load. Test on this repository and on the largest one in `~/workspace`.
- A path that escapes the working directory returns 403.
- Fields a tool does not report are shown as unavailable, not as zero.

## 6 Phase 4: Service control

- [ ] Add `ServiceManager`, `SystemdManager`, and `NoManager`.
- [ ] Add `agentop service status|start|stop|restart|token`.
- [ ] Add `/service` routes and capability reporting.
- [ ] Add `deploy/update.sh` with pre-checks, status file, health poll, and rollback, plus `update.py` to launch it through `systemd-run`.
- [ ] Add `agentop service update`.
- [ ] Add the service panel, opened from the switcher, with disconnected view and update progress. It always acts on the selected service, and its confirmations name that service.

Testing update needs care, since this is the production install:

- [ ] Test `update.sh` against a throwaway clone with a separate port, unit, and registry path, before any use on the real service.
- [ ] Simulate each failure: dirty tree, non-fast-forward, build failure, and a commit that fails health.

**Done when:**

- Restart from the UI works, and the page reconnects by itself.
- Stop from the UI shows a clear stopped state, and `systemctl --user start agentop-api` brings it back.
- An update applies and the new version shows in the panel.
- A deliberately broken commit rolls back automatically and reports the failure.
- Restart and update work the same on a remote connection, and a remote Stop warns that the app cannot start it again.
- Throughout all of the above, every tmux session survives. Compare `tmux ls` before and after.

## 7 Phase 5: Desktop app

- [ ] Create the Tauri project in `desktop/`, loading the `ui/` build.
- [ ] Add `platform/tauri.ts`, with the S2 outcome and the S1 renderer default.
- [ ] Add the Rust commands `read_service_info` and `service_control`, with no general shell permission, and a strict CSP.
- [ ] Implement `ConnectionStore` with tokens in the OS keychain, and the default connection from `service.json`.
- [ ] Decide what is local by `instance_id`, as in design 8.3. Test this with a tunnel whose loopback URL points at another machine.
- [ ] Add a first-run view for when no service is installed.
- [ ] Build a Linux package. Add a `make` or `npm` script that builds the whole project.

**Done when:**

- Running both side by side, the desktop app and web app show the same state, with the same features, apart from the items in design 8.3.
- With the service stopped, the app opens, shows it as stopped, and starts it.
- Closing the app leaves the service and all sessions running.
- Services reached by `ssh -L`, LAN address, and HTTPS proxy all connect, and the app can switch among them.
- Through a tunnel, Start is hidden and `systemctl` is never run for that connection, even though its URL is loopback.
- The S1 tests pass in the final build, including Japanese input.

## 8 Phase 6: Parity and cutover

- [ ] History view: list, filter, resume through the normal start flow, delete.
- [ ] Dialogues view: list, detail, stop, remove. Show dialogue agents with a label in the session list.
- [ ] Settings: theme, renderer, font size.
- [ ] Walk the parity table in design section 12, item by item.
- [ ] Move the web app to `/`. Delete `html/index.html` and the old `/api` routes and `/ws/sessions` endpoint.
- [ ] Update `README.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/dashboard.md`, and `docs/cli.md`, including the stale unit name and registry path.
- [ ] Document exposing a service safely: tunnel, proxy, or TLS, and what plain HTTP gives away.
- [ ] Add a tag and release notes for `v2.0.0`.

**Done when:** every row of the parity table works in both clients, and a full day of normal use needs no fallback to the old dashboard. Removal happens only after that.

## 9 After v2.0

### 9.1 Security review (TODO)

The v2.0 security model is deliberately simple and is to be revisited. Do this before recommending that anyone expose a service beyond loopback.

- [ ] Plain HTTP on non-loopback addresses (design D6): decide whether to keep allowing it, or to require TLS or a tunnel.
- [ ] Shared token per service (design D7): decide whether to move to per-client tokens with individual revoke.
- [ ] Review the rest of design section 7: open CORS, token storage in `localStorage`, what `/health` exposes without auth, and agents running with skip-permissions.

### 9.2 Features

- File preview and diff view.
- Grouping sessions by project.
- A merged session list across services, and a separate window per service.
- Offline app shell for the web app, so it survives its service stopping.
- Attention indicator for agents waiting on input.
- Desktop auto-update.
- macOS host support through `LaunchdManager`.
- Non-git install kinds for `agentop service update`.
