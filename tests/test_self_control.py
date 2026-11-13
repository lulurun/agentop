"""A service started by hand with `agentop serve` restarts, stops, and updates itself.

Each test runs a real `agentop serve` in its own process, with its own data
directory and tmux server. The update tests swap in a stand-in script, so
nothing here touches the real install.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ByHand:
    def __init__(self, tmp_path: Path, **env: str):
        self.port = free_port()
        self.home = tmp_path / "state"
        # Its own home too: serve installs skills under ~/.claude as it starts.
        (tmp_path / "home").mkdir(exist_ok=True)
        self.env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "AGENTOP_HOME": str(self.home),
            "AGENTOP_TMUX_SOCKET": f"agentop-self-{os.getpid()}-{self.port}",
            "PYTHONPATH": str(ROOT / "src"),
            **env,
        }
        self.process = subprocess.Popen(
            [sys.executable, "-m", "agentop.cli", "serve", "--port", str(self.port), "--name", "by-hand"],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.base = f"http://127.0.0.1:{self.port}/api/v2"
        self.wait_up()
        token = (self.home / "service.json").read_text()
        import json

        self.http = httpx.Client(
            base_url=self.base, headers={"Authorization": f"Bearer {json.loads(token)['token']}"}, timeout=10
        )

    def answers(self) -> bool:
        try:
            return httpx.get(f"{self.base}/health", timeout=1).status_code == 200
        except httpx.HTTPError:
            return False

    def wait_up(self, timeout: float = 20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.answers():
                return
            time.sleep(0.1)
        raise AssertionError("the service did not come up")

    def wait_down(self, timeout: float = 10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.answers():
                return
            time.sleep(0.02)
        raise AssertionError("the service did not go down")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(10)
        subprocess.run(["tmux", "-L", self.env["AGENTOP_TMUX_SOCKET"], "kill-server"], capture_output=True, check=False)


@pytest.fixture
def by_hand(tmp_path):
    services = []

    def start(**env):
        services.append(ByHand(tmp_path, **env))
        return services[-1]

    yield start
    for service in services:
        service.close()


def test_it_can_restart_stop_and_update_itself(by_hand):
    s = by_hand()
    info = s.http.get("/service").json()
    assert info["manager"] == "self"
    assert info["capabilities"] == {"stop": True, "restart": True, "update": True}


def test_restart_keeps_the_process_and_runs_the_code_again(by_hand):
    s = by_hand()
    time.sleep(1.2)
    before = s.http.get("/service").json()
    assert before["uptime_seconds"] >= 1
    assert s.http.post("/admin/restart").status_code == 202
    s.wait_down()
    s.wait_up()
    after = s.http.get("/service").json()
    assert after["pid"] == before["pid"] == s.process.pid
    assert after["uptime_seconds"] < before["uptime_seconds"]
    assert s.process.poll() is None


def test_stop_ends_the_process(by_hand):
    s = by_hand()
    assert s.http.post("/admin/stop").status_code == 202
    assert s.process.wait(15) == 0


def test_the_cli_restarts_and_stops_it_through_the_service(by_hand):
    s = by_hand()
    (s.home / "config.toml").write_text(f"port = {s.port}\n")

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "agentop.cli", "service", *args], env=s.env, capture_output=True, text=True, timeout=30, check=False
        )

    status = cli("status")
    assert "by-hand: running" in status.stdout and "Started with agentop serve." in status.stdout
    assert cli("start").returncode != 0
    before = s.http.get("/service").json()["started_at"]
    assert cli("restart").returncode == 0
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if s.http.get("/service").json()["started_at"] != before:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    assert s.http.get("/service").json()["started_at"] != before
    assert cli("stop").returncode == 0
    assert s.process.wait(15) == 0
    assert "Nothing is answering" in cli("status").stdout


def stand_in(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "update.sh"
    script.write_text(f'#!/bin/bash\nHEALTH_URL="$AGENTOP_HEALTH_URL"\n{body}\n')
    return script


def test_an_update_restarts_it_and_ends(by_hand, tmp_path):
    marker = tmp_path / "restarted"
    script = stand_in(tmp_path, f'eval "$AGENTOP_RESTART_CMD" && touch {marker}')
    s = by_hand(AGENTOP_UPDATE_SCRIPT=str(script))
    time.sleep(1.2)
    before = s.http.get("/service").json()["uptime_seconds"]
    assert s.http.post("/admin/update").status_code == 202
    deadline = time.monotonic() + 20
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.exists()
    s.wait_up()
    assert s.http.get("/service").json()["uptime_seconds"] < before
    # The updater was this process's child before the restart. It is reaped, not left running.
    deadline = time.monotonic() + 10
    while s.http.get("/admin/update").json()["running"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert s.http.get("/admin/update").json()["running"] is False


def test_an_update_is_running_until_its_script_ends(by_hand, tmp_path):
    release = tmp_path / "release"
    script = stand_in(tmp_path, f"while [ ! -f {release} ]; do sleep 0.1; done")
    s = by_hand(AGENTOP_UPDATE_SCRIPT=str(script))
    assert s.http.post("/admin/update").status_code == 202
    assert s.http.get("/admin/update").json()["running"] is True
    second = s.http.post("/admin/update")
    assert second.status_code == 409 and "already running" in second.json()["detail"]
    release.touch()
    deadline = time.monotonic() + 10
    while s.http.get("/admin/update").json()["running"] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert s.http.get("/admin/update").json()["running"] is False


def test_if_the_new_version_died_the_updater_starts_it_again(by_hand, tmp_path):
    saved = tmp_path / "restart-command"
    script = stand_in(tmp_path, f'printf %s "$AGENTOP_RESTART_CMD" > {saved}')
    s = by_hand(AGENTOP_UPDATE_SCRIPT=str(script))
    assert s.http.post("/admin/update").status_code == 202
    deadline = time.monotonic() + 10
    while not saved.exists() and time.monotonic() < deadline:
        time.sleep(0.1)

    s.process.send_signal(signal.SIGKILL)
    s.process.wait(10)
    subprocess.run(
        ["bash", "-c", saved.read_text()],
        env={**s.env, "HEALTH_URL": f"{s.base}/health"},
        check=True,
        timeout=15,
    )
    s.wait_up()
    pid = s.http.get("/service").json()["pid"]
    assert pid != s.process.pid
    os.kill(pid, signal.SIGTERM)
    s.wait_down()
    assert (s.home / "logs" / "serve.log").exists()
