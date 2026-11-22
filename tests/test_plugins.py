"""The plugin loader: a plugin can fail, be removed, or be disabled, and only its feature disappears.

Each test writes its plugins into a temporary folder and runs the real app,
with a store that never touches tmux, so session events are fed by hand.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentop import plugin_manifest
from agentop.service import plugins as plugin_mod
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import PluginHost
from agentop.service.store import SessionStore
from tests.broken_plugins import BROKEN, FAILS_LATER, install, write_plugin
from tests.conftest import signed_in

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class QuietStore(SessionStore):
    """A store with no scanning: tests set the session list themselves."""

    async def startup(self):
        pass

    async def shutdown(self):
        pass

    async def refresh(self, with_meta=False):
        pass

    def set_rows(self, rows: dict) -> None:
        self._publish_changes(rows, include_volatile=True)
        old, self._sessions = self._sessions, rows
        self._notify_listeners(old, rows)


GOOD = """
import asyncio, os
from pathlib import Path

OUT = Path(os.environ["PLUGIN_TEST_OUT"])

def register(api):
    @api.router.get("/hello")
    def hello():
        return {"hello": api.id}

    @api.router.post("/emit")
    def emit():
        api.emit({"n": 1})
        return {"ok": True}

    @api.router.get("/count")
    def count():
        return {"sessions": len(api.sessions())}

    @api.router.post("/save")
    def save():
        api.store.save({"saved": True})
        return api.store.load()

    def on_event(payload):
        name = payload["id"] if isinstance(payload, dict) else "scan"
        with OUT.open("a") as f:
            f.write(name + "\\n")

    for event in ("session.started", "session.exited", "session.closed"):
        api.on(event, lambda p, e=event: on_event({"id": e + ":" + p["id"]}))

    async def ticker():
        (OUT.parent / "task-started").write_text(str(len(list(OUT.parent.glob("task-*")))))
        try:
            await asyncio.sleep(3600)
        finally:
            (OUT.parent / "task-stopped").write_text("yes")

    api.task(ticker)
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    root.mkdir()
    out = tmp_path / "out" / "events.txt"
    out.parent.mkdir()
    monkeypatch.setenv("PLUGIN_TEST_OUT", str(out))
    monkeypatch.setattr(plugin_mod, "REGISTER_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(plugin_mod, "HANDLER_TIMEOUT_SECONDS", 0.2)

    class Env:
        def __init__(self):
            self.root = root
            self.roots = [root]
            self.data = tmp_path / "home"
            self.out = out

        def client(self):
            store = QuietStore()
            host = PluginHost(store, roots=self.roots, data_dir=self.data)
            app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
            client = TestClient(app)
            client.store, client.host = store, host
            return client

    return Env()


def by_id(client):
    response = client.get("/api/v2/plugins", headers=AUTH)
    assert response.status_code == 200
    return {p["id"]: p for p in response.json()}


def wait_until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


def lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def session(session_id, status):
    return {"id": session_id, "status": status}


# ---------------------------------------------------------------------- loading


def test_no_plugins_folder_is_just_the_core(env, tmp_path):
    env.root.rmdir()
    with env.client() as client:
        assert by_id(client) == {}
        assert client.get("/api/v2/sessions", headers=AUTH).status_code == 200
        assert client.get("/api/v2/service", headers=AUTH).json()["plugins"] == 0


def test_a_good_plugin_loads_and_serves_behind_the_token(env):
    write_plugin(env.root, "good", GOOD, ui=["session-badge"])
    with env.client() as client:
        info = by_id(client)["good"]
        assert info == {
            "id": "good",
            "name": "Good",
            "version": "0.1.0",
            "description": "The good plugin",
            "state": "enabled",
            "error": None,
            "ui": ["session-badge"],
        }
        assert client.get("/api/v2/plugins/good/hello", headers=AUTH).json() == {"hello": "good"}
        assert client.get("/api/v2/plugins/good/hello").status_code == 401
        assert client.get("/api/v2/service", headers=AUTH).json()["plugins"] == 1


def test_plugin_routes_are_not_in_the_api_schema(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        schema = client.app.routes  # the v2 mount
        v2 = next(r for r in schema if getattr(r, "path", "") == "/api/v2").app
        assert not any("/plugins/good" in p for p in v2.openapi()["paths"])


def test_store_is_a_json_file_in_the_plugins_folder(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        assert client.post("/api/v2/plugins/good/save", headers=AUTH).json() == {"saved": True}
    assert json.loads((env.data / "plugins" / "good" / "store.json").read_text()) == {"saved": True}


def test_sessions_is_a_snapshot(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        client.portal.call(client.store.set_rows, {"a-1": session("a-1", "running")})
        assert client.get("/api/v2/plugins/good/count", headers=AUTH).json() == {"sessions": 1}


def test_a_plugin_can_import_its_own_modules(env):
    folder = write_plugin(env.root, "multi", "from .helpers import ANSWER\n\ndef register(api):\n    api.router.get('/answer')(lambda: ANSWER)\n")
    (folder / "helpers.py").write_text("ANSWER = 42\n")
    with env.client() as client:
        assert client.get("/api/v2/plugins/multi/answer", headers=AUTH).json() == 42


# ---------------------------------------------------------------------- login methods

LOGIN = """
def register(api):
    @api.public_router.post("/sign-in")
    def sign_in(body: dict):
        if body.get("secret") != "open sesame":
            from fastapi import HTTPException
            raise HTTPException(401, "No")
        return api.sign_in("Phone")
"""


def test_the_service_token_is_always_a_login_method(env):
    with env.client() as client:
        assert client.get("/api/v2/auth/methods").json() == [{"id": "token", "name": "Service token"}]


def test_a_login_plugin_signs_in_an_ordinary_client_without_a_token(env):
    write_plugin(env.root, "secret", LOGIN, ui=["login"])
    with env.client() as client:
        methods = client.get("/api/v2/auth/methods").json()
        assert methods == [{"id": "token", "name": "Service token"}, {"id": "secret", "name": "Secret"}]
        assert client.post("/api/v2/plugins/secret/sign-in", json={"secret": "wrong"}).status_code == 401
        body = client.post("/api/v2/plugins/secret/sign-in", json={"secret": "open sesame"}).json()
        assert set(body) == {"client_id", "token", "expires_in", "ttl"}
        mine = {"Authorization": f"Bearer {body['token']}"}
        assert client.get("/api/v2/sessions", headers=mine).status_code == 200
        listed = client.get("/api/v2/auth/clients", headers=mine).json()
        assert [c["name"] for c in listed if c["current"]] == ["Phone"]
        # Renewed and revoked like any client.
        assert client.post("/api/v2/auth/renew", headers=mine).status_code == 200


def test_a_disabled_login_plugin_is_no_way_in(env):
    write_plugin(env.root, "secret", LOGIN, ui=["login"])
    with env.client() as client:
        client.patch("/api/v2/plugins/secret", json={"enabled": False}, headers=AUTH)
        assert [m["id"] for m in client.get("/api/v2/auth/methods").json()] == ["token"]
        assert client.post("/api/v2/plugins/secret/sign-in", json={"secret": "open sesame"}).status_code == 404


def test_only_a_login_plugin_may_have_public_routes(env):
    write_plugin(env.root, "sneaky", LOGIN, ui=["session-badge"])
    with env.client() as client:
        info = by_id(client)["sneaky"]
        assert info["state"] == "failed" and "login" in info["error"]
        assert client.post("/api/v2/plugins/sneaky/sign-in", json={"secret": "open sesame"}).status_code == 404
        assert [m["id"] for m in client.get("/api/v2/auth/methods").json()] == ["token"]


# ---------------------------------------------------------------------- enable and disable


def test_disable_hides_routes_stops_tasks_and_saves_the_flag(env):
    write_plugin(env.root, "good", GOOD)
    stopped = env.out.parent / "task-stopped"
    with env.client() as client:
        wait_until((env.out.parent / "task-started").exists)
        response = client.patch("/api/v2/plugins/good", json={"enabled": False}, headers=AUTH)
        assert response.json()["state"] == "disabled"
        assert client.get("/api/v2/plugins/good/hello", headers=AUTH).status_code == 404
        assert stopped.exists()
    assert json.loads((env.data / "state" / "plugins.json").read_text()) == {"good": {"enabled": False}}


def test_enable_brings_routes_and_tasks_back(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        client.patch("/api/v2/plugins/good", json={"enabled": False}, headers=AUTH)
        (env.out.parent / "task-started").unlink()
        response = client.patch("/api/v2/plugins/good", json={"enabled": True}, headers=AUTH)
        assert response.json()["state"] == "enabled"
        assert client.get("/api/v2/plugins/good/hello", headers=AUTH).status_code == 200
        wait_until((env.out.parent / "task-started").exists)


def test_the_saved_flag_wins_over_the_default_after_a_restart(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        client.patch("/api/v2/plugins/good", json={"enabled": False}, headers=AUTH)
    with env.client() as client:
        assert by_id(client)["good"]["state"] == "disabled"


def test_a_plugin_disabled_at_start_is_not_imported_until_enabled(env):
    marker = env.out.parent / "imported"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n\ndef register(api):\n    api.router.get('/x')(lambda: 1)\n"
    write_plugin(env.root, "lazy", code, default_enabled=False)
    with env.client() as client:
        assert by_id(client)["lazy"]["state"] == "disabled"
        assert not marker.exists()
        assert client.get("/api/v2/plugins/lazy/x", headers=AUTH).status_code == 404
        client.patch("/api/v2/plugins/lazy", json={"enabled": True}, headers=AUTH)
        assert marker.exists()
        assert client.get("/api/v2/plugins/lazy/x", headers=AUTH).json() == 1


WINDS_DOWN = """
import os
from pathlib import Path

OUT = Path(os.environ["PLUGIN_TEST_OUT"])

def register(api):
    api.router.get("/x")(lambda: 1)
    api.on_disable(lambda: OUT.write_text("wound down"))
"""


def test_turning_a_plugin_off_calls_its_on_disable(env):
    write_plugin(env.root, "winder", WINDS_DOWN)
    with env.client() as client:
        client.patch("/api/v2/plugins/winder", json={"enabled": False}, headers=AUTH)
        wait_until(env.out.exists)
    assert env.out.read_text() == "wound down"


def test_stopping_the_service_does_not_call_on_disable(env):
    write_plugin(env.root, "winder", WINDS_DOWN)
    with env.client() as client:
        assert client.get("/api/v2/plugins/winder/x", headers=AUTH).json() == 1
    assert not env.out.exists()


def test_a_failing_on_disable_still_turns_the_plugin_off(env):
    write_plugin(env.root, "winder", "def register(api):\n    api.on_disable(lambda: 1 / 0)\n")
    with env.client() as client:
        assert client.patch("/api/v2/plugins/winder", json={"enabled": False}, headers=AUTH).json()["state"] == "disabled"


def test_unknown_plugin_is_404(env):
    with env.client() as client:
        assert client.patch("/api/v2/plugins/nope", json={"enabled": True}, headers=AUTH).status_code == 404
        assert client.patch("/api/v2/plugins/nope", json={"enabled": True}).status_code == 401


# ---------------------------------------------------------------------- events


def test_session_events_reach_handlers(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        call = client.portal.call
        call(client.store.set_rows, {"a-1": session("a-1", "starting")})
        call(client.store.set_rows, {"a-1": session("a-1", "running")})
        call(client.store.set_rows, {"a-1": session("a-1", "exited")})
        call(client.store.set_rows, {})
        wait_until(lambda: len(lines(env.out)) == 3)
        assert sorted(lines(env.out)) == ["session.closed:a-1", "session.exited:a-1", "session.started:a-1"]


def test_disabled_plugins_hear_nothing(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        client.patch("/api/v2/plugins/good", json={"enabled": False}, headers=AUTH)
        client.portal.call(client.store.set_rows, {"a-1": session("a-1", "running")})
        time.sleep(0.2)
        assert lines(env.out) == []


def test_emit_and_list_changes_reach_the_events_socket(env):
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        ticket = client.post("/api/v2/tickets", json={"purpose": "events"}, headers=AUTH).json()["ticket"]
        with client.websocket_connect(f"/api/v2/ws/events?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            client.post("/api/v2/plugins/good/emit", headers=AUTH)
            assert ws.receive_json() == {"type": "plugin", "id": "good", "data": {"n": 1}}
            client.patch("/api/v2/plugins/good", json={"enabled": False}, headers=AUTH)
            assert ws.receive_json() == {"type": "plugins"}


# ---------------------------------------------------------------------- broken plugins


@pytest.mark.parametrize("plugin_id", BROKEN)
def test_a_broken_plugin_fails_with_its_reason(env, plugin_id):
    code, toml, reason = BROKEN[plugin_id]
    write_plugin(env.root, plugin_id, code, toml)
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        plugins = by_id(client)
        assert plugins[plugin_id]["state"] == "failed"
        assert reason in plugins[plugin_id]["error"]
        assert plugins["good"]["state"] == "enabled"
        assert client.get("/api/v2/sessions", headers=AUTH).status_code == 200


def test_a_plugin_for_another_api_version_is_incompatible(env):
    write_plugin(env.root, "future", GOOD, plugin_api=99)
    with env.client() as client:
        info = by_id(client)["future"]
        assert info["state"] == "incompatible"
        assert "Update agentop" in info["error"]
        assert client.patch("/api/v2/plugins/future", json={"enabled": True}, headers=AUTH).json()["state"] == "incompatible"


def test_a_failed_plugin_needs_a_restart_to_retry(env):
    write_plugin(env.root, "register-raises", BROKEN["register-raises"][0])
    with env.client() as client:
        client.patch("/api/v2/plugins/register-raises", json={"enabled": False}, headers=AUTH)
        info = client.patch("/api/v2/plugins/register-raises", json={"enabled": True}, headers=AUTH).json()
        assert info["state"] == "failed"
        assert "Restart the service" in info["error"]


def test_a_failing_route_is_a_500_and_nothing_else(env):
    write_plugin(env.root, "route-raises", FAILS_LATER["route-raises"])
    with env.client() as client:
        client_500 = TestClient(client.app, raise_server_exceptions=False)
        assert client_500.get("/api/v2/plugins/route-raises/boom", headers=AUTH).status_code == 500
        assert by_id(client)["route-raises"]["state"] == "enabled"
        assert client.get("/api/v2/sessions", headers=AUTH).status_code == 200


def test_a_crashing_task_fails_the_plugin(env):
    write_plugin(env.root, "task-crashes", FAILS_LATER["task-crashes"])
    with env.client() as client:
        wait_until(lambda: by_id(client)["task-crashes"]["state"] == "failed")
        assert "task boom" in by_id(client)["task-crashes"]["error"]


@pytest.mark.parametrize(
    "body",
    ["raise RuntimeError('handler boom')", "import time; time.sleep(0.5)"],
    ids=["raises", "hangs"],
)
def test_repeated_handler_errors_fail_the_plugin(env, body):
    code = f"def register(api):\n    def handler(rows):\n        {body}\n    api.on('scan', handler)\n"
    write_plugin(env.root, "bad-handler", code)
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        rows = {}
        for i in range(plugin_mod.MAX_HANDLER_ERRORS):
            rows[f"s-{i}"] = session(f"s-{i}", "running")
            client.portal.call(client.store.set_rows, dict(rows))
        wait_until(lambda: by_id(client)["bad-handler"]["state"] == "failed")
        assert "event handler" in by_id(client)["bad-handler"]["error"]
        # The core never waited for the handlers, and the good plugin heard every start.
        wait_until(lambda: len(lines(env.out)) == plugin_mod.MAX_HANDLER_ERRORS)
        assert by_id(client)["good"]["state"] == "enabled"


def test_every_broken_plugin_at_once_leaves_the_core_working(env):
    for plugin_id, (code, toml, _) in BROKEN.items():
        write_plugin(env.root, plugin_id, code, toml)
    write_plugin(env.root, "good", GOOD)
    with env.client() as client:
        plugins = by_id(client)
        assert {p["state"] for i, p in plugins.items() if i != "good"} == {"failed"}
        assert plugins["good"]["state"] == "enabled"
        assert client.get("/api/v2/plugins/good/hello", headers=AUTH).status_code == 200
        assert client.get("/api/v2/sessions", headers=AUTH).status_code == 200
        assert client.get("/api/v2/health").json()["status"] == "ok"


def test_folders_that_are_not_plugins_are_skipped(env):
    (env.root / "__pycache__").mkdir()
    (env.root / ".hidden").mkdir()
    (env.root / "README.md").write_text("notes")
    with env.client() as client:
        assert by_id(client) == {}


# ---------------------------------------------------------------------- plugin folders


def test_the_built_in_folder_is_always_searched_first(tmp_path, monkeypatch):
    monkeypatch.delenv(plugin_manifest.DIRS_ENV, raising=False)
    assert plugin_manifest.plugin_roots() == [plugin_manifest.DEFAULT_ROOT]
    one, two = tmp_path / "one", tmp_path / "two"
    monkeypatch.setenv(plugin_manifest.DIRS_ENV, f"{one}::{two}:{one}:{plugin_manifest.DEFAULT_ROOT}")
    assert plugin_manifest.plugin_roots() == [plugin_manifest.DEFAULT_ROOT, one, two]


def test_plugins_load_from_every_folder_and_the_first_of_a_name_wins(env, tmp_path):
    extra = tmp_path / "extra"
    write_plugin(env.root, "good", GOOD)
    write_plugin(extra, "good", "raise RuntimeError('the second good ran')\n")
    write_plugin(extra, "other", "def register(api): pass\n")
    env.roots = [env.root, tmp_path / "missing", extra]
    with env.client() as client:
        plugins = by_id(client)
        assert {i: p["state"] for i, p in plugins.items()} == {"good": "enabled", "other": "enabled"}
        assert client.host.plugins["other"].folder == extra / "other"


def test_the_service_finds_the_built_in_plugins_and_the_extra_folders(tmp_path, monkeypatch):
    install(tmp_path / "broken")
    monkeypatch.setenv(plugin_manifest.DIRS_ENV, str(tmp_path / "broken"))
    host = PluginHost(QuietStore(), data_dir=tmp_path / "home")
    names = [folder.name for folder in host._folders()]
    assert {"dialogue", "memory", "viewer"} <= set(names)
    assert set(BROKEN) | set(FAILS_LATER) <= set(names)


def test_flags_that_cannot_be_parsed_are_moved_aside(tmp_path):
    host = PluginHost(QuietStore(), data_dir=tmp_path / "home")
    host.flags_path.parent.mkdir(parents=True)
    host.flags_path.write_text('{"memory": {"enabled": tr')
    host._write_flag("viewer", True)
    assert json.loads(host.flags_path.read_text()) == {"viewer": {"enabled": True}}
    [aside] = host.flags_path.parent.glob("plugins.json.corrupt-*")
    assert aside.read_text() == '{"memory": {"enabled": tr'


def test_flags_that_cannot_be_read_are_not_written(tmp_path, monkeypatch):
    host = PluginHost(QuietStore(), data_dir=tmp_path / "home")
    host._write_flag("memory", False)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError):
        host._write_flag("viewer", True)
    monkeypatch.undo()
    assert json.loads(host.flags_path.read_text()) == {"memory": {"enabled": False}}
