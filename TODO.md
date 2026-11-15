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
- [x] Open CORS on `/api/v2`: kept open, reviewed 2026-11-15. The app must reach services on other origins, and there are no cookies or other ambient credentials, so the token decides access, not the origin. A foreign page gets 401 everywhere except `/health` and signing in with the service token.
- [x] Token storage in the web app's `localStorage`: only the client's own expiring token is stored, never the service token, 2026-11-14. XSS could still read it: the CSP stays the main defence.
- [x] What `/api/v2/health` exposes without a token: only `{"status": "ok"}` since 2026-11-15. Name, version and `api_version` moved behind the token, in `/service`. The app no longer pins an `instance_id`, since a host runs one service, and no longer refuses a service over its `api_version`: `/api/v2` only grows, and a higher `api_version` shows that the app is out of date.
- [x] Agents started with skip-permissions flags, reachable by anyone who holds the token: kept, reviewed 2026-11-15. Skipping permissions is on purpose: agentop is for developers on their own machines, who could start the CLIs that way themselves. So a token is as good as a shell as the user, and the token is the boundary: TLS only, per-client expiring tokens that can be revoked, and a closed `/health` (above).

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
- [x] Broken test plugins, and the core suites passing with them installed and with none, 2026-11-15. The broken plugins are in `tests/broken_plugins.py`; `AGENTOP_PLUGIN_DIRS` installs them next to the built-in ones (the commands are in that file). With all 14 installed, pytest passed 358 of 358 and Playwright 52 of 53; the one that failed, a proxy test in `remote.spec.ts`, then passed 4 times out of 4 on its own. Both run slower, since `register-hangs` holds every service start for the full 5s register timeout.
- [x] `register_cli` for plugin CLI commands (`plugin_cli.py`); `agentop recall` moved into the Memory plugin with it.
- [x] Move dialogues into the first plugin, `src/agentop/plugins/dialogue/` and `ui/src/plugins/dialogue/` (design 12.9), 2026-11-14. `api.agents` was dropped instead of built: the orchestrator is its own process (design 12.3).
- [x] Prove it: the core suites pass with both dialogue folders deleted; disabling the plugin stops running dialogues (`api.on_disable`, after a warning in Settings), hides every dialogue UI, and leaves other sessions untouched.
- [x] Attention indicator: the `attention` plugin, 2026-11-15 (design 12.8). It runs on the 2s `scan` tick: measured live at about 6 ms a tick for 11 sessions. Settings shows what each tick costs, and turning the plugin off stops it entirely.
- [x] File viewer: file preview, diff against HEAD, Markdown and images, and syntax highlighting, completed 2026-11-15 (design 12.8). Grouping sessions by project, the last planned plugin, is in the backlog in [docs/plugins.md](docs/plugins.md#backlog).

## Workspaces

Design: [v2-design.md section 10.7](docs/v2-design.md#107-workspaces-2026-11-15), agreed 2026-11-15.

- [x] Phase 1, the shell, built 2026-11-15: workspaces in the header (switcher, then the workspaces, the current one highlighted), one tab layout per workspace per connection, pinned default tabs, the side each tab kind opens on, Sessions tabs kept mounted while hidden, old saved tabs moved into Sessions. Settings becomes the gear with display options only; Status becomes a tab with the plugins in it. The `left-view` plugin slot becomes `workspace`, and Dialogues moves into one as it is. Keyboard: Ctrl+Shift+1 to 9, Ctrl+Shift+N per workspace.
- [x] Phase 2, History, built 2026-11-15: the list (search, filter by CLI, sort by last updated or CLI) in its pinned tab, and the selected past session on the right with Resume and Remove.
- [ ] History stats on the right: messages, tokens, transcript size. `/saved-sessions` only has the title, tool, directory and last active time today.
- [ ] Dialogues workspace: designed in its own conversation (list on the left, the two actors split in tabs, view only; the dialogue's files on the right opening in the right group; New dialogue in the header). Dialogue sessions leave the Sessions list, and a plugin claims a session in a way the core can read without naming the plugin.
- [x] Memory workspace, 2026-11-15: search on the left, a conversation per tab opened at the hit, the session's summary on the right (design 12.11.3, phase 6).
- [ ] Later: the workspace in the URL, a badge on a workspace that needs attention, `navigate(workspace, selection)` (design 10.7.6).

## Memory plugin

Design: [v2-design.md section 12.11](docs/v2-design.md#1211-memory-agentops-own-record-of-conversations). One host only for now.

- [x] Phase 1: archive. Sweep claude, codex, and antigravity transcripts into `~/.agentop/plugins/memory/archive/`, appending only new bytes, never deleting; status route and Settings section.
- [x] Phase 2: messages table in SQLite (`plugins/memory/messages.sqlite`), prompts and the assistant's text only, rebuilt from the archive. Reads Codex's compressed `.jsonl.zst` transcripts.
- [x] Phase 3: conversation view for transcripts in the File viewer (`agentop/conversation.py`, shared with the messages table); Transcripts list falls back to the archive.
- [x] Phase 4: recall: FTS5 trigram search, `/search` and `/conversation` routes, `agentop recall`, and the `recall` skill (`src/agentop/skills/recall/`).
- [x] Phase 5: session summaries (title, summary, keywords) written by `claude --print` on Haiku, searched beside the messages; `recall` lists matching sessions first.
- [x] Search in the app: the Memory workspace, 2026-11-15 (design 12.11.3, phase 6).
- [x] Record recall misses (nothing found, or the wrong thing), 2026-11-15: every search and each miss in `plugins/memory/recall.jsonl`, from `agentop recall --missed` (the skill runs it when a recall fails) and the app's Didn't find it?. Counts in Settings → Memory.
- [ ] Once misses have been noted for a while, read `recall.jsonl` and decide on hybrid search with embeddings ([design 12.11.4](docs/v2-design.md#12114-how-other-agents-remember-and-whether-keyword-search-is-enough-2026-11-12)).
- [x] Session summaries from Codex too, for a host without Claude Code, 2026-11-15: `codex exec --ephemeral` on `gpt-6-luna`, with no shell tool and without the user's config. Claude Code still comes first where both are installed.
- [x] A recall skill for Codex sessions too: agentop serve installs the skills into `~/.codex/skills/` (2026-11-13).
- [x] Claude Code's `cleanupPeriodDays` (default 30 days) as a backstop for a long service outage: decided 2026-11-15 not to change it. agentop is a wrapper on top of the CLIs and leaves their settings as they are; the archive sweep is the backstop.
- [ ] Later: archives from other hosts, aggregated in one place.
