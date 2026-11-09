# TODO

Work agreed but not started. Details of the v2 work are in [docs/v2-plan.md](docs/v2-plan.md); the tests that need a real user and PC are in [docs/v2-manual-tests.md](docs/v2-manual-tests.md).

## Retire the old dashboard

Do this only after the parity walk passes and a full day of normal use needs no fallback to the old dashboard (manual tests J1 and J2).

- [ ] Walk the parity table in [v2-design.md section 13](docs/v2-design.md#13-migration), item by item, in both the web app and the desktop app.
- [ ] Use v2 alone for a full day of normal work.
- [ ] Serve the web app at `/` instead of `/app/`.
- [ ] Delete `html/index.html`, the old `/api` routes, the `/ws/sessions` endpoint, and `HostOriginGuard`'s special handling for them.
- [ ] Retire `run_app.sh`. The devhost's `agentop-api` unit, which runs `agentop serve`, is in Eiger.
- [ ] Update `README.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/dashboard.md`, and `docs/cli.md`, including the stale unit name and registry path.
- [ ] Document exposing a service safely: SSH tunnel, HTTPS proxy, or TLS, and what plain HTTP gives away.
- [ ] Tag `v2.0.0` with release notes.

## Security review

The v2 security model is deliberately simple. Review it before recommending that anyone expose a service beyond loopback.

- [ ] Plain HTTP on non-loopback addresses (design D6): keep allowing it, or require TLS or a tunnel.
- [ ] One shared token per service (design D7): move to per-client tokens that can be revoked one at a time.
- [ ] Token expiry: tokens never expire today; only `agentop service token --rotate` replaces one.
- [ ] Rotate the PC service's token, which was pasted into a chat.
- [ ] Open CORS on `/api/v2`.
- [ ] Token storage in the web app's `localStorage`.
- [ ] What `/api/v2/health` exposes without a token (name, version, instance ID).
- [ ] Agents started with skip-permissions flags, reachable by anyone who holds the token.
- [ ] The HTTPS proxy on pisces-01: public on 443 with only the token in front. Consider an IP allow-list or a second factor at nginx.
- [ ] Stop over the API: `Ctrl-C` then `/exit` is typed into the agent's terminal. Confirm no tool treats either differently in a way that matters.

## Plugin system

Design: [v2-design.md section 12](docs/v2-design.md#12-plugins).

- [x] Plugin loader in the core: scan `src/agentop/plugins/*/plugin.toml`, load each in isolation, keep the plugin list with states.
- [x] `GET /api/v2/plugins` and `PATCH /api/v2/plugins/{id}`, saved flags in `~/.agent-dashboard/plugins.json`, `plugins` events.
- [x] The `register(api)` surface: scoped router, isolated event handlers, supervised tasks, read-only sessions, per-plugin store.
- [x] UI: per-connection plugin list, slots with error boundaries, Settings → Plugins with switches and errors.
- [ ] Broken test plugins, and the core suites passing with them installed and with none. The broken plugins and the loader's own tests are in `tests/test_plugins.py`; the core suites already run with no plugins. Still to do: run the core pytest and Playwright suites with the broken plugins installed.
- [ ] `api.agents` for plugins that drive agents, and `register_cli` for plugin CLI commands.
- [ ] Move dialogues into the first plugin, `src/agentop/plugins/dialogue/` and `ui/src/plugins/dialogue/` (design 12.9): package, routes under `/api/v2/plugins/dialogue/`, `agentop dialogue` through `register_cli`, the Dialogues view and the session-row label through UI slots. Saved dialogues stay in place.
- [ ] Prove it: the core suites pass with the dialogue folder deleted; disabling the plugin stops running dialogues, hides every dialogue UI, and leaves other sessions untouched.
- [ ] Then, one at a time: attention indicator, file preview and diff, grouping by project.

## Delegation plugin

Design: [v2-design.md section 12.10](docs/v2-design.md#1210-delegation-one-session-asks-another). Needs the plugin system first.

- [ ] Idle check in the core (agent idle at its prompt, no recent typing), shared with the start flow's ready marker and the attention plugin.
- [ ] Sender identity: an environment variable set in every managed session.
- [ ] Phase 1: `agentop ask`, `reply`, `request`, `requests`; a queue for each target session; delivery only when idle; timeouts; the scrollback parser as a fallback.
- [ ] Phase 1 safety: allowlist, per-target approval, hop limit, audit log.
- [ ] Phase 1 UI: Requests view with status, cancel, and approve.
- [ ] Skill files for claude, codex, and agy that teach `@agentop #X` and how to answer a request.
- [ ] Phase 2: `#project:<name>` targets, which start a new session for each request.
- [ ] Phase 3: delegation across hosts (D9), after the security review.
