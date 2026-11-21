## Quick reference

| Task | Where to look |
|------|---------------|
| Start here: every part of agentop, and why it is built that way | [docs/0_00_design.md](docs/0_00_design.md) |
| CLI commands | [docs/0_08_cli.md](docs/0_08_cli.md) |
| Code structure | [docs/0_03_architecture.md](docs/0_03_architecture.md) |
| UI layout: names of its parts, where tabs open, keys | [docs/0_10_ui.md](docs/0_10_ui.md) |
| Projects: a goal, and a Coordinator session that drives other agents to reach it | [docs/0_13_projects.md](docs/0_13_projects.md) |
| Plugins: how they work, and each one | [docs/0_12_00_plugins.md](docs/0_12_00_plugins.md), and `docs/0_12_<number>_<id>.md` for the larger ones (dialogue, memory, input) |
| Service and app setup | [docs/1_setup.md](docs/1_setup.md) |
| Build and release (UI, desktop app) | [docs/1_setup.md](docs/1_setup.md#building-and-releasing), `scripts/` |
| Manual tests (real user and PC) | [docs/8_manual-tests.md](docs/8_manual-tests.md) |
| Backlog: everything not done yet, by priority | [docs/9_backlog.md](docs/9_backlog.md) |

A doc of its own holds the detail of its part; `0_00_design.md` sums each part up and links there. Doc names sort in reading order: the index is `0_00_design.md`; a section's own doc is `0_<section, 2 digits>_<name>.md` (`0_03_architecture.md`); the plugins page is `0_12_00_plugins.md`, and a plugin's own doc is `0_12_<number>_<id>.md`, with the number from the table there. The other docs are `1_setup.md`, `8_manual-tests.md` and `9_backlog.md`.

## Key facts

- `agentop serve` runs the service at `http://127.0.0.1:9775`: the app at `/`, the API at `/api/v2`. agentop ships no systemd unit or proxy config; running it under one is up to the machine's owner ([docs/1_setup.md](docs/1_setup.md))
- Supported agent tools: `claude`, `codex`, `antigravity`
- Every agentop session belongs to a project, in `~/.agentop/projects/<name>/`; Sessions is the `default` project, and each dialogue is a project too
- Only sessions started by agentop (managed) can be stopped or sent prompts

## Running the service

```bash
agentop serve                              # foreground, http://127.0.0.1:9775
agentop service status                     # or restart, stop, update
```

## README

The top of `README.md`, from the title through "What is agentop" (the intro video link included), is written and managed by hand. Leave it alone unless asked to change it specifically: no edits, rewording, or fixes there as part of other work.

## Comments

Minimum only. Write a comment only for a *why* the code can't show: a tool quirk, a race, a security constraint.

- No comments that restate the code or the name
- No history or dates: git and `docs/0_00_design.md` hold those
- Never copy CLI help or docs into docstrings
- A module docstring is at most 3 lines, and a function docstring is one line unless the contract (args, raises) isn't obvious from the signature. This applies to new and changed code; older docstrings are trimmed when their code is next changed
