## Dashboard server

The dashboard runs as a systemd user service and starts automatically at boot.

```bash
# Status / restart / stop
systemctl --user status agentop
systemctl --user restart agentop
systemctl --user stop agentop

# Logs (live)
journalctl --user -u agentop -f
```

Runs at http://127.0.0.1:9775. Logs also written to `dashboard.log`.

### First-time service setup

```bash
# Create service file
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/agentop.service << 'EOF'
[Unit]
Description=Agentop Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/lulurun/workspace/agentop
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

# Allow service to start at boot without login
loginctl enable-linger lulurun
```

## CLI

Use `agentop` to list, start, stop, and browse history — run `agentop --help` for full usage.
