"""A project's app layout, .project/layout.json, and its routes."""

import json

import pytest
from fastapi.testclient import TestClient

from agentop import projects
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import PluginHost
from tests.conftest import signed_in
from tests.test_plugins import AUTH, TOKEN, QuietStore


def layout(**changes):
    value = {
        "version": 1,
        "left-panel": {"open": True},
        "mid-panel": {
            "left": {
                "open": True,
                "tabs": [
                    {"id": "start", "content": {"kind": "start"}},
                    {"id": "t1", "content": {"kind": "session", "sessionId": "dev-a1b2c3"}},
                ],
                "active": "t1",
            },
            "right": {
                "open": True,
                "tabs": [{"id": "t2", "content": {"kind": "file", "file": {"sessionId": "dev-a1b2c3", "source": "workdir", "path": "src/x.py"}}}],
                "active": "t2",
            },
        },
        "right-panel": {"open": False},
        "focused": "right",
    }
    value.update(changes)
    return value


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    store = QuietStore()
    host = PluginHost(store, roots=[], data_dir=tmp_path / "home")
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
    with TestClient(app) as test_client:
        yield test_client


def test_a_layout_is_saved_in_the_project_and_read_back_as_written(client):
    projects.create("alpha", "projects")
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).status_code == 204
    assert client.put("/api/v2/projects/alpha/layout", json=layout(), headers=AUTH).status_code == 204
    path = projects.folder("alpha") / ".project" / "layout.json"
    assert json.loads(path.read_text()) == layout()
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).json() == layout()


def test_each_project_has_its_own(client):
    projects.create("alpha", "projects")
    projects.create("beta", "projects")
    client.put("/api/v2/projects/alpha/layout", json=layout(focused="left"), headers=AUTH)
    assert client.get("/api/v2/projects/beta/layout", headers=AUTH).status_code == 204
    client.put("/api/v2/projects/beta/layout", json=layout(), headers=AUTH)
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).json()["focused"] == "left"
    assert client.get("/api/v2/projects/beta/layout", headers=AUTH).json()["focused"] == "right"


def test_sessions_is_the_default_project_and_is_made_on_the_first_save(client):
    assert client.get("/api/v2/projects/default/layout", headers=AUTH).status_code == 204
    assert client.put("/api/v2/projects/default/layout", json=layout(), headers=AUTH).status_code == 204
    assert projects.get("default")["owner"] == projects.CORE
    assert client.get("/api/v2/projects/default/layout", headers=AUTH).json() == layout()


@pytest.mark.parametrize("name", ["nope", "Bad Name", "..", "alpha%2F..%2Fbeta"])
def test_only_a_known_project_has_a_layout(client, name):
    projects.create("alpha", "projects")
    assert client.get(f"/api/v2/projects/{name}/layout", headers=AUTH).status_code == 404
    assert client.put(f"/api/v2/projects/{name}/layout", json=layout(), headers=AUTH).status_code == 404


def test_a_closed_project_keeps_its_file_but_has_no_layout_route(client):
    projects.create("alpha", "projects")
    client.put("/api/v2/projects/alpha/layout", json=layout(), headers=AUTH)
    projects.close("alpha")
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).status_code == 404
    assert projects.layout_path("alpha").is_file()


def test_the_routes_need_a_token(client):
    projects.create("alpha", "projects")
    assert client.get("/api/v2/projects/alpha/layout").status_code == 401
    assert client.put("/api/v2/projects/alpha/layout", json=layout()).status_code == 401
    assert not projects.layout_path("alpha").exists()


@pytest.mark.parametrize(
    "bad",
    [
        layout(version=2),
        layout(focused="middle"),
        layout(extra=True),
        layout(**{"left-panel": {"open": "yes"}}),
        layout(**{"mid-panel": {"left": {"open": True, "tabs": [], "active": "gone"}, "right": {"open": False, "tabs": []}}}),
        layout(**{"mid-panel": {"left": {"open": True, "tabs": [{"id": "x", "content": {"kind": "session", "sessionId": "a"}}] * 2}, "right": {"open": False}}}),
        layout(**{"mid-panel": {"left": {"open": True, "tabs": [{"id": "x", "content": {"kind": "shell", "cmd": "rm"}}]}, "right": {"open": False}}}),
        layout(**{"mid-panel": {"left": {"open": True, "tabs": [{"id": f"t{i}", "content": {"kind": "status"}} for i in range(65)]}, "right": {"open": False}}}),
    ],
)
def test_a_save_that_isnt_a_layout_is_refused_and_changes_nothing(client, bad):
    projects.create("alpha", "projects")
    client.put("/api/v2/projects/alpha/layout", json=layout(), headers=AUTH)
    assert client.put("/api/v2/projects/alpha/layout", json=bad, headers=AUTH).status_code == 422
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).json() == layout()


def test_a_hand_edit_is_read_as_written_and_an_unreadable_one_is_moved_aside(client):
    projects.create("alpha", "projects")
    path = projects.layout_path("alpha")
    path.write_text(json.dumps({"version": 1, "focused": "right", "note": "by hand"}))
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).json()["note"] == "by hand"
    path.write_text("{ not json")
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).status_code == 204
    assert not path.exists()
    assert [p.name.startswith("layout.json.corrupt-") for p in path.parent.iterdir() if p.name.startswith("layout.json")] == [True]
    path.write_text("[1, 2]")
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).status_code == 204


def test_a_saved_layout_drops_empty_optional_fields_but_keeps_no_active_tab(client):
    projects.create("alpha", "projects")
    value = layout(**{"mid-panel": {"left": {"open": False, "tabs": [], "active": None}, "right": {"open": True, "tabs": [{"id": "p", "content": {"kind": "plugin", "plugin": "memory", "view": "search", "key": None}}], "active": "p"}}})
    client.put("/api/v2/projects/alpha/layout", json=value, headers=AUTH)
    saved = json.loads(projects.layout_path("alpha").read_text())
    assert saved["mid-panel"]["left"] == {"open": False, "tabs": [], "active": None}
    assert saved["mid-panel"]["right"]["tabs"][0]["content"] == {"kind": "plugin", "plugin": "memory", "view": "search"}


def test_panel_widths_and_the_split_are_kept_and_bounded(client):
    projects.create("alpha", "projects")
    sized = layout(**{"left-panel": {"open": True, "width": 300}, "right-panel": {"open": False, "width": 420}})
    sized["mid-panel"]["split"] = 0.35
    assert client.put("/api/v2/projects/alpha/layout", json=sized, headers=AUTH).status_code == 204
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).json() == sized
    for bad in ({"open": True, "width": 50}, {"open": True, "width": 300.5}, {"open": True, "width": "300"}):
        assert client.put("/api/v2/projects/alpha/layout", json=layout(**{"left-panel": bad}), headers=AUTH).status_code == 422
    too_far = layout()
    too_far["mid-panel"]["split"] = 1.5
    assert client.put("/api/v2/projects/alpha/layout", json=too_far, headers=AUTH).status_code == 422
    assert client.get("/api/v2/projects/alpha/layout", headers=AUTH).json() == sized
