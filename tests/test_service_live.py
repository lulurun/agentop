"""End-to-end tests: a real uvicorn server, real WebSockets, a private tmux server."""

import json
import os
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import psutil
import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from agentop import registry
from agentop.service import identity
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.store import SessionStore
from agentop.tmux import Session


class LiveService:
    def __init__(self, port):
        self.port = port
        self.base = f"http://127.0.0.1:{port}/api/v2"
        self.ident = identity.load_or_create()
        self.service_auth = {"Authorization": f"Bearer {self.ident.token}"}
        self.headers = self.sign_in()
        self.http = httpx.Client(base_url=self.base, headers=self.headers, timeout=10)

    def sign_in(self, name="test", **body):
        response = httpx.post(f"{self.base}/auth/clients", json={"name": name, **body}, headers=self.service_auth)
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def ticket(self, purpose="events", session_id=None):
        body = {"purpose": purpose, "session_id": session_id}
        return self.http.post("/tickets", json=body).json()["ticket"]

    def events(self):
        return connect(f"ws://127.0.0.1:{self.port}/api/v2/ws/events?ticket={self.ticket()}")

    def terminal(self, session_id, cols=80, rows=24):
        ticket = self.ticket("terminal", session_id)
        url = f"ws://127.0.0.1:{self.port}/api/v2/ws/sessions/{session_id}/terminal"
        return connect(f"{url}?ticket={ticket}&cols={cols}&rows={rows}")

    def create(self, tool="fakeagent", name="demo", cwd="/tmp"):
        response = self.http.post("/sessions", json={"tool": tool, "cwd": cwd, "name": name})
        assert response.status_code == 202, response.text
        return response.json()

    def wait_for(self, session_id, status, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.http.get(f"/sessions/{session_id}")
            if response.status_code == 200 and response.json()["status"] == status:
                return response.json()
            time.sleep(0.05)
        raise AssertionError(f"{session_id} never became {status}: {response.text}")


@contextmanager
def run_service(settings=None):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = settings or Settings(port=port)
    app = create_app(settings, store=SessionStore(tick_seconds=0.1))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        yield LiveService(port)
    finally:
        server.should_exit = True
        thread.join(10)


@pytest.fixture
def service(fake_agents):
    with run_service() as live:
        yield live


def read_until(ws, text, timeout=5.0):
    needle = text.encode()
    tail = b""  # only the end is kept: a flood delivers tens of megabytes
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = ws.recv(timeout=0.2)
        except TimeoutError:
            continue
        tail = (tail + (frame if isinstance(frame, bytes) else frame.encode()))[-4096:]
        if needle in tail:
            return
    raise AssertionError(f"{text!r} not seen in {tail[-500:]!r}")


# ---------------------------------------------------------------------- auth


def test_health_is_open_and_says_only_that_it_is_up(service):
    assert httpx.get(f"{service.base}/health").json() == {"status": "ok"}
    # The same with a token: what the service is, and which version, is in /service.
    assert service.http.get("/health").json() == {"status": "ok"}


def test_service_says_what_it_is_to_a_client(service):
    body = service.http.get("/service").json()
    assert body["api_version"] == 1 and body["name"] == service.ident.name and body["version"]
    assert "instance_id" not in body and "token" not in json.dumps(body)


@pytest.mark.parametrize("path", ["/service", "/sessions", "/tools", "/fs/dirs", "/saved-sessions"])
def test_everything_else_needs_the_token(service, path):
    assert httpx.get(service.base + path).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert httpx.get(service.base + path, headers=bad).status_code == 401
    assert service.http.get(path).status_code == 200


def test_mutations_need_the_token(service):
    body = {"tool": "fakeagent", "cwd": "/tmp"}
    assert httpx.post(f"{service.base}/sessions", json=body).status_code == 401
    assert httpx.post(f"{service.base}/tickets", json={"purpose": "events"}).status_code == 401


def test_any_origin_works_with_the_token_and_only_then(service):
    origin = {"Origin": "https://anywhere.example"}
    assert httpx.get(f"{service.base}/sessions", headers=origin).status_code == 401
    response = service.http.get("/sessions", headers=origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_preflight_succeeds_without_a_token(service):
    response = httpx.options(
        f"{service.base}/sessions",
        headers={
            "Origin": "https://anywhere.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200


def test_foreign_page_can_read_health_but_no_session_data(service):
    service.create()
    response = httpx.get(f"{service.base}/sessions", headers={"Origin": "https://evil.example"})
    assert response.status_code == 401 and "demo" not in response.text


@pytest.mark.parametrize("ticket", ["", "made-up"])
def test_events_reject_a_bad_ticket(service, ticket):
    url = f"ws://127.0.0.1:{service.port}/api/v2/ws/events?ticket={ticket}"
    with connect(url) as ws, pytest.raises(ConnectionClosed) as caught:
        ws.recv(timeout=2)
    assert caught.value.rcvd.code == 4401


def test_terminal_ticket_is_bound_to_one_session(service):
    first = service.create(name="one")
    other = service.create(name="two")
    service.wait_for(first["id"], "running")
    ticket = service.ticket("terminal", first["id"])
    url = f"ws://127.0.0.1:{service.port}/api/v2/ws/sessions/{other['id']}/terminal?ticket={ticket}"
    with connect(url) as ws, pytest.raises(ConnectionClosed) as caught:
        ws.recv(timeout=2)
    assert caught.value.rcvd.code == 4401


def test_a_reused_ticket_is_rejected(service):
    ticket = service.ticket()
    url = f"ws://127.0.0.1:{service.port}/api/v2/ws/events?ticket={ticket}"
    with connect(url) as ws:
        assert json.loads(ws.recv(timeout=2))["type"] == "snapshot"
    with connect(url) as ws, pytest.raises(ConnectionClosed) as caught:
        ws.recv(timeout=2)
    assert caught.value.rcvd.code == 4401


def test_terminal_for_a_missing_session_closes_4404(service):
    with service.terminal("no-such-session") as ws, pytest.raises(ConnectionClosed) as caught:
        ws.recv(timeout=2)
    assert caught.value.rcvd.code == 4404


def test_the_service_token_only_adds_a_client(service):
    assert httpx.get(f"{service.base}/sessions", headers=service.service_auth).status_code == 401
    assert httpx.post(f"{service.base}/tickets", json={"purpose": "events"}, headers=service.service_auth).status_code == 401
    response = httpx.post(f"{service.base}/auth/clients", json={"name": "phone"}, headers=service.service_auth)
    body = response.json()
    assert response.status_code == 201 and body["ttl"] == 12 * 3600 and 0 < body["expires_in"] <= body["ttl"]
    assert body["token"] != service.ident.token
    # A client token can't add another client.
    assert httpx.post(f"{service.base}/auth/clients", json={}, headers=service.headers).status_code == 401


def test_renewing_gives_a_new_token(service):
    renewed = service.http.post("/auth/renew").json()
    assert f"Bearer {renewed['token']}" != service.headers["Authorization"]
    fresh = {"Authorization": f"Bearer {renewed['token']}"}
    assert httpx.get(f"{service.base}/sessions", headers=fresh).status_code == 200
    # The old one keeps working for a moment, for requests already on their way.
    assert service.http.get("/sessions").status_code == 200


def test_clients_are_listed_and_revoked_one_at_a_time(service):
    other = service.sign_in("laptop")
    listed = service.http.get("/auth/clients").json()
    assert {c["name"] for c in listed} == {"test", "laptop"}
    assert [c["current"] for c in listed if c["name"] == "test"] == [True]
    laptop = next(c["id"] for c in listed if c["name"] == "laptop")
    assert service.http.delete(f"/auth/clients/{laptop}").status_code == 204
    assert httpx.get(f"{service.base}/sessions", headers=other).status_code == 401
    assert service.http.get("/sessions").status_code == 200
    assert service.http.delete(f"/auth/clients/{laptop}").status_code == 404


def test_a_client_can_ask_for_a_shorter_life_but_not_a_longer_one(service):
    short = httpx.post(f"{service.base}/auth/clients", json={"ttl_seconds": 60}, headers=service.service_auth).json()
    assert short["expires_in"] <= 60
    long = httpx.post(f"{service.base}/auth/clients", json={"ttl_seconds": 10**9}, headers=service.service_auth).json()
    assert long["expires_in"] <= 12 * 3600


def test_wrong_service_tokens_are_slowed_down(service):
    add = f"{service.base}/auth/clients"
    codes = [httpx.post(add, json={}, headers={"Authorization": "Bearer wrong"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[-1] == 429
    # Clients already signed in carry on.
    assert service.http.get("/sessions").status_code == 200


def test_revoking_a_client_closes_its_sockets(service, monkeypatch):
    from agentop.service import routes

    monkeypatch.setattr(routes, "CLIENT_CHECK_SECONDS", 0.1)
    other = service.sign_in("tablet")
    ticket = httpx.post(f"{service.base}/tickets", json={"purpose": "events"}, headers=other).json()["ticket"]
    tablet = next(c["id"] for c in service.http.get("/auth/clients").json() if c["name"] == "tablet")
    with connect(f"ws://127.0.0.1:{service.port}/api/v2/ws/events?ticket={ticket}") as ws:
        ws.recv(timeout=5)  # the snapshot
        service.http.delete(f"/auth/clients/{tablet}")
        with pytest.raises(ConnectionClosed) as caught:
            while True:
                ws.recv(timeout=5)
    assert caught.value.rcvd.code == routes.WS_SIGNED_OUT


# ---------------------------------------------------------------------- the app's address


def test_the_old_dashboard_and_its_routes_are_gone(service):
    root = f"http://127.0.0.1:{service.port}"
    for path in ("/api/health", "/api/sessions", "/api/dialogues"):
        assert httpx.get(root + path).status_code == 404


@pytest.mark.parametrize(
    "path, location", [("/app", "./"), ("/app/", "../"), ("/app/assets/index.js", "../../assets/index.js")]
)
def test_the_apps_old_address_leads_to_the_new_one(service, path, location):
    response = httpx.get(f"http://127.0.0.1:{service.port}{path}")
    assert response.status_code == 308 and response.headers["location"] == location


# ---------------------------------------------------------------------- lifecycle


def test_create_returns_at_once_then_runs(service):
    started = time.monotonic()
    created = service.create()
    assert time.monotonic() - started < 0.5
    assert created["status"] == "starting" and created["id"].startswith("demo-")
    running = service.wait_for(created["id"], "running")
    assert running["pid"] and running["attachable"] and running["id"] == created["id"]


def test_events_follow_a_start_step_by_step(service):
    with service.events() as ws:
        assert json.loads(ws.recv(timeout=2))["type"] == "snapshot"
        created = service.create()
        seen = []
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and "running" not in [s for s, _ in seen]:
            event = json.loads(ws.recv(timeout=5))
            if event["type"] == "session.upserted" and event["session"]["id"] == created["id"]:
                seen.append((event["session"]["status"], event["session"]["step"]))
    assert ("starting", "waiting") in seen or ("starting", "confirming") in seen
    assert seen[-1] == ("running", None)


def test_terminal_attaches_on_the_first_try(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"]) as ws:
        read_until(ws, "fake agent ready")


def test_terminal_input_and_output(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"]) as ws:
        read_until(ws, "fake agent ready")
        ws.send(b"hello-agent\r")
        read_until(ws, "got: hello-agent")


def test_terminal_starts_at_the_requested_size_and_resizes(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"], cols=101, rows=33) as ws:
        read_until(ws, "fake agent ready")

        def size():
            return Session.list_panes(created["id"], "#{window_width}x#{window_height}")[0]

        assert size().startswith("101x")
        ws.send(json.dumps({"type": "resize", "cols": 90, "rows": 30}))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not size().startswith("90x"):
            time.sleep(0.05)
        assert size().startswith("90x")


def test_bad_control_frames_are_ignored(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"]) as ws:
        read_until(ws, "fake agent ready")
        for frame in ["not json", "[]", '{"type":"resize"}', '{"type":"resize","cols":"x","rows":1}']:
            ws.send(frame)
        ws.send(b"still-alive\r")
        read_until(ws, "got: still-alive")


def test_disconnecting_does_not_end_the_session(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"]) as ws:
        read_until(ws, "fake agent ready")
    time.sleep(0.3)
    assert Session.has(created["id"])
    assert service.http.get(f"/sessions/{created['id']}").json()["status"] == "running"


def test_missing_binary_ends_failed_with_the_terminal_available(service):
    created = service.create(tool="brokenagent")
    failed = service.wait_for(created["id"], "failed")
    assert "not installed" in failed["error"] and failed["attachable"]
    with service.terminal(created["id"]) as ws:
        read_until(ws, "not found")


def test_a_failed_session_can_be_dismissed(service):
    created = service.create(tool="brokenagent")
    service.wait_for(created["id"], "failed")
    assert service.http.delete(f"/sessions/{created['id']}").status_code == 200
    assert not Session.has(created["id"])
    assert service.http.get(f"/sessions/{created['id']}").status_code == 404


def test_agent_exit_becomes_exited_and_terminal_still_works(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    Session.send_keys(created["id"], "C-d")
    exited = service.wait_for(created["id"], "exited")
    assert exited["attachable"] and exited["pid"] is None
    with service.terminal(created["id"]) as ws:
        ws.send(b"echo back-in-shell\r")
        read_until(ws, "back-in-shell")


def test_killed_tmux_session_disappears_and_tells_the_terminal(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"]) as ws:
        read_until(ws, "fake agent ready")
        Session.kill(created["id"])
        read_until(ws, "session_ended")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and service.http.get(f"/sessions/{created['id']}").status_code != 404:
        time.sleep(0.05)
    assert service.http.get(f"/sessions/{created['id']}").status_code == 404


def test_session_created_outside_the_service_appears(service):
    from agentop import ops

    name = ops.start("fakeagent", "/tmp", "from-cli")["name"]
    service.wait_for(name, "running")


def test_stop_only_acts_on_its_target(service):
    one = service.create(name="foo")
    two = service.create(name="foo")
    service.wait_for(one["id"], "running")
    service.wait_for(two["id"], "running")
    assert service.http.post(f"/sessions/{one['id']}/stop").status_code == 200
    assert not Session.has(one["id"])
    assert Session.has(two["id"])


def test_patch_sets_name_and_description(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    patched = service.http.patch(f"/sessions/{created['id']}", json={"display_name": "New", "description": "D"})
    assert patched.json()["display_name"] == "New" and patched.json()["description"] == "D"
    assert Session.has(created["id"]), "renaming must not touch tmux"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"tool": "nope", "cwd": "/tmp"}, "Unknown tool"),
        ({"tool": "fakeagent", "cwd": "/no/such/dir"}, "Not a directory"),
        ({"tool": "fakeagent", "cwd": "/tmp", "name": "a.b"}, "Name must"),
        ({"tool": "fakeagent", "cwd": "/tmp", "name": "a:b"}, "Name must"),
        ({"tool": "fakeagent", "cwd": "/tmp", "name": "-x"}, "Name must"),
        ({"tool": "fakeagent", "cwd": "/tmp", "resume": {"session_id": "x; rm -rf ~"}}, "Invalid session_id"),
    ],
)
def test_bad_input_is_refused_at_once(service, body, fragment):
    response = service.http.post("/sessions", json=body)
    assert response.status_code == 400 and fragment in response.text


def test_resume_uses_the_same_flow(service):
    body = {"tool": "fakeagent", "cwd": "/tmp", "name": "again", "resume": {"session_id": "abc-123"}}
    created = service.http.post("/sessions", json=body).json()
    assert created["status"] == "starting"
    service.wait_for(created["id"], "running")


def test_fs_dirs_lists_directories(service, tmp_path):
    (tmp_path / "shown").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file").write_text("x")
    body = service.http.get("/fs/dirs", params={"path": str(tmp_path)}).json()
    assert "shown" in body["dirs"] and ".hidden" not in body["dirs"] and "file" not in body["dirs"]
    assert ".hidden" in service.http.get("/fs/dirs", params={"path": str(tmp_path), "hidden": True}).json()["dirs"]
    assert service.http.get("/fs/dirs", params={"path": str(tmp_path / "file")}).status_code == 400
    assert service.http.get("/fs/dirs", params={"path": "/no/such"}).status_code == 404


# ---------------------------------------------------------------------- restart


def test_sessions_survive_a_service_restart(fake_agents):
    with run_service() as first:
        created = first.create()
        first.wait_for(created["id"], "running")
        instance_id = first.ident.instance_id
    assert Session.has(created["id"])
    with run_service() as second:
        assert second.ident.instance_id == instance_id
        second.wait_for(created["id"], "running")
        with second.terminal(created["id"]) as ws:
            read_until(ws, "fake agent ready")


def test_restart_during_a_start_is_reported(fake_agents):
    registry.upsert_session("lost-1", {"managed": True, "status": "starting", "step": "waiting"})
    with run_service() as live:
        failed = live.wait_for("lost-1", "failed")
        assert "restart" in failed["error"]


# ---------------------------------------------------------------------- bridge health


def _resources():
    me = psutil.Process()
    return len(os.listdir("/proc/self/fd")), len(me.children())


def test_connect_cycles_leak_nothing(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    with service.terminal(created["id"]) as ws:
        read_until(ws, "fake agent ready")
    time.sleep(0.5)
    before_fds, before_children = _resources()

    for _ in range(100):
        with service.terminal(created["id"]) as ws:
            ws.recv(timeout=5)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _resources()[1] > before_children:
        time.sleep(0.1)
    fds, children = _resources()
    assert children <= before_children
    assert fds <= before_fds + 5
    assert not [p for p in psutil.Process().children() if p.status() == psutil.STATUS_ZOMBIE]
    assert Session.list_all()[created["id"]] == 0


def test_flood_with_a_slow_client_stays_responsive_and_recovers(service):
    created = service.create()
    service.wait_for(created["id"], "running")
    Session.send_keys(created["id"], "C-d")
    service.wait_for(created["id"], "exited")
    Session.send_keys(created["id"], "yes flood-flood-flood-flood", "Enter")
    with service.terminal(created["id"]) as ws:
        for _ in range(3):
            ws.recv(timeout=5)
        time.sleep(1.0)  # not reading, so the bridge stalls

        started = time.monotonic()
        assert httpx.get(f"{service.base}/health", timeout=5).status_code == 200
        assert time.monotonic() - started < 1.0

        Session.send_keys(created["id"], "C-c")
        Session.send_keys(created["id"], "clear; echo flood-is-over", "Enter")
        read_until(ws, "flood-is-over", timeout=15)




# ---------------------------------------------------------------------- files


def test_session_files_and_changes(service, tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work/a.txt").write_text("x")
    created = service.create(cwd=str(tmp_path / "work"))
    service.wait_for(created["id"], "running")
    listing = service.http.get(f"/sessions/{created['id']}/files").json()
    assert [e["name"] for e in listing["entries"]] == ["a.txt"]
    assert service.http.get(f"/sessions/{created['id']}/changes").json()["is_repo"] is False


def test_session_files_refuse_escapes_and_need_the_token(service, tmp_path):
    created = service.create(cwd=str(tmp_path))
    service.wait_for(created["id"], "running")
    path = f"/sessions/{created['id']}/files"
    assert service.http.get(path, params={"path": ".."}).status_code == 403
    assert service.http.get(path, params={"path": "nope"}).status_code == 404
    assert httpx.get(service.base + path).status_code == 401
    assert httpx.get(f"{service.base}/sessions/{created['id']}/changes").status_code == 401


def test_a_rotated_service_token_takes_effect_without_a_restart(service):
    old = service.service_auth["Authorization"]
    new = identity.rotate_token().token
    add = f"{service.base}/auth/clients"

    assert httpx.post(add, json={}, headers={"Authorization": old}).status_code == 401
    assert httpx.post(add, json={}, headers={"Authorization": f"Bearer {new}"}).status_code == 201
    # Clients signed in before the rotation stay signed in.
    assert service.http.get("/sessions").status_code == 200
    assert identity.load_or_create().instance_id == service.ident.instance_id


def test_a_service_given_its_identity_ignores_the_file(fake_agents):
    from fastapi.testclient import TestClient

    from agentop.service.identity import Identity

    identity.load_or_create()
    app = create_app(Settings(), ident=Identity("fixed", "i", "box"))
    identity.rotate_token()
    client = TestClient(app)
    response = client.post("/api/v2/auth/clients", json={}, headers={"Authorization": "Bearer fixed"})
    assert response.status_code == 201
    assert client.get("/api/v2/sessions", headers={"Authorization": f"Bearer {response.json()['token']}"}).status_code == 200
