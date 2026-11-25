"""Projects and their Coordinator (docs/0_13_projects.md)."""

import time

import pytest

from agentop import coordinator, ops, projects, registry
from agentop.coordinator_skill import PATH as COORDINATOR_SKILL_SOURCE
from agentop.tmux import CapturePane, Session
from tests.test_service_live import run_service


@pytest.fixture
def quick_boot(fake_agents):
    for agent in fake_agents.values():
        agent.boot_seconds = 0.2


def wait_for_screen(name, text, timeout=5.0):
    deadline = time.monotonic() + timeout
    while text not in CapturePane.scrollback(name):
        assert time.monotonic() < deadline, CapturePane.scrollback(name)
        time.sleep(0.1)


def test_the_coordinator_is_codex_then_claude_then_any():
    assert coordinator.pick_tool(["antigravity", "claude", "codex"]) == "codex"
    assert coordinator.pick_tool(["antigravity", "claude"]) == "claude"
    assert coordinator.pick_tool(["antigravity"]) == "antigravity"
    assert coordinator.pick_tool([]) is None


def test_the_project_skill_is_only_for_projects_plugin_coordinators():
    phrase = "You own the project's objective. Your job is pursuit"
    project = {"name": "alpha", "folder": "/tmp/alpha", "owner": "projects"}
    dialogue = {"name": "debate", "folder": "/tmp/debate", "owner": "dialogue"}

    assert phrase not in coordinator.instructions(project)
    assert "COORDINATOR_SKILL.md" in coordinator.instructions(project)
    assert "COORDINATOR_SKILL.md" not in coordinator.instructions(dialogue)
    assert "## Coordinator rules" in coordinator.instructions(project)
    assert "## Coordinator rules" not in coordinator.instructions(dialogue)
    assert "agentop coordinator task start --action" in coordinator.instructions(project)
    assert "agentop coordinator" not in coordinator.instructions(dialogue)
    prompt = coordinator.welcome_prompt(project)
    normalized = " ".join(prompt.split())
    assert phrase in normalized
    for command in ("agentop project show", "agentop coordinator task start", "agentop coordinator task accept",
                    "agentop coordinator yield", "agentop coordinator review", "agentop coordinator status"):
        assert command in normalized
    assert "agentop coordinator yield owner --question" in normalized.split("<project-coordinator-skill>")[0]
    assert "Your context is cleared at the next wake" in normalized
    assert phrase in coordinator.resume_prompt(project)
    assert "agentop coordinator status" in coordinator.resume_prompt(project)
    assert "STATE.md" not in coordinator.resume_prompt(project).split("<project-coordinator-skill>")[0]
    assert phrase not in coordinator.welcome_prompt(dialogue)
    assert "agentop coordinator yield" not in coordinator.welcome_prompt(dialogue)
    assert coordinator.resume_prompt(dialogue) == ""
    assert phrase not in coordinator.member_header(project, "dev")


def test_names_and_the_default_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    assert projects.is_valid_name("my-app_2") and not projects.is_valid_name("My App") and not projects.is_valid_name("")
    assert projects.ensure_default()["owner"] == projects.CORE
    projects.create("alpha", "projects", welcome="plan things")
    assert (projects.folder("alpha") / "code").is_dir()
    with pytest.raises(projects.ProjectError):
        projects.create("alpha", "projects")
    assert [p["name"] for p in projects.list_projects("projects")] == ["alpha"]
    with pytest.raises(projects.ProjectError):
        projects.close(projects.DEFAULT)
    assert not projects.kept("alpha")
    projects.close("alpha")
    assert projects.get("alpha") is None and projects.folder("alpha").is_dir() and projects.kept("alpha")
    assert not hasattr(projects, "remove")


