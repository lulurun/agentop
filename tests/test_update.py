"""update.sh, on throwaway repositories, with a stand-in for the service.

Nothing here touches the real install: the script is pointed at a clone in
tmp_path, and "restarting" just publishes that clone's VERSION file for the
health check to find.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "agentop" / "service" / "update.sh"


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Install:
    def __init__(self, root: Path, port: int):
        self.root = root
        self.upstream = root / "upstream"
        self.dev = root / "dev"
        self.repo = root / "install"
        self.www = root / "www"
        self.data = root / "data"
        self.port = port
        self.www.mkdir()
        git(root, "init", "--bare", "-b", "main", "origin.git")
        git(root, "clone", "origin.git", "dev")
        self.commit("v1")
        git(self.dev, "push", "origin", "main")
        git(root, "clone", "origin.git", "install")
        self.good = git(self.repo, "rev-parse", "HEAD")

    def commit(self, version, **extra):
        (self.dev / "VERSION").write_text(version)
        for name, text in extra.items():
            (self.dev / name).parent.mkdir(parents=True, exist_ok=True)
            (self.dev / name).write_text(text)
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-m", version)

    def publish(self, version, **extra):
        self.commit(version, **extra)
        git(self.dev, "push", "origin", "main")

    def release(self, tag, version=None, **extra):
        self.publish(version or tag, **extra)
        git(self.dev, "tag", tag)
        git(self.dev, "push", "origin", tag)

    def run(self, **env):
        restart = f"test ! -f BAD_START || {{ rm -f {self.www}/health; exit 0; }}; cp VERSION {self.www}/health"
        return subprocess.run(
            ["/bin/bash", str(SCRIPT)],
            env={
                **os.environ,
                "AGENTOP_REPO": str(self.repo),
                "AGENTOP_HOME": str(self.data),
                "AGENTOP_HEALTH_URL": f"http://127.0.0.1:{self.port}/health",
                "AGENTOP_HEALTH_TIMEOUT": "2",
                "AGENTOP_INSTALL_CMD": "true",
                "AGENTOP_BUILD_CMD": "test ! -f BAD_BUILD",
                "AGENTOP_RESTART_CMD": restart,
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    @property
    def status(self):
        return json.loads((self.data / "state" / "update-status.json").read_text())

    @property
    def head(self):
        return git(self.repo, "rev-parse", "HEAD")

    @property
    def serving(self):
        path = self.www / "health"
        return path.read_text() if path.exists() else None


@pytest.fixture
def install(tmp_path):
    www = tmp_path / "www"
    handler = functools.partial(Quiet, directory=str(www))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    yield Install(tmp_path, server.server_address[1])
    server.shutdown()


def test_applies_a_new_commit(install):
    install.publish("v2")
    assert install.run().returncode == 0
    assert install.status["state"] == "done"
    assert install.status["to_commit"] == install.head[:7]
    assert install.serving == "v2"


def test_nothing_to_do(install):
    assert install.run().returncode == 0
    assert install.status["state"] == "done"
    assert install.status["message"] == "Already up to date."
    assert install.serving is None  # never restarted


def test_refuses_a_dirty_tree_and_changes_nothing(install):
    install.publish("v2")
    (install.repo / "VERSION").write_text("edited by hand")
    assert install.run().returncode == 1
    assert install.status["state"] == "failed"
    assert "uncommitted" in install.status["message"]
    assert install.head == install.good
    assert (install.repo / "VERSION").read_text() == "edited by hand"


def test_refuses_another_branch(install):
    install.publish("v2")
    git(install.repo, "checkout", "-q", "-b", "experiment")
    assert install.run().returncode == 1
    assert "isn't on main" in install.status["message"]
    assert install.head == install.good


def test_refuses_to_lose_local_commits(install):
    install.publish("v2")
    (install.repo / "LOCAL").write_text("mine")
    git(install.repo, "add", "-A")
    git(install.repo, "commit", "-m", "local")
    local = install.head
    assert install.run().returncode == 1
    assert "aren't on the remote" in install.status["message"]
    assert install.head == local


def test_a_failed_build_rolls_back(install):
    install.publish("v2", BAD_BUILD="x")
    assert install.run().returncode == 1
    assert install.status["state"] == "rolled_back"
    assert install.head == install.good
    assert install.serving == "v1"


def test_a_commit_that_fails_health_rolls_back(install):
    install.publish("v2", BAD_START="x")
    assert install.run().returncode == 1
    assert install.status["state"] == "rolled_back"
    assert "previous version" in install.status["message"]
    assert install.head == install.good
    assert install.serving == "v1"


def test_says_so_when_going_back_also_fails(install):
    install.publish("v2")
    assert install.run(AGENTOP_RESTART_CMD="false").returncode == 2
    assert install.status["state"] == "rollback_failed"
    assert "needs attention" in install.status["message"]
    assert install.head == install.good


def test_a_replaced_script_does_not_disturb_the_run(install):
    """git pull can rewrite update.sh under the running bash."""
    copy = install.repo / "update.sh"
    copy.write_text(SCRIPT.read_text())
    git(install.repo, "add", "-A")
    git(install.repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "script")
    git(install.repo, "push", "origin", "main")
    git(install.dev, "pull", "-q", "origin", "main")
    install.publish("v2", **{"update.sh": "echo replaced; exit 7\n" * 400})

    result = subprocess.run(
        ["/bin/bash", str(copy)],
        env={
            **os.environ,
            "AGENTOP_REPO": str(install.repo),
            "AGENTOP_HOME": str(install.data),
            "AGENTOP_HEALTH_URL": f"http://127.0.0.1:{install.port}/health",
            "AGENTOP_HEALTH_TIMEOUT": "2",
            "AGENTOP_INSTALL_CMD": "true",
            "AGENTOP_BUILD_CMD": "true",
            "AGENTOP_RESTART_CMD": f"cp VERSION {install.www}/health",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert install.status["state"] == "done"


def release_install(install):
    """The install as the desktop app keeps it: on a tag, not on a branch."""
    install.release("v1.0.0")
    git(install.repo, "fetch", "-q", "--tags", "origin")
    git(install.repo, "checkout", "-q", "--detach", "v1.0.0")
    install.good = install.head


def test_from_releases_checks_out_the_newest_tag_and_ignores_main(install):
    release_install(install)
    install.publish("work in progress")  # on main, not released
    assert install.run(AGENTOP_UPDATE_FROM="release").returncode == 0
    assert install.status["message"] == "Already up to date."

    install.release("v1.1.0")
    install.publish("after the release")
    assert install.run(AGENTOP_UPDATE_FROM="release").returncode == 0
    assert install.status["state"] == "done"
    assert install.head == git(install.repo, "rev-parse", "v1.1.0^{commit}")
    assert install.serving == "v1.1.0"


def test_from_releases_never_goes_backwards(install):
    install.release("v1.0.0")
    install.publish("newer than the release")
    git(install.repo, "pull", "-q")
    ahead = install.head
    assert install.run(AGENTOP_UPDATE_FROM="release").returncode == 0
    assert "Already newer than the latest release, v1.0.0" in install.status["message"]
    assert install.head == ahead


def test_from_releases_a_failed_release_rolls_back_to_the_old_tag(install):
    release_install(install)
    install.release("v1.1.0", BAD_BUILD="x")
    assert install.run(AGENTOP_UPDATE_FROM="release").returncode == 1
    assert install.status["state"] == "rolled_back"
    assert install.head == install.good


def test_from_releases_with_none_yet_changes_nothing(install):
    assert install.run(AGENTOP_UPDATE_FROM="release").returncode == 1
    assert install.status["message"] == "There is no release yet."
    assert install.head == install.good


@pytest.fixture
def versions(monkeypatch):
    from agentop.service import update

    monkeypatch.setattr(update, "_latest", {})
    monkeypatch.setattr(update, "_running", {})
    return update


def test_following_releases_the_latest_is_the_newest_tag_above_what_runs_asked_once_an_hour(install, versions):
    (install.repo / "pyproject.toml").write_text('[project]\nversion = "1.9.0"\n')
    assert versions.running_version(install.repo, "release") == "1.9.0"
    assert versions.latest(install.repo, "release")["latest"] == "1.9.0"
    versions._latest.clear()
    for tag in ("v1.8.0", "v1.10.0", "v1.10.0-rc1"):
        install.release(tag)
    found = versions.latest(install.repo, "release")
    assert (found["latest"], found["check_error"]) == ("1.10.0", None)
    install.release("v2.0.0")
    assert versions.latest(install.repo, "release")["latest"] == "1.10.0"


def test_following_main_versions_are_commits_and_the_latest_never_goes_back(install, versions):
    current = versions.running_version(install.repo, "main")
    assert current == install.good[:10]
    assert versions.latest(install.repo, "main")["latest"] == current
    versions._latest.clear()
    install.publish("v2")
    assert versions.latest(install.repo, "main")["latest"] == git(install.dev, "rev-parse", "HEAD")[:10]
    git(install.repo, "pull", "-q")
    assert versions.running_version(install.repo, "main") == current  # what runs, until a restart

    versions._latest.clear()
    versions._running.clear()
    (install.repo / "LOCAL").write_text("x")
    git(install.repo, "add", "-A")
    git(install.repo, "commit", "-qm", "ahead of the remote")
    assert versions.latest(install.repo, "main")["latest"] == install.head[:10]


def test_the_ui_is_built_only_when_it_changed_and_installed_only_when_its_lockfile_did(install, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "npm.log"
    (bin_dir / "npm").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\n'
        'case "$*" in *build*) mkdir -p ../src/agentop/service/webdist && touch ../src/agentop/service/webdist/index.html;; '
        "*ci*) mkdir -p node_modules;; esac\n"
    )
    (bin_dir / "npm").chmod(0o755)
    env = {"AGENTOP_BUILD_CMD": "build_ui", "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    calls = lambda: log.read_text().splitlines() if log.exists() else []  # noqa: E731

    gitignore = "node_modules/\nsrc/agentop/service/webdist/\n"
    install.publish("v2", **{".gitignore": gitignore, "ui/package-lock.json": "1", "ui/app.ts": "a"})
    assert install.run(**env).returncode == 0
    assert [c.split()[0] for c in calls()] == ["ci", "run"]
    log.unlink()
    install.publish("v3", **{"server.py": "changed"})
    assert install.run(**env).returncode == 0
    assert calls() == []
    install.publish("v4", **{"ui/app.ts": "b"})
    assert install.run(**env).returncode == 0
    assert [c.split()[0] for c in calls()] == ["run"]
