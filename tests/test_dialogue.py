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

from websockets.sync.client import connect  # noqa: E402

from agentop import paths  # noqa: E402
from agentop.tmux import CapturePane, Session  # noqa: E402
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


# ---------------------------------------------------------------------- the New dialogue wizard


def test_scenarios_list_their_brief_fields(service):
    scenarios = {s["id"]: s for s in service.http.get("/plugins/dialogue/scenarios").json()}
    main = scenarios["implementation-requirements"]
    assert main["fields"][0] == "title" and "acceptance_criteria" in main["fields"]
    assert main["name_a"] == "ProductOwner" and not main["shelf"]
    # A scenario without a description asks for the whole brief in one field.
    assert scenarios["shelf/pm-sde"]["fields"] == ["title", "brief_text"]
    # The dialogue fills these in itself, so they are never asked for.
    assert not any(set(s["fields"]) & {"name_a", "name_b", "progress_file", "brief", "topic"} for s in scenarios.values())


def test_placeholders_and_fill():
    from agentop.plugins.dialogue import scenarios

    text = "# {title}\n{goal} for {name_a}, {goal} again. {not-a-field}"
    assert scenarios.placeholders(text) == ["title", "goal"]
    # A value is never read as a placeholder itself.
    filled = scenarios.fill(text, {"title": "T", "goal": "{title}"}, {"name_a": "PM"})
    assert filled == "# T\n{title} for PM, {title} again. {not-a-field}"


@pytest.mark.parametrize(
    "body, message",
    [
        ({"scenario": "no-such"}, "No such scenario"),
        ({"scenario": "../implementation-requirements"}, "No such scenario"),
        ({"scenario": "shelf/pm-sde", "values": {"title": "T"}}, "brief_text"),
        ({"scenario": "shelf/pm-sde", "values": {"title": "T", "brief_text": "  "}}, "brief_text"),
        ({"scenario": "shelf/pm-sde", "values": {"title": "T", "brief_text": "B"}, "agent_a": "nope"}, "Unknown tool"),
    ],
)
def test_a_new_dialogue_needs_every_field(service, body, message):
    response = service.http.post(BASE, json={"agent_a": "fakeagent", "agent_b": "fakeagent", **body})
    assert response.status_code == 400 and message in response.json()["detail"]
    assert service.http.get(BASE).json() == []


def test_a_new_dialogue_saves_its_brief_and_starts_hidden_read_only_agents(service, fake_agents, monkeypatch):
    monkeypatch.setattr(type(fake_agents["fakeagent"]), "boot_seconds", 0.1)
    body = {
        "scenario": "shelf/pm-sde",
        "values": {"title": "Ship the Widget!", "brief_text": "Build {it} well."},
        "agent_a": "fakeagent",
        "agent_b": "fakeagent",
        "max_turns": 7,
    }
    response = service.http.post(BASE, json=body)
    assert response.status_code == 202, response.text
    dialogue_id = response.json()["id"]
    assert dialogue_id.startswith("pm-sde_ship-the-widget_")

    folder = paths.plugins_dir() / "dialogue" / "dialogues" / dialogue_id
    assert (folder / "brief.md").read_text() == "# Ship the Widget!\n\nBuild {it} well.\n"
    assert (folder / "scenario.toml").is_file()

    deadline = time.monotonic() + 15
    while True:
        detail = service.http.get(f"{BASE}/{dialogue_id}").json()
        if detail["session_a_alive"] and detail["session_b_alive"]:
            break
        assert time.monotonic() < deadline, detail
        time.sleep(0.1)
    assert detail["title"] == "Ship the Widget!" and detail["max_turns"] == 7

    # Both agents are the plugin's: listed with their owner, and their terminal can't be typed into.
    session = service.wait_for(detail["session_a"], "running")
    assert session["owner"] == "dialogue"
    from tests.test_service_live import read_until

    # Watching never resizes the window: the orchestrator reads the agents' replies off it.
    with service.terminal(detail["session_a"], cols=40, rows=10) as ws:
        assert json.loads(ws.recv(timeout=5)) == {"type": "size", "cols": 80, "rows": 40}
        read_until(ws, "fake agent ready")
        ws.send(b"typed-by-a-viewer\r")
        ws.send(json.dumps({"type": "resize", "cols": 30, "rows": 8}))
        time.sleep(0.5)
        assert Session.window_size(detail["session_a"]) == (80, 40)
    assert "typed-by-a-viewer" not in CapturePane.scrollback(detail["session_a"])

    assert service.http.delete(f"{BASE}/{dialogue_id}").status_code == 200
    assert not folder.exists()


