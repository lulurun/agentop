# Backlog

Everything agreed, suggested, or still to check, and not done yet. It replaces the backlog in the old `plan.md` (section 9), the open items of its phases, and the November 2026 outside review (`review-2026-11.md`); both files are in git history. What is built is described from [0_00_design.md](0_00_design.md).

**Priorities**

| | Means |
|---|---|
| **P1** | Next. Guards everything else, or can hurt the owner's own machine or data |
| **P2** | Soon. A real defect or gap, but rare or with a workaround |
| **P3** | Later. An idea or a nice-to-have, not scheduled |

Review findings keep the review's status: *read in code* (the mechanism is in the code; not seen running), *proven live*, or *idea*. File:line references were checked on 2026-11-19.

## P1

### CI on every push

Nothing runs the tests: `.github/workflows/desktop.yml` only builds and releases on `v*` tags. The agent CLIs update often, and the parts that depend on them break silently: `send_text` (paste then Enter, `agentclis/base.py`), the trust prompt (`agentclis/claude.py`), idle detection (`actor.py`), the screen parsers.

- A workflow on every push: pytest, vitest, `tsc`, `cargo test`. Playwright nightly.
- Idea: a scheduled job that starts each installed CLI in tmux and checks its ready marker and a paste, to catch CLI updates.
- Then the missing tests, first from screens captured from each CLI: `scanner.py`, `actor.py`, the session-meta parsing in `claude.py` and `antigravity.py`, the dialogue `parser.py`, `ClientStore` concurrency. vitest covers pure modules only; the Rust start/stop/adoption test is `#[ignore]` (`desktop/src-tauri/src/service.rs`); the `tmux_server` fixture is opt-in, not autouse.

### Check a saved PID before signalling it

Two places signal a PID read from a file, with no check that it is still the process that was started. After a crash or reboot the PID can be reused, and Stop, Remove, or quitting the app would kill an unrelated process.

- **Dialogue** (*proven live*): the runner's PID in `meta.json` (`plugins/dialogue/ops.py:125`) gets SIGTERM on Stop (`ops.py:525`) and through `_close_open_sessions` (`ops.py:294`) on Remove and when the plugin is turned off.
- **Desktop app** (*read in code*): adopts the PID in `desktop-serve.pid` if `kill(pid, 0)` succeeds, and on exit sends SIGTERM, then SIGKILL (`desktop/src-tauri/src/service.rs`).
- Fix: save the process start time and command line beside the PID, compare before signalling, skip on a mismatch.

### An app-level error boundary

*Read in code.* There is none (`ui/src/main.tsx`). A plugin's `tabLabel` runs outside any boundary (`components/Workbench.tsx`), and `PluginBoundary` renders nothing and never resets (`plugins/Slot.tsx:9`). One render error blanks the one window everything runs in.

- Fix: a top-level boundary with a reload button; a reset key on `PluginBoundary`; wrap plugin calls.

## P2

### Removing a dialogue deletes its project folder

Projects never delete a folder (`agentop project close` keeps it), but a dialogue is a project too, and `agentop dialogue remove` and the Dialogues Remove action `rmtree` its folder (`plugins/dialogue/ops.py`, `remove_dialogue` and `remove_dialogues_by_status`). Decide whether dialogues follow the same rule: mark and drop the entry, keep the folder.


### Service and UI updates in the header

Update can follow `v*` releases (`update_from = "release"`, built 2026-11-18). The desktop app's header offers **Update agentop** (built 2026-11-20); the web app's header has no Update yet, only each service's Status ([design 7.4](0_00_design.md#74-releases-and-the-update-indicator)).

### Desktop app: say which copy is running

When a service already answers on the port, the app uses it (a dev checkout, a unit). It doesn't say whether that is its own copy in `~/.agentop-desktop` or another one.

### Desktop app: trust only its own service

*Read in code.* Whatever answers `/health` on the configured port is treated as the app's service (`desktop/src-tauri/src/service.rs`), and that origin is granted the commands that return the service token and use the keychain (`desktop/src-tauri/src/main.rs`). On a shared host another user's process on that port would get the token. Fix: compare the instance id in `/service` with `service.json` before trusting it.

