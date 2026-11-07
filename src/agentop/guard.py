"""Host and Origin checks for the routes that have no token.

Browsers do not apply CORS to WebSockets, so without this any web page could
open the terminal socket on a loopback service. Checking Host as well defeats
DNS rebinding, where a hostile name resolves to 127.0.0.1 and the page is then
"same origin" with the service.

A proxy or tunnel can make a remote request look local, because it connects
from loopback and may send a loopback Host. So a request carrying forwarding
headers is treated as remote: unless its name is listed in
AGENTOP_ALLOWED_ORIGINS (comma-separated host names or origins), it is refused.
These routes have no token, so being reachable would mean anyone could open a
terminal. /api/v2 is token-protected and exempt, and is what a proxy should use.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)

ALLOWED_ORIGINS_ENV = "AGENTOP_ALLOWED_ORIGINS"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
FORWARDING_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def _hostname(authority: str) -> str:
    """Return the lowercase host name of a `host[:port]` value."""
    return (urlsplit(f"//{authority}").hostname or "").lower()


def allowed_hosts_from_env() -> frozenset[str]:
    """Parse the allowlist, accepting either `example.com` or `https://example.com`."""
    hosts = set()
    for entry in os.environ.get(ALLOWED_ORIGINS_ENV, "").split(","):
        entry = entry.strip()
        if entry:
            hosts.add(urlsplit(entry if "://" in entry else f"//{entry}").hostname or "")
    hosts.discard("")
    return frozenset(h.lower() for h in hosts)


def is_request_allowed(
    host: str | None, origin: str | None, allowed: frozenset[str], proxied: bool = False
) -> bool:
    """Decide whether a request may proceed, from its Host and Origin headers.

    A proxied request is allowed only by name: loopback proves nothing about it.

    Host must be loopback (on any port, so SSH tunnels work) or listed.
    Origin, when present, must be the page's own address or a listed name. A
    different loopback port is not the same origin: other local web apps must
    not be able to drive agents.
    """
    if not host:
        return False
    hostname = _hostname(host)
    if proxied:
        names = {hostname, (urlsplit(origin).hostname or "").lower() if origin else ""}
        if not names & allowed:
            return False
    elif hostname not in LOOPBACK_HOSTS and hostname not in allowed:
        return False
    if origin is None:
        return True
    parts = urlsplit(origin)
    if not parts.hostname:  # includes the opaque origin "null"
        return False
    return parts.netloc.lower() == host.lower() or parts.hostname.lower() in allowed


class HostOriginGuard:
    """ASGI middleware applying is_request_allowed to HTTP and WebSocket requests."""

    def __init__(self, app, allowed: frozenset[str] | None = None, exempt_prefixes: tuple[str, ...] = ()):
        self.app = app
        self.allowed = allowed_hosts_from_env() if allowed is None else allowed
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope["path"].startswith(self.exempt_prefixes or ("\0",)):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host, origin = headers.get("host"), headers.get("origin")
        proxied = any(name in headers for name in FORWARDING_HEADERS)
        if is_request_allowed(host, origin, self.allowed, proxied):
            await self.app(scope, receive, send)
            return

        # Someone following an old bookmark through the proxy lands in the app.
        if proxied and scope["type"] == "http" and scope["path"] == "/":
            headers = [(b"location", b"app/"), (b"cache-control", b"no-store")]
            await send({"type": "http.response.start", "status": 307, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        LOG.warning("Rejected %s %s: Host=%r Origin=%r", scope["type"], scope["path"], host, origin)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"Forbidden: host or origin not allowed\n"})