def test_an_ordinary_terminal_can_be_opened_read_only(service):
    from tests.test_service_live import read_until

    created = service.create()
    service.wait_for(created["id"], "running")
    assert service.http.get(f"/sessions/{created['id']}").json()["owner"] is None
    ticket = service.ticket("terminal", created["id"])
    url = f"ws://127.0.0.1:{service.port}/api/v2/ws/sessions/{created['id']}/terminal?ticket={ticket}&readonly=true"
    with connect(url) as ws:
        read_until(ws, "fake agent ready")
        ws.send(b"not-sent\r")
        time.sleep(0.5)
    assert "not-sent" not in CapturePane.scrollback(created["id"])


# ---------------------------------------------------------------------- transcripts, files and stats


def test_turns_files_and_stats(service):
    directory = seed_dialogue()
    meta = json.loads((directory / "meta.json").read_text())
    meta["end_time"] = "2026-11-01T00:30:00+00:00"
    (directory / "meta.json").write_text(json.dumps(meta))
    (directory / "scenario.toml").write_text(
        'name = "debate"\n[role_a]\nname = "Pro"\nprompt = "x"\n[role_b]\nname = "Con"\nprompt = "y"\n'
    )
    turns = [
        {"turn": 1, "side": "a", "name": "Pro", "status": "continue", "body": "one two three", "at": "2026-11-01T00:01:00+00:00"},
        {"turn": 2, "side": "b", "name": "Con", "status": "continue", "body": "four", "at": "2026-11-01T00:02:00+00:00"},
        {"turn": 3, "side": "a", "name": "Pro", "status": "complete", "body": "done", "at": "2026-11-01T00:03:00+00:00"},
    ]
    (directory / "turns.jsonl").write_text("".join(json.dumps(t) + "\n" for t in turns) + "not json\n")
    (directory / "deliverables").mkdir()
    (directory / "deliverables" / "plan.md").write_text("# Plan")
    (directory / "secret.txt").write_text("not listed")
    (directory / "deliverables" / "escape.md").symlink_to(directory / "secret.txt")

    assert service.http.get(f"{BASE}/{directory.name}/turns").json() == turns

    detail = service.http.get(f"{BASE}/{directory.name}").json()
    assert (detail["name_a"], detail["name_b"], detail["running"]) == ("Pro", "Con", False)
    assert detail["stats"] == {
        "turns": 3,
        "turns_a": 2,
        "turns_b": 1,
        "words_a": 4,
        "words_b": 1,
        "retries": 0,
        "duration_seconds": 1800.0,
        "completed_by": "Pro",
    }

    files = [f["path"] for f in service.http.get(f"{BASE}/{directory.name}/files").json()]
    assert files == ["brief.md", "progress.md", "deliverables/plan.md"]
    shown = service.http.get(f"{BASE}/{directory.name}/file", params={"path": "deliverables/plan.md"}).json()
    assert shown["text"] == "# Plan" and not shown["binary"]
    for path in ("secret.txt", "meta.json", "deliverables/escape.md", "../" + directory.name + "/secret.txt", "/etc/passwd"):
        assert service.http.get(f"{BASE}/{directory.name}/file", params={"path": path}).status_code == 404, path


def test_turns_of_an_older_dialogue_come_from_its_log(service):
    directory = seed_dialogue()
    (directory / "scenario.toml").write_text(
        'name = "debate"\n[role_a]\nname = "Pro"\nprompt = "x"\n[role_b]\nname = "Con"\nprompt = "y"\n'
    )
    (directory / "dialogue.log").write_text(
        "[2026-11-01T00:01:00] agentop.plugins.dialogue.parser [Pro] turn:1 status:continue body_len:9\n"
        "[2026-11-01T00:01:00] agentop.plugins.dialogue.parser [Pro] turn:1 body:\n"
        "first\n[not a log line]\n"
        "[2026-11-01T00:02:00] agentop.plugins.dialogue.parser [Con] turn:2 status:complete body_len:3\n"
        "[2026-11-01T00:02:00] agentop.plugins.dialogue.parser [Con] turn:2 body:\n"
        "end\n"
    )
    turns = service.http.get(f"{BASE}/{directory.name}/turns").json()
    assert [(t["turn"], t["side"], t["status"], t["body"]) for t in turns] == [
        (1, "a", "continue", "first\n[not a log line]"),
        (2, "b", "complete", "end"),
    ]
