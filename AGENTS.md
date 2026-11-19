## Quick reference

| Task | Where to look |
|------|---------------|
| CLI commands | [docs/cli.md](docs/cli.md) |
| Dialogues (a plugin) | [docs/dialogue.md](docs/dialogue.md) |
| Projects: groups of sessions with a shared folder and a Coordinator; Sessions, the Projects plugin and Dialogues build on them (not built) | [docs/project.md](docs/project.md) |
| Plugins | [docs/plugins.md](docs/plugins.md) |
| Code structure | [docs/architecture.md](docs/architecture.md) |
| UI layout: names of its parts, where tabs open, keys | [docs/ui.md](docs/ui.md) |
| Service and app setup | [docs/setup.md](docs/setup.md) |
| Build and release (UI, desktop app) | [docs/setup.md](docs/setup.md#building-and-releasing), `scripts/` |
| Manual tests (real user and PC) | [docs/manual-tests.md](docs/manual-tests.md) |
| Backlog: everything not done yet, by priority | [docs/backlog.md](docs/backlog.md) |

## Key facts

- `agentop serve` runs the service at `http://127.0.0.1:9775`: the app at `/`, the API at `/api/v2`. agentop ships no systemd unit or proxy config: on the devhost, a separate ops repo owns the `agentop-api` unit and the nginx setup
- Supported agent tools: `claude`, `codex`, `antigravity`
- Dialogues are stored in `~/.agentop/projects/<scenario>_<brief>_<id>/`
- Only sessions started by agentop (managed) can be stopped or sent prompts

## Running the service

```bash
agentop serve                              # foreground, http://127.0.0.1:9775
agentop service status                     # or restart, stop, update
```

On the devhost it runs as the `agentop-api` user unit, which the ops repo owns:

```bash
systemctl --user status agentop-api
systemctl --user restart agentop-api
journalctl --user -u agentop-api -f
```

## README

The top of `README.md`, from the title through "What is agentop" (the intro video link included), is written and managed by hand. Leave it alone unless asked to change it specifically: no edits, rewording, or fixes there as part of other work.

## Comments

Minimum only. Write a comment only for a *why* the code can't show: a tool quirk, a race, a security constraint.

- No comments that restate the code or the name
- No history or dates: git and `docs/design.md` hold those
- Never copy CLI help or docs into docstrings
- A module docstring is at most 3 lines, and a function docstring is one line unless the contract (args, raises) isn't obvious from the signature. This applies to new and changed code; older docstrings are trimmed when their code is next changed
