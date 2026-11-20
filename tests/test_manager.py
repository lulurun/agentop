"""Service control, with a fake systemctl. The real unit is never touched."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentop.service import manager, routes, update
from agentop.service.api import admin
from tests.conftest import signed_in


@pytest.fixture
def systemctl(tmp_path, monkeypatch):
    """A systemctl that records its arguments and reports `MainPID` from a file."""
    log = tmp_path / "calls"
    pid = tmp_path / "pid"
    script = tmp_path / "systemctl"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {log}\n'
        'case "$*" in *show*) '
        'echo LoadState=loaded; echo ActiveState=active; echo "MainPID=$(cat ' + str(pid) + ')";; esac\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("AGENTOP_UNIT", "agentop-test.service")
    pid.write_text("0")
    return SimpleNamespace(calls=lambda: log.read_text().splitlines() if log.exists() else [], pid=pid)


def test_a_process_is_managed_only_if_it_is_the_units_main_process(systemctl):
    assert manager.for_process(os.getpid()).name == "none"
    systemctl.pid.write_text(str(os.getpid()))
    assert manager.for_process(os.getpid()).name == "systemd"


def test_a_machine_without_systemctl_has_no_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert manager.for_process(1).name == "none"


def test_no_manager_refuses():
    with pytest.raises(manager.ManagerError):
        manager.NoManager().restart()


def test_restart_does_not_wait_for_its_own_death(systemctl):
    manager.SystemdManager().restart()
    assert "--user restart --no-block agentop-test.service" in systemctl.calls()


def test_a_failed_command_is_reported(systemctl, tmp_path):
    (tmp_path / "systemctl").write_text("#!/bin/sh\necho no such unit >&2\nexit 5\n")
    with pytest.raises(manager.ManagerError, match="no such unit"):
        manager.SystemdManager().stop()


@pytest.fixture
def api(monkeypatch):
    """/api/v2 with its token check out of the way."""
    from pathlib import Path

    from agentop.service.config import Settings
    from agentop.service.identity import Identity

    monkeypatch.setattr(admin, "LIFECYCLE_DELAY_SECONDS", 0)
    ident = Identity(token="t", instance_id="i", name="box")
    ctx = routes.Context(Settings(), ident, SimpleNamespace(), clients=signed_in("t"))
    client = TestClient(routes.create_v2_app(ctx), headers={"Authorization": "Bearer t"})
    assert Path(routes.REPO_ROOT).exists()
    return client


def test_there_is_no_start(api):
    assert api.post("/admin/start").status_code in (404, 405)


def test_a_process_that_is_not_agentop_serve_reports_no_capabilities_and_refuses(api, systemctl):
    assert api.get("/service").json()["manager"] == "none"
    assert api.get("/service").json()["capabilities"] == {"stop": False, "restart": False, "update": False}
    for path in ("/admin/restart", "/admin/stop", "/admin/update"):
        assert api.post(path).status_code == 409
    assert not any("restart" in call or "stop" in call for call in systemctl.calls())


def test_managed_restart_is_accepted_and_runs(api, systemctl):
    systemctl.pid.write_text(str(os.getpid()))
    info = api.get("/service").json()
    assert info["manager"] == "systemd" and info["capabilities"]["restart"]
    with api:
        assert api.post("/admin/restart").status_code == 202
    assert any("restart --no-block" in call for call in systemctl.calls())


def test_a_running_update_is_not_started_twice(api, systemctl, monkeypatch):
    systemctl.pid.write_text(str(os.getpid()))
    monkeypatch.setattr(update, "is_running", lambda: True)
    response = api.post("/admin/update")
    assert response.status_code == 409 and "already running" in response.json()["detail"]


def test_update_info_keeps_a_stable_shape(api, tmp_path, monkeypatch):
    monkeypatch.setattr(update, "available", lambda repo, update_from: dict.fromkeys(("current", "upstream", "behind", "check_error")))
    monkeypatch.setattr(update, "is_running", lambda: False)
    monkeypatch.setattr(update, "read_status", lambda: {"state": "done", "to_commit": "abc1234"})
    body = api.get("/admin/update").json()
    assert body["running"] is False and body["last_run"]["state"] == "done"


def test_version_says_what_runs_and_what_update_brings_in_one_form(api, monkeypatch):
    monkeypatch.setattr(update, "running_version", lambda repo, update_from: "a012345678")
    monkeypatch.setattr(update, "latest", lambda repo, update_from: {"latest": "b012345678", "checked_at": 1.0, "check_error": None})
    body = api.get("/admin/version").json()
    assert (body["update_from"], body["current"], body["latest"]) == ("main", "a012345678", "b012345678")
    assert "ui_build" in body


def test_available_counts_commits_behind(tmp_path):
    import subprocess

    def git(cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=cwd, check=True, capture_output=True)

    git(tmp_path, "init", "--bare", "-b", "main", "o.git")
    git(tmp_path, "clone", "o.git", "a")
    git(tmp_path / "a", "commit", "--allow-empty", "-m", "1")
    git(tmp_path / "a", "push", "origin", "main")
    git(tmp_path, "clone", "o.git", "b")
    git(tmp_path / "a", "commit", "--allow-empty", "-m", "2")
    git(tmp_path / "a", "push", "origin", "main")
    update._checked.clear()
    result = update.available(tmp_path / "b")
    assert result["behind"] == 1 and result["check_error"] is None


def test_available_from_releases_names_the_newest_tag_and_never_goes_back(tmp_path):
    import subprocess

    def git(cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=cwd, check=True, capture_output=True)

    git(tmp_path, "init", "--bare", "-b", "main", "o.git")
    git(tmp_path, "clone", "o.git", "a")
    git(tmp_path / "a", "commit", "--allow-empty", "-m", "1")
    git(tmp_path / "a", "tag", "v1.9.0")
    git(tmp_path / "a", "commit", "--allow-empty", "-m", "2")
    git(tmp_path / "a", "tag", "v1.10.0")  # newer than v1.9.0 by version, not by name
    git(tmp_path / "a", "tag", "v2.0.0-rc1")  # not a release
    git(tmp_path / "a", "push", "--tags", "origin", "main")
    git(tmp_path, "clone", "o.git", "b")
    git(tmp_path / "b", "checkout", "-q", "--detach", "v1.9.0")
    update._checked.clear()
    result = update.available(tmp_path / "b", "release")
    assert (result["upstream"], result["behind"], result["check_error"]) == ("v1.10.0", 1, None)

    git(tmp_path / "b", "checkout", "-q", "main")
    git(tmp_path / "b", "commit", "--allow-empty", "-m", "ahead of the release")
    update._checked.clear()
    assert update.available(tmp_path / "b", "release")["behind"] == 0


def test_update_from_must_be_main_or_release():
    from agentop.service.config import Settings

    with pytest.raises(ValueError, match="update_from"):
        Settings(update_from="nightly")
