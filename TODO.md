# TODO

Work agreed but not started. Details of the v2 work are in [docs/v2-plan.md](docs/v2-plan.md); the tests that need a real user and PC are in [docs/v2-manual-tests.md](docs/v2-manual-tests.md).

## v2.0.0 release

The old dashboard was removed on 2026-11-14, without the parity walk and the day of v2-only use (manual tests J1 and J2), which stay open as checks of v2.

- [ ] Walk the parity table in [v2-design.md section 13](docs/v2-design.md#13-migration) in the web app and the desktop app (J1), and use v2 alone for a day (J2).
- [ ] Tag `v2.0.0` with release notes.

## Security review

The v2 security model is deliberately simple. Review it before recommending that anyone expose a service beyond loopback.

- [x] Plain HTTP on non-loopback addresses (design D6): refused, 2026-11-14. The service won't start without TLS, and the app won't add such an address; the README has an SSH tunnel guide.
- [x] One shared token per service (design D7): per-client tokens, revoked one at a time from the Status dialog or `agentop service clients`, 2026-11-14. The service token only signs a client in.
- [x] Token expiry: a client's token runs out after 12 hours unused (`client_ttl`) and 30 days at most (`client_max_age`); the app renews it hourly while on screen, 2026-11-14.
- [x] Rotate the PC service's token, which was pasted into a chat: rotated 2026-11-15, after every client had signed in with its own token, so none was signed out.
- [ ] Open CORS on `/api/v2`.
- [x] Token storage in the web app's `localStorage`: only the client's own expiring token is stored, never the service token, 2026-11-14. XSS could still read it: the CSP stays the main defence.
- [ ] What `/api/v2/health` exposes without a token (name, version, instance ID).
- [ ] Agents started with skip-permissions flags, reachable by anyone who holds the token.
- [ ] The HTTPS proxy on pisces-01: public on 443 with only the token in front. Consider an IP allow-list or a second factor at nginx.
- [ ] Stop over the API: `Ctrl-C` then `/exit` is typed into the agent's terminal. Confirm no tool treats either differently in a way that matters.

### Lower priority

Per-client tokens (above) already limit what a leaked browser token can do. This one also makes signing in less convenient, which matters little on a hardened host and a lot for personal use, so it waits.

- [ ] Single-use pairing codes, so the service token never has to leave the machine. Discussed 2026-11-14, not built.
  - Today the service token never expires, and signing in a browser or phone means copying it off the machine (into a chat, a password manager, a phone). Each copy can leak, and whoever has it can sign in clients until it's rotated.
  - `agentop service pair` on the machine prints a short code, such as `K7QF-2M9X`. It is typed into the app's sign-in form instead of the service token, works once, and expires after 10 minutes, so a code that leaks later is useless. Making one needs shell access to the machine (SSH, or sitting at it).
  - A setting turns the service token off for `POST /api/v2/auth/clients` entirely, so pairing codes are the only way in. Without it the codes add little. It can't be "refuse it except from loopback": behind a proxy such as nginx on pisces-01, every request comes from `127.0.0.1`.
  - The CLI and the desktop app then sign in by writing a pairing code into `~/.agentop/state/` and using it, instead of sending the service token. Only something that can write to the user's home can do that, the same trust the `service.json` file gives today.
  - Downside: every new browser or phone, and every sign-in after 12 hours unused, needs a fresh code from a shell on the machine.

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
