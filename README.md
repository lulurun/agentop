# agentop

Run AI coding agents (Claude Code, Codex, Antigravity) in tmux, and work with all of them from one window.

## The idea

Agent CLIs are built to run in a terminal, and tmux is the natural place to keep them running. Agentop does not replace the terminal with a chat view, and it does not call any LLM API. It starts each agent in its own tmux session, finds the ones you started yourself, and brings them all into one app. Each agent is used the way it was built to be used, and runs on your own login and subscription.

```
  web app ─┐                      ┌─ tmux: claude   (fix-auth-a1b2c3)
           ├── agentop service ───┼─ tmux: codex    (review-7f3a91)
  desktop ─┘    (one per machine) └─ tmux: agy      (eiger-c4d2e0)
```

## Core concepts

- **Session.** One agent CLI running in one tmux session. Its id is the tmux session name. A session agentop started is **managed**: it can be stopped, resumed, and sent text. One agentop finds running on its own is **external**: it is listed, and can be attached if it is in tmux.
- **Sessions outlive everything else.** Closing the app, or restarting or updating the service, leaves every agent running. tmux owns the agents; agentop only attaches to them.
- **Service.** `agentop serve` runs on each machine that runs agents. It keeps the session list, bridges terminals over WebSocket, and serves the `/api/v2` API. The service token signs a browser or app in; each one then gets its own token, which runs out after 12 hours unused.
- **App.** One UI, built as a web app (served by the service at `/`) and as a Tauri desktop app. Sessions are on the left, the agent's live terminal is in the middle, and details (token usage, changed files) are on the right. The terminal is a real `tmux attach`, so everything the agent's TUI does works as it does in your own terminal.
- **Connections.** The app can add any agentop service it can reach, on this machine or another, and switch between them. It can also see the status of the service it is connected to, and restart or update it.
- **History.** Past conversations of each tool, which can be resumed as new sessions.
- **CLI.** The `agentop` command does the same from a shell: start, stop, list, history, and service control.

## Plugins

Everything above is the **core**. It is always loaded and cannot be switched off. New features are built as **plugins**:

- Each plugin is one folder under `src/agentop/plugins/`, with its UI under `ui/src/plugins/`, and a `plugin.toml` manifest.
- A core loader finds them at service start and loads each one on its own. A plugin gets a narrow API: its own routes under `/api/v2/plugins/<id>/`, core events, supervised background tasks, a hook for being turned off, its own data folder, and `agentop` commands of its own.
- Plugin UI goes into fixed slots in the app, such as a badge on a session row, a view in the left column, or a section in Settings. Settings → Plugins switches each plugin on or off for that service.
- **A plugin can fail, be removed, or be disabled, and the only visible effect is that its feature disappears.** Sessions, terminals, and service control keep working.

The plugins that ship with agentop are Dialogues, the File viewer, and Memory. What each one does is in [docs/plugins.md](docs/plugins.md), and how they work in [design section 12](docs/v2-design.md#12-plugins).

## Requirements

- Linux, Python 3.11+, and tmux
- Node 20.19+ or 22.12+, to build the app
- At least one agent CLI: `claude`, `codex`, or `agy`

## Install

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
pip install -e .
(cd ui && npm ci && npm run build)   # the build output is not committed
agentop serve                        # http://127.0.0.1:9775
agentop open                         # opens the app, signed in
```

That's all agentop needs. Running it as a system service or behind a proxy is optional, and up to you. Connecting to other machines, updating, and HTTPS are in [docs/v2-setup.md](docs/v2-setup.md).

## Using agentop on another machine

The service only speaks plain HTTP on loopback. It refuses to start on any other address without TLS, and the app refuses to add a plain `http://` address that isn't loopback, since anyone on the network could read the token and every terminal. The simplest way to reach a machine you can SSH to is a tunnel.

On the machine that runs the agents (say `build-box`):

```bash
agentop serve                        # listens on 127.0.0.1:9775 only
agentop service connect-info         # prints http://127.0.0.1:9775#token=…
```

On your own machine, forward a local port to it over SSH and keep the command running:

```bash
ssh -N -L 9776:127.0.0.1:9775 build-box
```

Then, in the app on your machine, open Connect to a service from the service menu and paste the connect-info string with the port changed to the tunnel's: `http://127.0.0.1:9776#token=…`. Use a port other than 9775 if your own machine runs a service too. Add `-f` to run the tunnel in the background, or put a `LocalForward 9776 127.0.0.1:9775` line under `Host build-box` in `~/.ssh/config`.

To open the remote app directly instead, use `http://127.0.0.1:9776/#token=…` in a browser. An HTTPS proxy or TLS in the service are the other options, in [docs/v2-setup.md](docs/v2-setup.md#exposing-a-service).

## Secure setup

The defaults suit a service that only you reach, over loopback or a tunnel. The service token (`agentop service token`) never expires, but it only signs a client in, and the app doesn't keep it. Each browser or app gets its own token, which the app renews while it's open. After 12 hours unused, or 30 days in all, it's signed out and asks for the service token again. You can see every client, and sign any one out, in the service's Status dialog or with `agentop service clients`.

**What isn't done yet.** The service token is never rotated by itself: it stays the same until you run `agentop service token --rotate`. Signing in a new browser or phone still means copying that token off the machine. Single-use pairing codes would remove that, but they make every sign-in need a shell on the machine, which is a poor trade for personal use. So they are a lower-priority item in the [security review in TODO.md](TODO.md#security-review), with the rest of the open items there.

For a service reachable from the internet, such as one behind an HTTPS proxy:

- **Shorten the lifetimes** in `~/.agentop/config.toml`, then restart the service:

  ```toml
  client_ttl = "1h"       # signed out after an hour unused
  client_max_age = "7d"   # and after a week, used or not
  ```

- **Rotate the service token** now and then, and at once if it was ever pasted somewhere it shouldn't be: `agentop service token --rotate`. Signed-in clients carry on. Add `--revoke-clients` to sign them all out too, if a client token may have leaked.
- **Check who's signed in** with `agentop service clients`, and sign out anything you don't recognise with `--revoke ID`.
- **Put something else in front** at the proxy too, such as an IP allow-list or a second factor. Tokens are the only thing in front of the agents.

## Documentation

- [Setup](docs/v2-setup.md): running the service and the app
- [CLI reference](docs/cli.md): every command and option
- [Architecture](docs/architecture.md): code structure and modules
- [Design](docs/v2-design.md): the full v2 design, including the plugin system (section 12)
- [Plugins](docs/plugins.md): what each plugin does
- [TODO](TODO.md): work agreed but not started

## License

MIT
