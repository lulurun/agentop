# TODO

Work agreed but not started. Details of the v2 work are in [docs/v2-plan.md](docs/v2-plan.md); the tests that need a real user and PC are in [docs/v2-manual-tests.md](docs/v2-manual-tests.md).

## v2.0.0 release

The old dashboard was removed on 2026-11-14, without the parity walk and the day of v2-only use (manual tests J1 and J2), which stay open as checks of v2.

- [ ] Walk the parity table in [v2-design.md section 13](docs/v2-design.md#13-migration) in the web app and the desktop app (J1), and use v2 alone for a day (J2).
- [ ] Tag `v2.0.0` with release notes.

## Security review

The v2 security model is deliberately simple. Review it before recommending that anyone expose a service beyond loopback.

- [ ] Plain HTTP on non-loopback addresses (design D6): keep allowing it, or require TLS or a tunnel.
- [ ] One shared token per service (design D7): move to per-client tokens that can be revoked one at a time.
- [ ] Token expiry: tokens never expire today; only `agentop service token --rotate` replaces one.
- [ ] Rotate the PC service's token, which was pasted into a chat. Accepted for now (2026-11-13): the service is only used by its owner, to try agentop out. Rotate before it is used for anything that matters.
- [ ] Open CORS on `/api/v2`.
- [ ] Token storage in the web app's `localStorage`.
- [ ] What `/api/v2/health` exposes without a token (name, version, instance ID).
- [ ] Agents started with skip-permissions flags, reachable by anyone who holds the token.
- [ ] The HTTPS proxy on pisces-01: public on 443 with only the token in front. Consider an IP allow-list or a second factor at nginx.
- [ ] Stop over the API: `Ctrl-C` then `/exit` is typed into the agent's terminal. Confirm no tool treats either differently in a way that matters.

## Plugin system

Design: [v2-design.md section 12](docs/v2-design.md#12-plugins).

- [x] Plugin loader in the core: scan `src/agentop/plugins/*/plugin.toml`, load each in isolation, keep the plugin list with states.
- [x] `GET /api/v2/plugins` and `PATCH /api/v2/plugins/{id}`, saved flags in `~/.agentop/state/plugins.json`, `plugins` events.
- [x] The `register(api)` surface: scoped router, isolated event handlers, supervised tasks, read-only sessions, per-plugin store.
- [x] UI: per-connection plugin list, slots with error boundaries, Settings → Plugins with switches and errors.
- [ ] Broken test plugins, and the core suites passing with them installed and with none. The broken plugins and the loader's own tests are in `tests/test_plugins.py`; the core suites already run with no plugins. Still to do: run the core pytest and Playwright suites with the broken plugins installed.
- [x] `register_cli` for plugin CLI commands (`plugin_cli.py`); `agentop recall` moved into the Memory plugin with it.
- [x] Move dialogues into the first plugin, `src/agentop/plugins/dialogue/` and `ui/src/plugins/dialogue/` (design 12.9), 2026-11-14. `api.agents` was dropped instead of built: the orchestrator is its own process (design 12.3).
- [x] Prove it: the core suites pass with both dialogue folders deleted; disabling the plugin stops running dialogues (`api.on_disable`, after a warning in Settings), hides every dialogue UI, and leaves other sessions untouched.
- [ ] Then, one at a time: attention indicator, file preview and diff (built in the File viewer), grouping by project.

## Memory plugin

Design: [v2-design.md section 12.11](docs/v2-design.md#1211-memory-agentops-own-record-of-conversations). One host only for now.

- [x] Phase 1: archive. Sweep claude, codex, and antigravity transcripts into `~/.agentop/plugins/memory/archive/`, appending only new bytes, never deleting; status route and Settings section.
- [x] Phase 2: messages table in SQLite (`plugins/memory/messages.sqlite`), prompts and the assistant's text only, rebuilt from the archive. Reads Codex's compressed `.jsonl.zst` transcripts.
- [x] Phase 3: conversation view for transcripts in the File viewer (`agentop/conversation.py`, shared with the messages table); Transcripts list falls back to the archive.
- [x] Phase 4: recall: FTS5 trigram search, `/search` and `/conversation` routes, `agentop recall`, and the `recall` skill (`src/agentop/skills/recall/`).
- [x] Phase 5: session summaries (title, summary, keywords) written by `claude --print` on Haiku, searched beside the messages; `recall` lists matching sessions first.
- [ ] Search in the app: a Memory view that searches and opens the conversation around a hit, with the matching sessions' summaries.
- [ ] Record recall misses (nothing found, or the wrong thing), to decide on hybrid search with embeddings ([design 12.11.4](docs/v2-design.md#12114-how-other-agents-remember-and-whether-keyword-search-is-enough-2026-11-12)).
- [ ] Session summaries from Codex too (`codex exec`), for a host without Claude Code.
- [x] A recall skill for Codex sessions too: agentop serve installs the skills into `~/.codex/skills/` (2026-11-13).
- [ ] Decide whether to raise Claude Code's `cleanupPeriodDays` (default 30 days) as a backstop for a long service outage. Left alone for now.
- [ ] Later: archives from other hosts, aggregated in one place.
