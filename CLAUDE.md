## Quick reference

| Task | Where to look |
|------|---------------|
| CLI commands | [docs/cli.md](docs/cli.md) |
| Dialogues (a plugin) | [docs/dialogue.md](docs/dialogue.md) |
| Plugins | [docs/plugins.md](docs/plugins.md) |
| Code structure | [docs/architecture.md](docs/architecture.md) |
| Service and app setup | [docs/v2-setup.md](docs/v2-setup.md) |
| v2 manual tests (real user and PC) | [docs/v2-manual-tests.md](docs/v2-manual-tests.md) |
| Agreed work not started | [TODO.md](TODO.md) |

## Key facts

- `agentop serve` runs the service at `http://127.0.0.1:9775`: the app at `/`, the API at `/api/v2`. agentop ships no systemd unit or proxy config: on the devhost, Eiger owns the `agentop-api` unit (`potential-panda/Eiger/deploy/agentop-api.service`) and the nginx setup
- Supported agent tools: `claude`, `codex`, `antigravity`
- Dialogues are stored in `~/.agentop/plugins/dialogue/dialogues/<scenario>_<brief>_<id>/`
- Only sessions started by agentop (managed) can be stopped or sent prompts

## Running the service

```bash
agentop serve                              # foreground, http://127.0.0.1:9775
agentop service status                     # or restart, stop, update
```

On the devhost it runs as the `agentop-api` user unit, which Eiger owns:

```bash
systemctl --user status agentop-api
systemctl --user restart agentop-api
tail -f /panda-infra/agentop/api.log
```
