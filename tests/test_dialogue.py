"""The Dialogues plugin: its routes, its command, and what turning it off does.

The whole file is skipped when the plugin folder is gone, so the core suites
can be run without it (design 12.9).
"""

import json
import os
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("agentop.plugins.dialogue")

from agentop import paths  # noqa: E402
from tests.test_service_live import run_service  # noqa: E402

BASE = "/plugins/dialogue/dialogues"


@pytest.fixture
def service(fake_agents):
    with run_service() as live:
        yield live


def seed_dialogue(name="debate_topic_abc12345", status="completed", session_a="diaabc12a-1", session_b="diaabc12b-1"):
    directory = paths.plugins_dir() / "dialogue" / "dialogues" / name
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "id": name.rsplit("_", 1)[-1],
                "session_a": session_a,
                "session_b": session_b,
                "agent_a": "claude",
                "agent_b": "codex",
                "max_turns": 10,
                "start_time": "2026-11-01T00:00:00+00:00",
                "status": status,
                "scenario_name": "_".join(name.split("_")[:-2]),
                "brief_name": name.split("_")[-2],
            }
        )
    )
    (directory / "brief.md").write_text("The brief.")
    (directory / "progress.md").write_text("Agreed on a plan.")
    return directory


def status_of(directory):
    return json.loads((directory / "meta.json").read_text())["status"]


# ---------------------------------------------------------------------- routes


def test_dialogues_list_detail_and_remove(service):
    directory = seed_dialogue()
    listed = service.http.get(BASE).json()
    assert [d["id"] for d in listed] == [directory.name]
    assert listed[0]["agent_b"] == "codex"

    detail = service.http.get(f"{BASE}/{directory.name}").json()
    assert detail["brief"] == "The brief." and detail["progress"] == "Agreed on a plan."

    assert service.http.delete(f"{BASE}/{directory.name}").status_code == 200
    assert not directory.exists()
    assert service.http.get(BASE).json() == []


def test_stopping_a_dialogue_records_it(service):
    directory = seed_dialogue(status="running")
    assert service.http.post(f"{BASE}/{directory.name}/stop").status_code == 200
    assert service.http.get(f"{BASE}/{directory.name}").json()["status"] == "stopped"


@pytest.mark.parametrize("dialogue_id", ["abc12345", "debate", "a", "..", ".hidden", "%2e%2e", "de*"])
def test_delete_needs_the_exact_dialogue_name(service, dialogue_id):
    directory = seed_dialogue()
    assert service.http.delete(f"{BASE}/{dialogue_id}").status_code in (404, 405)
    assert directory.exists()


def test_dialogues_need_the_token(service):
    assert httpx.get(f"{service.base}{BASE}").status_code == 401


def test_the_old_core_routes_are_gone(service):
    assert service.http.get("/dialogues").status_code == 404


# ---------------------------------------------------------------------- turning it off


def test_turning_it_off_stops_running_dialogues_and_keeps_them(service):
    running = seed_dialogue("debate_topic_run00001", status="running")
    done = seed_dialogue("debate_topic_done0001", status="completed")

    assert service.http.patch("/plugins/dialogue", json={"enabled": False}).json()["state"] == "disabled"
    assert service.http.get(BASE).status_code == 404

    deadline = time.monotonic() + 5
    while status_of(running) != "stopped":
        assert time.monotonic() < deadline, "the running dialogue was not stopped"
        time.sleep(0.05)
    assert status_of(done) == "completed"

    # Saved dialogues come back with it.
    assert service.http.patch("/plugins/dialogue", json={"enabled": True}).json()["state"] == "enabled"
    assert {d["id"] for d in service.http.get(BASE).json()} == {running.name, done.name}


# ---------------------------------------------------------------------- the command and the orchestrator


def agentop(*args):
    return subprocess.run(
        [sys.executable, "-m", "agentop.cli", *args], capture_output=True, text=True, env=os.environ.copy(), timeout=30
    )


def test_the_command_lists_saved_dialogues(tmux_server):
    directory = seed_dialogue()
    result = agentop("dialogue", "list")
    assert result.returncode == 0, result.stderr
    assert directory.name in result.stdout


def test_the_command_says_when_the_plugin_is_off(tmux_server):
    flags = paths.state_dir() / "plugins.json"
    flags.parent.mkdir(parents=True, exist_ok=True)
    flags.write_text(json.dumps({"dialogue": {"enabled": False}}))
    result = agentop("dialogue", "list")
    assert result.returncode != 0
    assert "Dialogues plugin is disabled" in result.stderr


def test_the_orchestrator_process_loads_without_the_service(tmux_server):
    result = subprocess.run(
        [sys.executable, "-m", "agentop.plugins.dialogue.runner", "no-such-dialogue"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "not found" in result.stderr
