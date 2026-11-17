# agentop

You run your agents in the **CLI**. You are a heavy multi-AI **subscription** user.

You know the afternoon: Claude Code deep in a feature, Codex reviewing a change, Antigravity halfway through a deploy, and you the only index of which terminal holds which task. Then you leave the desk: an idea on the train, a prod page at dinner. The work either waits or runs on unseen, and when you come back you want the same sessions, not reconstructions of them.

You know the moments, too: deep in a dev session, you want to deploy and see a quick result; in the middle of troubleshooting a problem, you want to implement a fix to test your idea. Do it in the same session and you pollute the context that makes it good: the session that knows your feature best is one pasted deploy log away from losing the thread. Start a fresh session instead and it knows nothing: you spend your first prompts rebuilding, from scratch, a context another session already holds.

`agentop` is for that way of working.

You run your agents in the **CLI**. You are a heavy multi-AI **subscription** user.

So two things come first. agentop runs the **agent CLIs themselves** (claude, codex, agy) under tmux, unchanged, not a chat wrapper around them. And it runs on **your own subscriptions**: its core calls no LLM API and needs no API key; it uses your logged-in CLIs as they are.

It gives each task its own agent and each agent its own named session, then shows them all in one window: tasks, live terminals, changed files, on this machine or another, even from a phone. And it lets you pass work between sessions so each keeps its role: when the dev session reaches a milestone, you have it hand the deploy to the deploy session and it keeps developing; when the investigation finds the fix, you have it hand that off to a dev session and it stays on the trail. No polluted context on one side, no context rebuilt from scratch on the other.

## What is agentop

**One agent per task, each in a session that outlives the window and your time at the desk.**

## How it works

My own first version of this was manual: one agent per tmux session, named after its task, found with `tmux ls`, started and stopped by hand. agentop automates that habit and hides it (you need no tmux knowledge) but keeps its one structural idea: **every agent lives in its own tmux session**. Nearly everything else follows from that.

- **Sessions outlive the app and the service.** Close the app, restart or update the service, or shut your laptop while the agents run on a dev host: every agent keeps running, and you come back to the same session. Claude Code sessions agentop starts have remote control on, so the Claude app on your phone can pick one up; for any tool, the agentop web app in the phone's browser does the job.
- **Every tool is driven the same way**: by typing into its terminal. The core calls no LLM API and never replaces the agent's terminal with a chat view; each CLI runs exactly as it was built. That is also all handoff needs, which is why it is part of the core, not a plugin: when you ask a Claude Code or Codex session to hand work over, it uses the `handoff` skill to write a self-contained prompt, and `agentop send` types it into any running session agentop started on the same machine.
- **Dialogues** (an exploratory plugin) build on the same base: two agents, even of different tools, talk through their sessions toward a goal you set, for example one challenging the other's design the way you would.

```
 web app ─┐                     ┌─ tmux: claude  fix-auth-a1b2c3   ◄─ Claude mobile app
          │                     ├─ tmux: claude  dev-search-5d8e17
          ├── agentop service ──┼─ tmux: codex   review-7f3a91     ◄─ ChatGPT mobile app
 desktop ─┘   (one per machine) └─ tmux: agy     deploy-c4d2e0
```

## Core concepts

- **Session.** One agent CLI running in one tmux session; its id is the tmux session name. A session agentop started is **managed**; one agentop finds already running is **external**, listed and attachable if it is in tmux. Managed sessions can be stopped, resumed, and sent text.
- **Ownership.** tmux owns the agents; agentop attaches to them.
- **Service.** `agentop serve` runs on each machine that runs agents. It keeps the session list, bridges terminals over WebSocket, and serves the `/api/v2` API and the app at `/`. The service token signs a browser or app in; each one then gets its own token, which by default runs out after 12 hours unused and after 30 days in all; the app renews it while in use.
- **App.** One UI, served by the service, in a browser or in the Tauri desktop app, which runs this computer's service while it is open. Sessions sit on the left, the agent's live terminal in the middle, details (token usage, changed files) on the right. The terminal is a real `tmux attach`, so everything the agent's TUI does works as it does in your own terminal. The layout adapts to a phone-width screen.
- **Connections.** The app can add any agentop service it can reach, on this machine or another, and switch between them. It shows the connected service's status and can restart or update it.
- **History.** Past conversations of each tool, in a workspace of their own: searched, filtered by tool, sorted, and resumed as new sessions.
- **CLI.** The `agentop` command does the same from a shell: start, stop, list, send, history, and service control.

