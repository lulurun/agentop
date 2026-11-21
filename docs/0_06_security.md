# Security

The terminal socket is remote code execution as the user, with agents running in skip-permissions mode. Connecting to any reachable service raises the stakes: a service can be exposed beyond loopback, and it must accept requests from origins it has never seen. Where security sits in agentop is in [0_00_design.md section 6](0_00_design.md#6-security).

## 1 Rules

1. **Bind to loopback by default.** Exposing a service is an explicit choice: `agentop serve --host`, or `host` in `~/.agentop/config.toml`.
2. **Require a client's bearer token on every request**, with no exception for loopback or for any client, except `GET /api/v2/health`, which returns only `{"status": "ok"}`, `POST /api/v2/auth/clients` (below), `GET /api/v2/auth/methods`, and a login plugin's public routes ([4](#4-ways-to-sign-in)). The service's name and version are behind the token, in `/service`, so a stranger learns only that something answers.
   - The **service token**, random and 256-bit, lives in `~/.agentop/service.json`, mode `0600`. It does one thing: `POST /api/v2/auth/clients` signs in a new client with it. Every other route refuses it. After 10 wrong service tokens in a minute, from anyone, adding a client answers 429 for the rest of that minute. Clients already signed in are not affected.
   - Each **client** (a browser, the desktop app, one CLI command) gets its own random token, which expires after `client_ttl` (default `12h`) unless renewed, and never outlives `client_max_age` (default `30d`) from sign-in. Only a SHA-256 hash of it is kept, in `~/.agentop/state/clients.json`, mode `0600`.
   - `POST /api/v2/auth/renew` returns a new token good for another `client_ttl`. The old one keeps working until the new one is first used, then 60 seconds more, for requests already on their way; a phone that loses the reply carrying the new token goes on with the old one, and renews with it again.
   - `GET /api/v2/auth/clients` lists the clients, and `DELETE /api/v2/auth/clients/{id}` revokes one, this one included (to sign out). A revoked or expired client's open WebSockets close within 5 seconds, with code 4403.
   - `agentop service token --rotate` replaces the service token. Clients already signed in stay signed in, unless `--revoke-clients` is given.
3. **No ambient credentials.** There are no cookies. A token is only ever sent when the app attaches it deliberately, so a malicious page has nothing to ride on. This removes CSRF as a class of problem.
4. **WebSockets use single-use tickets.** Browsers cannot set headers on WebSocket requests, and a token in a URL ends up in logs. `POST /api/v2/tickets` returns a ticket valid for 30s, bound to one purpose (events, or terminal for one session).
5. **File APIs are confined.** Paths are resolved, including symlinks, and must stay inside the session's working directory (or the other folders a route names, such as the tool's memory folder).

Because any origin must be able to connect, the origin doesn't decide access; the token does. So:

- **CORS is open.** `/api/v2` answers any origin, without credentials mode, and the CORS layer sits outside auth so preflights succeed. A foreign page can read `/health`, which says only that the service is up.
- **There is no `Origin` or `Host` allowlist.** An allowlist would require configuring every service for every place the UI might be loaded from, which defeats "connect if reachable". Cookies are what those checks protect, and there are none.
- **The web app sets a strict CSP**: `script-src 'self'` with no inline script. It stores client tokens in `localStorage`, so this is the main defence for them. A stolen one is good for at most `client_ttl` unless the thief keeps renewing it, and can be revoked on its own; the service token is never stored. `connect-src` has to stay open to user-chosen hosts.

## 2 How each client signs in

| Client | Service token from | Client token kept in |
|--------|--------------------|----------------------|
| Web | `agentop open` passes it in the URL fragment, which is cleared at once. A visitor with no token sees a screen that explains this and accepts a pasted one | `localStorage` |
| Web, other services | Pasted through Connect to a service | `localStorage` |
| Desktop, local | A Rust command reads `service.json`, so it can always sign in again | OS keychain |
| Desktop, other services | Pasted through Connect to a service | OS keychain, else `state/desktop-tokens.json` (0600) |
| CLI (`service status`, `stop`, `restart`, `update`) | `service.json` | Nowhere: it signs in for 60 seconds for each command |

The service token is held in memory only until it has signed the app in.

**Renewal.** The app renews, never the service. A token is renewed once it is a twelfth of `client_ttl` old (an hour, by default), and never more often than every five minutes. The app checks every five minutes while it is on screen, and whenever it comes back on screen (`visibilitychange`, `focus`). It renews every saved service's token that is due, not only the one shown. Tabs share one `localStorage`, so one tab renews under a Web Lock (`agentop-renew:<connection>`) and the others take the new token from the `storage` event. A hidden or closed app renews nothing, so after `client_ttl` unused it is signed out, and shows "Signed out of …" with the token form. A 401 from any request does the same, after first taking any newer token another tab saved. Every request, the events socket's reconnects included, uses the token as it is at that moment, never one held from when the connection opened.

## 3 Remote access

### 3.1 What the service allows

| Route | Encrypted | Notes |
|-------|-----------|-------|
| Tunnel, such as `ssh -L` or a VPN, ending at loopback | Yes | Recommended; the service stays loopback-bound |
| Reverse proxy with TLS, including `tailscale serve` | Yes | Base paths are supported in the connection URL |
| Direct bind, with `--tls-cert` and `--tls-key` | Yes | Certificate must be trusted by the client |
| Direct bind, plain HTTP | **No** | Refused: the service won't start. The token and all terminal traffic would be readable on the network |

The Connect to a service form refuses a non-loopback `http://` address, and an address typed without a scheme defaults to `https://` unless it is loopback. Connections saved before this rule still work, with a "Not encrypted" label in the connection manager.

The refusal holds on a VPN too. Binding the service to a Tailscale or WireGuard address with plain HTTP is refused, though the VPN encrypts it: the service can't tell a VPN interface from a café's Wi-Fi, and one rule with no exceptions is easier to trust. A VPN route ends at loopback instead, as `tailscale serve` does.

- **A loopback bind does not prove a route is private.** A tunnel or a proxy in front of a loopback service makes it reachable from outside. So every route carries its own protection: every route but `/api/v2/health`, `/api/v2/auth/methods`, a login plugin's public routes and the app's static files needs the token, and there is nothing a proxy can expose by mistake. A proxy with no authentication of its own leaves the tokens as the only thing in front of the agents.
- **Claude remote control is a way in outside agentop.** Every Claude Code session agentop starts or resumes in the `default` project runs with `--remote-control`, and every Claude session with `--dangerously-skip-permissions` (`agentclis/claude.py`, `ops._launch_params`), so it shows up in the Claude mobile app and on claude.ai, and its bridge URL is in the details panel. Whoever can sign in to the user's Claude account can drive it, with no approval step and no agentop token. This is deliberate: it is what lets a phone pick up a Claude session without agentop. Sessions in other projects run without it: their Coordinator drives them, not a phone. Codex and Antigravity sessions have no such flag. Making it a per-session option is in the [backlog](9_backlog.md#sign-in-and-security). No choice of route below changes this.

### 3.2 Choosing a route

The question for each route is who can reach the sign-in screen. Whoever reaches it is one stolen or guessed token away from every agent on the machine, so the fewer people who can reach it, the better.

| Route | Who reaches the sign-in screen | Needs on each device | Who else sees the traffic | Suits |
|-------|-------------------------------|----------------------|---------------------------|-------|
| **Tailscale, with `tailscale serve`** | Only devices signed in to your tailnet | The Tailscale app | Nobody: WireGuard end to end, and TLS inside it. Relays carry only ciphertext | **One person, own devices, phone included. Recommended** |
| SSH tunnel (`ssh -L`) | Whoever has an SSH login to the host | An SSH client, and a tunnel per use | Nobody | A laptop, now and then. Awkward on a phone |
| Your own VPN (WireGuard, Headscale, ZeroTier) | Devices on that VPN | Its client, and keys you manage | Nobody | Tailscale's model without its coordination server, at the cost of running it yourself |
| HTTPS reverse proxy on a public host | Anyone on the internet | A browser | The proxy host | Devices that can't run a VPN client, or several people. Add a second gate at the proxy (IP allow-list, client certificates, or single sign-on) |
| Cloudflare Tunnel with Cloudflare Access | Whoever passes Access (email code, SSO) | A browser | Cloudflare, which ends TLS and sees every keystroke and screen | A browser anywhere, without a proxy host of your own, if Cloudflare seeing the terminals is acceptable |
| Direct TLS bind (`--tls-cert`) | Whoever can reach the port | A browser that trusts the certificate | Nobody | A LAN with a certificate you already have |
| Tailscale Funnel | Anyone on the internet | A browser | Nobody but Tailscale's edge, which can't read it | Not for agentop: it is a public proxy, with none of the tailnet's gate |

**For personal use, use Tailscale with `tailscale serve`.** It is the only route here that is private by default, works on a phone, and needs no host or certificate of your own:

- **Strangers can't reach anything.** The service stays bound to `127.0.0.1`, no port is open to the internet, and only your devices can reach the address at all. A leaked service token is then no use to someone without one of them. Behind a public proxy, the token is all that stands in front of the agents.
- **It is real HTTPS.** `tailscale serve` gets a Let's Encrypt certificate for `<machine>.<tailnet>.ts.net` and renews it. The app's HTTPS rule holds, and a phone can install the app from that address.
- **Nothing to run.** No proxy host, domain, nginx or certbot. A device is added by signing in to it and removed in Tailscale's admin console.

What it costs, and what it doesn't change:

- **Your Tailscale account becomes a way in**, like your Claude account already is: whoever signs in to it can add a device to the tailnet. Use an identity provider with two-factor sign-in.
- **Tailscale's coordination server decides which devices are in the tailnet.** It never holds the WireGuard private keys and can't read the traffic, but it could in principle admit a device. Tailnet Lock removes that, by making new devices need a signature from one you trust. Headscale replaces the server with your own.
- **Every device needs the client.** A borrowed computer can't reach the service. That is the point, and the reason a public proxy is the fallback for those cases.
- **Agentop's own sign-in still applies.** The tailnet only decides who can reach the service. Every device still signs in with the service token or Email code, and gets its own client token.
- **Everyone in the tailnet can reach it.** A tailnet of your own devices only is the case this recommendation is for. If others share it, or you share the machine to them, restrict port 443 on that machine to your own devices in the tailnet's access controls.
- **A machine's key expires after 180 days** by default, after which it drops off the tailnet until it signs in again. Turn key expiry off for the host that runs agentop.

How to set up each route is in [1_setup.md](1_setup.md#exposing-a-service).

## 4 Ways to sign in

The service token is one way to sign in, and always there: it is the recovery path, and what `agentop open`, the CLI and the desktop app use. Other ways are plugins, which can be turned on and off like any plugin.

- **Every way ends the same.** A login plugin only decides who may sign in. It then calls `api.sign_in(name)`, which adds an ordinary client, exactly as `POST /auth/clients` does with the service token. Renewal, expiry, revocation and the client list are the core's, and a plugin can't issue any other kind of credential.
- **`GET /api/v2/auth/methods`**, with no token, lists the ways to sign in: `[{id: "token", name: "Service token"}, …]`, then each enabled plugin whose manifest names the `login` UI slot. It says nothing else about the service. An older service answers 404, which the app reads as the service token only.
- **Public routes.** A login plugin adds routes with no client token on `api.public_router`, mounted at `/api/v2/plugins/{id}/…` and 404 unless it is enabled. Only a plugin with the `login` slot may add them. Public routes face anyone who can reach the service, so the plugin limits its own attempts.
- **The sign-in screen** shows, above the token form, the `login` component of each method the app has a module for.

The first login plugin is Email code, which mails a one-time code to one address, for signing in a phone without copying the service token onto it ([0_12_00_plugins.md](0_12_00_plugins.md#email-code-email-code)).
