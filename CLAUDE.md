## Quick reference

| Task | Where to look |
|------|---------------|
| CLI commands | [docs/cli.md](docs/cli.md) |
| Dialogue system | [docs/dialogue.md](docs/dialogue.md) |
| Code structure | [docs/architecture.md](docs/architecture.md) |
| Dashboard setup | [docs/dashboard.md](docs/dashboard.md) |

## Key facts

- Dashboard runs at `http://127.0.0.1:9775` as a systemd user service
- Supported agent tools: `claude`, `codex`, `antigravity`
- Dialogues are stored in `~/.agent-dashboard/dialogues/<id>/`
- Only sessions started by agentop (managed) can be stopped or sent prompts

## Dashboard service

```bash
systemctl --user status agentop
systemctl --user restart agentop
systemctl --user stop agentop
journalctl --user -u agentop -f   # live logs
```

## First-time service setup

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/agentop.service << 'EOF'
[Unit]
Description=Agentop Dashboard
After=network.target

[Service]
Type=forking
WorkingDirectory=/home/lulurun/workspace/agentop
ExecStart=/bin/bash /home/lulurun/workspace/agentop/run_app.sh
PIDFile=/home/lulurun/workspace/agentop/dashboard.pid
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/lulurun/workspace/agentop/dashboard.log
StandardError=append:/home/lulurun/workspace/agentop/dashboard.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable agentop
systemctl --user start agentop
loginctl enable-linger lulurun
```