## Plugins

Everything above is the **core**. It is always loaded and cannot be switched off. New features are built as **plugins**:

- Each plugin is one folder under `src/agentop/plugins/`, with its UI under `ui/src/plugins/`, and a `plugin.toml` manifest.
- A core loader finds them at service start and loads each one on its own. A plugin gets a narrow API: its own routes under `/api/v2/plugins/<id>/`, core events, supervised background tasks, a hook for being turned off, its own data folder, and `agentop` commands of its own.
- Plugin UI goes into fixed slots in the app, such as a badge on a session row, a workspace in the header, or a section in the service's Status tab. Status → Plugins switches each plugin on or off for that service.
- **A plugin can fail, be removed, or be disabled, and the only visible effect is that its feature disappears.** Sessions, terminals, and service control keep working.

The plugins that ship with agentop are Dialogues, Waiting for input, the File viewer, Memory, and Email code. What each one does is in [docs/plugins.md](docs/plugins.md), and how they work in [design section 12](docs/design.md#12-plugins).

## Requirements

**To run**

- Service and desktop app: Linux or macOS, with tmux and Python 3.11+. On macOS the desktop app runs the service.
- Web UI: any browser that can reach the service, a phone's included.
- At least one agent CLI, signed in: `claude`, `codex`, or `agy`.

**To build**

- Service: Python 3.11+
- UI: Node 20.19+ or 22.12+
- Desktop app: Rust, plus the WebKitGTK 4.1 development files on Linux ([docs/setup.md](docs/setup.md))

## Quick Start

Let your agent set it up:

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
claude                               # or codex, or agy
```

Then ask it: "Please set up agentop, run it, and open the UI."

Or by hand:

```bash
git clone git@github.com:lulurun/agentop.git
cd agentop
pip install -e .
(cd ui && npm ci && npm run build)   # the build output is not committed
agentop serve                        # http://127.0.0.1:9775
agentop open                         # opens the app, signed in
```

That's all agentop needs. Running it as a system service or behind a proxy is optional, and up to you. Connecting to other machines, updating, and HTTPS are in [docs/setup.md](docs/setup.md).

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

To open the remote app directly instead, use `http://127.0.0.1:9776/#token=…` in a browser. An HTTPS proxy or TLS in the service are the other options, in [docs/setup.md](docs/setup.md#exposing-a-service).

## Secure setup

The defaults suit a service that only you reach, over loopback or a tunnel. The service token (`agentop service token`) never expires, but it only signs a client in, and the app doesn't keep it. Each browser or app gets its own token, which the app renews while it's open. After 12 hours unused, or 30 days in all, it's signed out and asks for the service token again. You can see every client, and sign any one out, in the service's Status tab or with `agentop service clients`.

**Signing in without the token.** Copying the service token onto a phone is awkward, so the Email code plugin offers another way in: the sign-in screen gets a "Send me a sign-in code" button, and a 6-digit code goes to one address you set on the service. A code works for 10 minutes, once, in the browser that asked for it. Whoever can read that mailbox can sign in, so protect it (two-factor sign-in, at least). To set it up, write `~/.agentop/plugins/email-code/config.toml` and `chmod 600` it:

```toml
to = "you@example.com"
smtp_host = "smtp.gmail.com"
smtp_port = 587
smtp_user = "you@gmail.com"
smtp_password = "an app password"
```

Then turn on **Email code** in the service's Status tab, under Plugins, and check it with `agentop email-code test`.

**What isn't done yet.** The service token is never rotated by itself: it stays the same until you run `agentop service token --rotate`.

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

- [Setup](docs/setup.md): running the service and the app
- [CLI reference](docs/cli.md): every command and option
- [Architecture](docs/architecture.md): code structure and modules
- [Design](docs/design.md): the full design, including the plugin system (section 12)
- [Plugins](docs/plugins.md): what each plugin does
- [Backlog](docs/plan.md#9-backlog): work agreed but not started

## License

MIT
