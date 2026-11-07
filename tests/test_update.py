"""deploy/update.sh, on throwaway repositories, with a stand-in for the service.

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

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "update.sh"


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
            (self.dev / name).write_text(text)
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-m", version)

    def publish(self, version, **extra):
        self.commit(version, **extra)
        git(self.dev, "push", "origin", "main")

    def run(self, **env):
        restart = f'test ! -f BAD_START || {{ rm -f {self.www}/health; exit 0; }}; cp VERSION {self.www}/health'
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
        )

    @property
    def status(self):
        return json.loads((self.data / "update-status.json").read_text())

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
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
    )
    assert result.returncode == 0
    assert install.status["state"] == "done"
