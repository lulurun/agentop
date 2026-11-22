"""Waiting for input: a session is marked when its screen goes quiet after changing, and only then."""

import shutil
import time

import pytest
from fastapi.testclient import TestClient

from agentop import tmux
from agentop.plugins import attention
from agentop.plugins.attention import Watcher
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from tests.conftest import signed_in
from tests.test_plugins import AUTH, TOKEN, QuietStore


class FakeTmux:
    """Screens and panes set by the test. `activity` is the second of the last output, as tmux keeps it."""

    def __init__(self):
        self.screens = {"t1": "$ "}
        self.activity = {"t1": 0}
        self.size = {"t1": "80x24"}
        self.captures = 0

    def print(self, name, text, at):
        self.screens[name] = text
        self.activity[name] = int(at)

    def list_panes(self):
        return {n: (self.activity[n], self.size[n], n) for n in self.screens}

    def capture(self, name):
        self.captures += 1
        return self.screens[name]


ROWS = [{"id": "s1", "status": "running", "tmux": {"session": "t1", "clients": 0}}]


@pytest.fixture
def fake():
    return FakeTmux()


@pytest.fixture
def watcher(fake):
    return Watcher(fake.list_panes, fake.capture, quiet_seconds=4)


def test_a_screen_that_goes_quiet_after_changing_is_waiting(watcher, fake):
    assert not watcher.tick(ROWS, now=100)  # baseline
    fake.print("t1", "working 1", at=101)
    assert not watcher.tick(ROWS, now=102)
    fake.print("t1", "working 2", at=103)
    assert not watcher.tick(ROWS, now=104)
    assert not watcher.tick(ROWS, now=106)
    assert watcher.tick(ROWS, now=108)
    assert watcher.waiting() == {"s1": 108}


def test_a_screen_idle_from_the_start_is_never_waiting(watcher, fake):
    for now in range(100, 120, 2):
        assert not watcher.tick(ROWS, now=now)
    assert watcher.waiting() == {}


def test_any_change_clears_waiting(watcher, fake):
    watcher.tick(ROWS, now=100)
    fake.print("t1", "done", at=101)
    watcher.tick(ROWS, now=102)
    watcher.tick(ROWS, now=106)
    assert watcher.waiting() == {"s1": 106}
    fake.print("t1", "done\n> y", at=107)
    assert watcher.tick(ROWS, now=108)
    assert watcher.waiting() == {}


def test_a_resize_redraw_is_not_work(watcher, fake):
    watcher.tick(ROWS, now=100)
    fake.size["t1"] = "120x40"
    fake.print("t1", "redrawn wider", at=101)
    watcher.tick(ROWS, now=102)
    fake.print("t1", "redrawn wider, late", at=103)
    watcher.tick(ROWS, now=104)
    for now in (106, 108, 110):
        assert not watcher.tick(ROWS, now=now)
    assert watcher.waiting() == {}


def test_quiet_panes_are_not_read_again(watcher, fake):
    fake.activity["t1"] = 100
    watcher.tick(ROWS, now=100.5)
    watcher.tick(ROWS, now=102)  # output may have come later in second 100: one more look
    reads = fake.captures
    for now in range(104, 120, 2):
        watcher.tick(ROWS, now=now)
    assert fake.captures == reads
    assert watcher.stats["captured"] == 0


def test_output_in_the_same_second_as_a_capture_is_not_missed(watcher, fake):
    watcher.tick(ROWS, now=100.2)
    fake.print("t1", "Allow this command? (y/n)", at=100.7)
    watcher.tick(ROWS, now=102)
    watcher.tick(ROWS, now=106)
    assert "s1" in watcher.waiting()


def test_sessions_that_end_are_forgotten(watcher, fake):
    watcher.tick(ROWS, now=100)
    fake.print("t1", "x", at=101)
    watcher.tick(ROWS, now=102)
    watcher.tick(ROWS, now=106)
    assert watcher.tick([{**ROWS[0], "status": "exited"}], now=108)
    assert watcher.screens == {}


def test_tmux_failing_changes_nothing(fake):
    watcher = Watcher(lambda: None, fake.capture)
    assert not watcher.tick(ROWS, now=100)
    assert watcher.screens == {}


def test_an_overlapping_tick_is_skipped(watcher):
    watcher._lock.acquire()
    try:
        assert not watcher.tick(ROWS, now=100)
    finally:
        watcher._lock.release()
    assert watcher.stats["skipped"] == 1


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "attention", root / "attention")
    store = QuietStore()
    host = PluginHost(store, roots=[root], data_dir=tmp_path / "home")
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
    with TestClient(app) as test_client:
        yield test_client


def test_the_plugin_reports_its_state_and_cost(client):
    state = client.get("/api/v2/plugins/attention/state", headers=AUTH).json()
    assert state["waiting"] == {}
    assert state["quiet_seconds"] == attention.QUIET_SECONDS
    assert set(state["stats"]) == {"at", "sessions", "captured", "ms", "skipped"}


def test_turning_it_off_stops_it(client):
    assert client.patch("/api/v2/plugins/attention", json={"enabled": False}, headers=AUTH).json()["state"] == "disabled"
    assert client.get("/api/v2/plugins/attention/state", headers=AUTH).status_code == 404


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_watches_the_first_pane_of_the_first_window(tmux_server):
    name = "attn-test"
    tmux.run("new-session", "-d", "-s", name, "-x", "80", "-y", "24", "printf agent; sleep 30")
    # Another window and a split, both made active, are not what is watched.
    tmux.run("split-window", "-t", f"={name}:", "printf split; sleep 30")
    tmux.run("new-window", "-t", f"={name}:", "printf other; sleep 30")
    deadline = time.time() + 3
    while time.time() < deadline:
        panes = attention.list_panes() or {}
        if name in panes and "agent" in attention.capture(panes[name][2]):
            break
        time.sleep(0.05)
    activity, _size, pane_id = panes[name]
    assert activity > time.time() - 10
    assert "agent" in attention.capture(pane_id)
    assert "agent" not in tmux.CapturePane.screen(name)  # the active pane is the new window