### Dialogue: write each turn to a file

*Idea.* A turn waits until the screen is still for `idle_seconds` (5 s, 15 s for Antigravity), then scans the scrollback for the BEGIN/END block (`plugins/dialogue/parser.py`). A still screen doesn't mean the turn is done, the text is read back from what the TUI draws, and it depends on each tool's rendering. When no END block is there, the orchestrator pastes a recovery prompt into an agent that may still be working, and fails the turn after its retries (`orchestrator.py`).

- Fix: have each agent write its turn to a file in the dialogue folder, and read the file, not the screen. The done signal stays on tmux (agent hooks were declined, see below).

### Small correctness fixes

Each *read in code*; each rare, each small.

| Where | What | Fix |
|-------|------|-----|
| `cli.py` (`service clients`, `token --revoke-clients`) | `clients.json` has no cross-process lock; a revoke during a renewal could drop one write | The same `flock` as `registry.py` |
| `service/auth.py` | `ClientStore` changes `_clients` on threadpool threads (`require_client` is sync) without a lock: "dictionary changed size" for one request | A lock around `_clients` |
| `service/store.py:368` | `at_limit()` is checked outside the lock, so two starts at once can go over `max_active_sessions` | Check and record under one lock |
| `agentclis/base.py:159` | `--model` goes into the shell command unquoted. Harmless today (only code sets it), injection if it ever came from a scenario or brief | `shlex.quote` |
| `plugins/dialogue/model.py:82` | `meta.json` is written with `write_text` by the runner and the service; the Dialogues feature can read half a file | `jsonfile.write_json_atomic` |
| `plugins/dialogue/model.py:144` | `DialogueMeta.load` falls back to the glob `*{id}`; a short id could match another dialogue | Require exactly one match |
| `history.past_sessions` | `GET /history` writes to the registry (agent IDs it resolves), a side effect in a GET | Move the write into the history scan |
| `ui/src/store.ts` | `act`, `loadHistory` and `resumeHistory` aren't tied to a connect attempt: a late reply can land on another service after a switch | Capture the connection id before the await; drop the result if it changed |
| `ui/src/platform/tauri.ts:89` | `token_set` (keychain) is fire-and-forget, so a new `issuedAt` can pair with an old token and sign the app out | Await the write |
| `ui/src/terminal/TerminalView.tsx` | A 401 on the terminal ticket leaves the tab on "connecting" | Show "signed out" in the tab |
| `desktop/src-tauri/tauri.conf.json:23` | `img-src 'self' data:` may block external Markdown images in the viewer (not checked whether it applies to the service's page) | Check, then allow `https:` images or proxy them |

### Plugins: say what isolation means

*Read in code.* `register` and event handlers run under `wait_for(to_thread(...))`; a timeout leaves the thread running and holding a worker. `api.task` coroutines and async plugin routes run on the event loop, so a blocking call there stalls the whole service, though the `service/plugins.py` docstring says nothing a plugin does can block the loop for long. Re-import clears `sys.modules`, while a timed-out `register` thread keeps running against the old module.

- Fix: call it "fault isolation" in the docstring and [0_12_00_plugins.md](0_12_00_plugins.md#what-registerapi-gets), or run plugin tasks off the loop.

### Features and Memory navigation

From [0_10_ui.md](0_10_ui.md) and [0_12_03_memory.md 6](0_12_03_memory.md#6-the-memory-feature):

- The feature in the URL (`/sessions`, `/dialogues/<id>`), for the back button, deep links and reloading in place.
- A badge on a feature in the feature nav when something there needs attention.
- One call, `navigate(feature, selection)`, for jumps between features; with it, Resume from a Memory conversation, and jumping from a Sessions or History row to its conversation.

## P3

### Sign-in and security

- **Single-use pairing codes**, so the service token never leaves the machine. `agentop service pair` prints a short code (`K7QF-2M9X`), typed into the sign-in form instead of the token; once, 10 minutes. A setting turns the service token off for `POST /api/v2/auth/clients` (it can't be "loopback only": behind a proxy every request is from `127.0.0.1`). The CLI and desktop app would sign in by writing a code into `~/.agentop/state/`. Downside: every new browser, and every sign-in after 12 hours unused, needs a code from a shell. Email code ([0_12_00_plugins.md](0_12_00_plugins.md#email-code-email-code)) already covers most of the phone case; approving a device from one already signed in would be another login plugin.
- **Rotate the service token by itself.** Today only `agentop service token --rotate` does.
- **Close the plain HTTP path** to the devhost, `http://proxy-host:7700/agentop/` (the ops repo's nginx). It has no authentication and carries the token in the clear; the HTTPS address replaces it.

### Features

- **Projects**: the open questions in [0_13_projects.md 10](0_13_projects.md#10-open-questions): review reading transcripts, tasks and reviews in the Projects feature, and a quicker idle check.
- A merged session list across services, and a window per service (client-only; a non-goal in [design 2.2](0_00_design.md#22-non-goals) for now).
- An offline app shell for the web app, so it survives its service stopping.
- Colour in the File viewer's diff view.
- **Start from a prompt** ([0_04_start.md](0_04_start.md)): once the record shows how often the probe and chooser are right, offer the chooser to the Coordinator (`agentop coordinator task start --tier`); a way to read Claude's quota without typing `/usage` into a pane.
- Memory: once recall misses have been noted for a while, read `plugins/memory/recall.jsonl` and decide on hybrid search with embeddings ([0_12_03_memory.md 9](0_12_03_memory.md#9-how-other-agents-remember-and-whether-keyword-search-is-enough)).

### Platforms and install

- Signing and notarizing the macOS app (removes the Open Anyway step).
- A Windows desktop app, running the service in WSL.
- macOS hosts without the desktop app: `LaunchdManager` ([design 7.1](0_00_design.md#71-servicemanager)).
- Non-git install kinds for `agentop service update`.
- Not decided: whether the desktop setup should also ask each agent tool to run `agentop --version` (slow, and costs a model call).

## To check by hand

Not code: runs that need a real PC, display, or second machine. The cases are in [8_manual-tests.md](8_manual-tests.md).

| What | Decides | Cases |
|------|---------|-------|
| Terminal in the desktop webview (was spike S1): WebGL vs DOM vs ghostty-web, a real Claude session, a `yes` flood, resize during output, Japanese IME, CJK width, emoji; Chromium as the baseline | The Linux desktop renderer default, and whether xterm.js stays | C2–C6, H8 |
| Desktop networking (S2): from the `http://127.0.0.1` page, reach loopback, plain `http://` and `https://` on another machine | Whether WebKitGTK needs the plugin fallback | H5 |
| tmux mouse and clipboard (S3): wheel scrollback, mouse to agents, OSC 52, Shift+drag | [Design D3](0_00_design.md#15-decisions) (`mouse on`) | D1–D4 |
| Browser reachability (S4), in Chrome and Firefox, the table in [design 3.2](0_00_design.md#32-connecting-to-services) | The guidance in Connect to a service | B5–B11 |
| Desktop updater end to end: an installed app finds and installs a newer release, sessions survive | That self-update works | H10–H12 |
| The updater signing key (`~/.tauri/agentop-desktop.key`) is backed up somewhere besides the machine that made it. The repository secret is set | Losing it means reinstalling every app by hand | — |
| Update on the real devhost install from the panel, and a commit that fails to start | That rollback works in production | G4, G5 |

## Declined or on hold

Kept so they aren't proposed again.

- **`agentop send` idle check and delivery confirmation** (review 1.2): on hold since 2026-11-18; the owner doesn't see sending into a busy session as a problem. A visible record of handoffs, built from the `[agentop send from …]` prompts Memory already indexes, was part of the same suggestion.
- **Agent hooks or transcript tailing for busy/idle state** (2026-11-19): state stays on tmux signals; agent internals drift between versions.
- **The handoff plugin** (queue, idle-only delivery, audit log, Handoffs view, cross-host handoff): dropped 2026-11-13 ([design D9](0_00_design.md#15-decisions)).
- Deliberate, with their residual risk in [0_06_security.md](0_06_security.md): open CORS with no `Origin` or `Host` allowlist, the global service-token lockout, `/fs/dirs` browsing the whole filesystem, skip-permissions agents.
