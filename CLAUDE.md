## Dashboard server

Start (or restart) the web dashboard:

```bash
# kill stale pid if present, then start
[ -f dashboard.pid ] && kill $(cat dashboard.pid) 2>/dev/null; rm -f dashboard.pid
./run_app.sh
```

Stop:

```bash
kill $(cat dashboard.pid)
```

Logs: `dashboard.log`. Runs at http://127.0.0.1:8765.

## CLI

Use `agentop` to list, start, stop, and browse history — run `agentop --help` for full usage.