def test_the_instructions_are_written_once(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    found = projects.create("alpha", "projects", instructions="Run the alpha project.")
    coordinator.write_instructions(found)
    agents = projects.folder("alpha") / "AGENTS.md"
    assert "Coordinator of alpha" in agents.read_text() and "Run the alpha project." in agents.read_text()
    assert f"{projects.folder('alpha')}/code/<source-tree>" in agents.read_text()
    assert "You own the project's objective" not in agents.read_text()
    assert (projects.folder("alpha") / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (projects.folder("alpha") / coordinator.COORDINATOR_SKILL_FILE).read_text() == COORDINATOR_SKILL_SOURCE.read_text()
    assert not (projects.folder("alpha") / "STATE.md").exists()
    assert "# Direction 1" in (projects.folder("alpha") / "track/1/DIRECTION.md").read_text()
    assert "## M1 " in (projects.folder("alpha") / "track/1/PLAN.md").read_text()
    # The owner's text stays; only agentop's rules block is added, then kept up to date.
    agents.write_text("the owner's own")
    coordinator.write_instructions(found)
    text = agents.read_text()
    assert text.startswith("the owner's own") and text.count(coordinator.RULES_BEGIN) == 1
    agents.write_text(text.replace("## Coordinator rules", "## Old rules"))
    coordinator.write_instructions(found)
    assert agents.read_text() == text
    dialogue = projects.create("talk", "dialogue")
    coordinator.write_instructions(dialogue)
    (projects.folder("talk") / "AGENTS.md").write_text("the dialogue's own")
    coordinator.write_instructions(dialogue)
    assert (projects.folder("talk") / "AGENTS.md").read_text() == "the dialogue's own"


def test_a_session_inside_the_project_needs_no_extra_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    projects.create("alpha", "projects")
    repo = projects.folder("alpha") / "code" / "app"
    repo.mkdir()

    assert ops._launch_params({}, str(projects.folder("alpha")), "alpha") == {}
    assert ops._launch_params({}, str(repo), "alpha") == {}
    assert ops._launch_params({}, str(tmp_path / "outside"), "alpha") == {
        "add_dir": str(projects.folder("alpha"))
    }


def test_only_default_sessions_get_remote_control(tmp_path, monkeypatch):
    from agentop.agentclis.claude import ClaudeAgentCli

    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    projects.create("alpha", "projects")
    assert ops._launch_params({}, str(tmp_path / "repo"), projects.DEFAULT) == {"remote_control": True}
    assert "remote_control" not in ops._launch_params({}, str(tmp_path / "repo"), "alpha")
    claude = ClaudeAgentCli()
    assert "--remote-control" not in claude.launch_cmd and "--remote-control" not in claude.get_resume_cmd("S")
    assert claude._build_cmd(claude.launch_cmd, {"remote_control": True}).endswith(" --remote-control")
    assert "--remote-control" not in claude._build_cmd(claude.launch_cmd, {"add_dir": "/x"})


def test_a_default_session_gets_no_project_header(fake_agents, quick_boot, tmp_path):
    name = ops.start("fakeagent", str(tmp_path), "dev").name
    ops.first_prompt(name, "build it")
    wait_for_screen(name, "got: build it")
    assert "[agentop project" not in CapturePane.scrollback(name)


def test_a_session_outside_the_folder_is_told_about_its_project(fake_agents, quick_boot, tmp_path):
    projects.create("alpha", "projects")
    name = ops.start("fakeagent", str(tmp_path), "dev", project="alpha").name
    ops.first_prompt(name, "build it")
    wait_for_screen(name, "[agentop project alpha] You are `dev`")
    wait_for_screen(name, "got: build it")
    assert "The Coordinator owns pursuit" not in CapturePane.scrollback(name)
    assert registry.load()["sessions"][name]["project"] == "alpha"


def test_the_coordinator_starts_once_with_its_welcome_and_is_resumed_after(fake_agents, quick_boot):
    # Not the projects plugin's: its skill makes the welcome too long to wait for here.
    projects.create("alpha", "someplugin")
    with run_service() as service:
        assert service.http.post("/projects/default/coordinator").status_code == 400
        first = service.http.post("/projects/alpha/coordinator")
        assert first.status_code == 202, first.text
        row = first.json()
        assert (row["display_name"], row["project"]) == ("Coordinator", "alpha")
        assert row["cwd"] == str(projects.folder("alpha"))
        service.wait_for(row["id"], "running")
        wait_for_screen(row["id"], "got: You have just been started as a Coordinator")
        assert (projects.folder("alpha") / "AGENTS.md").is_file()
        # Already running: the same one.
        assert service.http.post("/projects/alpha/coordinator").json()["id"] == row["id"]
        assert service.http.post("/projects/nope/coordinator").status_code == 404

        registry.upsert_session(row["id"], {"agent_session_id": "COORD-1"})
        assert service.http.post(f"/sessions/{row['id']}/stop").status_code == 200
        assert not Session.has(row["id"])
        deadline = time.monotonic() + 5
        while service.http.get(f"/sessions/{row['id']}").status_code == 200:
            assert time.monotonic() < deadline
            time.sleep(0.1)

        again = service.http.post("/projects/alpha/coordinator").json()
        assert again["id"] != row["id"] and again["display_name"] == "Coordinator"
        assert registry.load()["sessions"][again["id"]]["agent_session_id"] == "COORD-1"


def test_the_projects_plugin_makes_closes_and_loads_again_its_own(fake_agents, quick_boot):
    with run_service() as service:
        base = "/plugins/projects/projects"
        assert service.http.post(base, json={"name": "Bad Name"}).status_code == 400
        made = service.http.post(base, json={"name": "alpha"})
        assert made.status_code == 201, made.text
        assert [p["name"] for p in service.http.get(base).json()] == ["alpha"]
        coordinator_row = next(
            r for r in service.http.get("/sessions").json() if r["project"] == "alpha" and r["display_name"] == "Coordinator"
        )
        service.wait_for(coordinator_row["id"], "running")
        wait_for_screen(coordinator_row["id"], "Your job is pursuit")
        assert "Run the alpha" not in (projects.folder("alpha") / "AGENTS.md").read_text()
        assert "You run this project" in (projects.folder("alpha") / "AGENTS.md").read_text()
        skill = projects.folder("alpha") / coordinator.COORDINATOR_SKILL_FILE
        assert skill.is_file()
        instructions = (projects.folder("alpha") / "AGENTS.md").read_text()
        assert "git status --porcelain=v1 --untracked-files=all" not in instructions
        assert "git status --porcelain=v1 --untracked-files=all" in skill.read_text()
        assert service.http.post(f"{base}/alpha/coordinator").json()["id"] == coordinator_row["id"]

        registry.upsert_session(coordinator_row["id"], {"agent_session_id": "ALPHA-COORD-1"})
        assert service.http.post(f"/sessions/{coordinator_row['id']}/stop").status_code == 200
        deadline = time.monotonic() + 5
        while service.http.get(f"/sessions/{coordinator_row['id']}").status_code == 200:
            assert time.monotonic() < deadline
            time.sleep(0.1)
        for route in (f"{base}/alpha/coordinator", "/projects/alpha/coordinator"):
            refused = service.http.post(route)
            assert refused.status_code == 409, refused.text
        for _ in range(3):
            assert service.http.get(base).json()[0]["name"] == "alpha"
            assert service.http.get("/projects/alpha").status_code == 200
            assert not any(r["project"] == "alpha" for r in service.http.get("/sessions").json())
        assert service.http.post(f"{base}/alpha/close").status_code in (404, 405)
        assert service.http.post(f"{base}/default/close").status_code in (404, 405)
        assert service.http.post(f"{base}/nope/coordinator").status_code == 404
        assert service.http.delete(f"{base}/alpha").status_code in (404, 405)
        assert projects.get("alpha") is not None

    # Service startup and its scans preserve a project without a Coordinator.
    with run_service() as service:
        assert service.http.get(base).json()[0]["name"] == "alpha"
        assert not any(r["project"] == "alpha" for r in service.http.get("/sessions").json())
        assert service.http.post("/projects/alpha/coordinator").status_code == 409

        # A closed project's kept folder, created again, is loaded as it is and its Coordinator resumes.
        (projects.folder("alpha") / "track/1/CLOSED.md").write_text("# Closed\n\n- 2026-11-25 10:00 closed by `Coordinator` of alpha\n")
        projects.close("alpha")
        assert service.http.post("/projects/alpha/coordinator").status_code == 404
        assert service.http.get(base).json() == []
        again = service.http.post(base, json={"name": "alpha"})
        assert again.status_code == 201, again.text
        row = next(r for r in service.http.get("/sessions").json()
                   if r["project"] == "alpha" and r["display_name"] == "Coordinator" and r["id"] != coordinator_row["id"])
        service.wait_for(row["id"], "running")
        wait_for_screen(row["id"], "You are resuming this project's Coordinator session")
        assert registry.load()["sessions"][row["id"]]["agent_session_id"] == "ALPHA-COORD-1"
        assert (projects.folder("alpha") / "track/1/CLOSED.md").read_text().splitlines()[-1].endswith(
            "reopened by the owner, in the app")


@pytest.mark.parametrize("status", [None, "exited", "failed"])
def test_stale_coordinator_requests_cannot_start_or_resume_projects(tmp_path, monkeypatch, status):
    import asyncio

    from agentop.service.store import SessionStore, StoreError

    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    found = projects.create("alpha", "projects")
    store = SessionStore()
    if status:
        store._sessions["coord-1"] = {"id": "coord-1", "project": "alpha", "display_name": "Coordinator", "status": status}

    def unexpected(*args, **kwargs):
        pytest.fail("a stale request must not write instructions, close, create or resume")

    monkeypatch.setattr(coordinator, "write_instructions", unexpected)
    monkeypatch.setattr(store, "create", unexpected)
    monkeypatch.setattr(store, "close", unexpected)

    async def check():
        for _ in range(2):
            with pytest.raises(StoreError) as refused:
                await store.ensure_coordinator("alpha", ["codex"])
            assert refused.value.status == 409
        # The project closes while a request is reading its metadata.
        def closing(name):
            (projects.meta_dir(name) / "project.json").unlink()
            return found

        monkeypatch.setattr(projects, "get", closing)
        with pytest.raises(StoreError) as refused:
            await store.ensure_coordinator("alpha", ["codex"])
        assert refused.value.status == 409

    asyncio.run(check())
    assert not (projects.folder("alpha") / "AGENTS.md").exists()
