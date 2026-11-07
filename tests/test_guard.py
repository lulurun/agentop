import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentop.guard import HostOriginGuard, allowed_hosts_from_env, is_request_allowed

NONE = frozenset()
PROXY = frozenset({"agents.example.com"})


@pytest.mark.parametrize(
    "host, origin, allowed, expected",
    [
        ("127.0.0.1:9775", None, NONE, True),
        ("localhost:9775", None, NONE, True),
        ("[::1]:9775", None, NONE, True),
        ("127.0.0.1:9775", "http://127.0.0.1:9775", NONE, True),
        # an SSH tunnel arrives on another local port; the page is still same-origin
        ("127.0.0.1:18000", "http://127.0.0.1:18000", NONE, True),
        ("localhost:9775", "http://LOCALHOST:9775", NONE, True),
        # hostile page in the user's browser
        ("127.0.0.1:9775", "https://evil.example", NONE, False),
        ("127.0.0.1:9775", "null", NONE, False),
        # another local web app on a different port
        ("127.0.0.1:9775", "http://127.0.0.1:3000", NONE, False),
        # DNS rebinding: looks same-origin, but the name is not ours
        ("evil.example:9775", "http://evil.example:9775", NONE, False),
        ("evil.example:9775", None, NONE, False),
        (None, None, NONE, False),
        # a named reverse proxy, forwarding Host or not
        ("agents.example.com", "https://agents.example.com", PROXY, True),
        ("127.0.0.1:7775", "https://agents.example.com", PROXY, True),
        ("agents.example.com", "https://evil.example", PROXY, False),
    ],
)
def test_is_request_allowed(host, origin, allowed, expected):
    assert is_request_allowed(host, origin, allowed) is expected


def test_allowed_hosts_accepts_names_and_origins(monkeypatch):
    monkeypatch.setenv("AGENTOP_ALLOWED_ORIGINS", " a.example , https://B.example:8443 ,,")
    assert allowed_hosts_from_env() == {"a.example", "b.example"}


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/thing")
    async def thing():
        return {"ok": True}

    @app.get("/open/thing")
    async def open_thing():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("hello")
        await socket.close()

    app.add_middleware(HostOriginGuard, allowed=NONE, exempt_prefixes=("/open",))
    return TestClient(app, base_url="http://127.0.0.1:9775")


def test_http_rejected_with_403(client):
    assert client.get("/thing").status_code == 200
    assert client.get("/thing", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/thing", headers={"Host": "evil.example"}).status_code == 403


def test_exempt_prefix_skips_the_check(client):
    assert client.get("/open/thing", headers={"Origin": "https://evil.example"}).status_code == 200


def test_websocket_handshake_rejected(client):
    with client.websocket_connect("/ws", headers={"Host": "127.0.0.1:9775"}) as ws:
        assert ws.receive_text() == "hello"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws", headers={"Host": "127.0.0.1:9775", "Origin": "https://evil.example"}
        ):
            pass
