# TODO

Work agreed but not started. Details of the v2 work are in [docs/v2-plan.md](docs/v2-plan.md); the tests that need a real user and PC are in [docs/v2-manual-tests.md](docs/v2-manual-tests.md).

## Retire the old dashboard

Do this only after the parity walk passes and a full day of normal use needs no fallback to the old dashboard (manual tests J1 and J2).

- [ ] Walk the parity table in [v2-design.md section 13](docs/v2-design.md#13-migration), item by item, in both the web app and the desktop app.
- [ ] Use v2 alone for a full day of normal work.
- [ ] Serve the web app at `/` instead of `/app/`.
- [ ] Delete `html/index.html`, the old `/api` routes, the `/ws/sessions` endpoint, and `HostOriginGuard`'s special handling for them.
- [ ] Retire the old `agentop.service` unit and `run_app.sh` in favour of `agentop-api.service`.
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

- [ ] Plugin loader in the core: scan `src/agentop/plugins/*/plugin.toml`, load each in isolation, keep the plugin list with states.
- [ ] `GET /api/v2/plugins` and `PATCH /api/v2/plugins/{id}`, saved flags in `~/.agent-dashboard/plugins.json`, `plugins` events.
- [ ] The `register(api)` surface: scoped router, isolated event handlers, supervised tasks, read-only sessions, per-plugin store.
- [ ] UI: per-connection plugin list, slots with error boundaries, Settings → Plugins with switches and errors.
- [ ] Broken test plugins, and the core suites passing with them installed and with none.
- [ ] Then, one at a time: attention indicator, file preview and diff, grouping by project.
