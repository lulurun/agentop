"""Which conversation a running codex works on, and which codex processes are agents."""

from __future__ import annotations

import os
import sqlite3
import time

import psutil
import pytest

from agentop.agentclis.codex import CodexAgentCli

THREAD_A = "01a18bcf-f7bc-7af3-91b5-74bcf3f51fca"
THREAD_B = "01a19215-cdb4-7361-9271-43cddf1afd12"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex = tmp_path / ".codex"
    codex.mkdir()
    with sqlite3.connect(codex / "state_5.sqlite") as db:
        db.execute(
            "CREATE TABLE threads (id TEXT, title TEXT, first_user_message TEXT, tokens_used INT,"
            " rollout_path TEXT, created_at_ms INT, cwd TEXT, archived INT)"
        )
        # Created long before this process started, so the start-time match finds neither.
        for thread_id, title in ((THREAD_A, "Resumed talk"), (THREAD_B, "After /new")):
            db.execute("INSERT INTO threads VALUES (?, ?, '', 0, NULL, 1000, '/w', 0)", (thread_id, title))
    with sqlite3.connect(codex / "logs_2.sqlite") as db:
        db.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INT, thread_id TEXT, process_uuid TEXT)")
    return codex


def log(home, pid: int, thread_id: str | None, ts: float | None = None):
    with sqlite3.connect(home / "logs_2.sqlite") as db:
        db.execute(
            "INSERT INTO logs (ts, thread_id, process_uuid) VALUES (?, ?, ?)",
            (int(ts or time.time()), thread_id, f"pid:{pid}:0e993edd"),
        )


def title(agent: CodexAgentCli) -> str | None:
    return agent.get_session_meta(os.getpid(), "/w").get("ai_title")


def test_no_record_means_no_title(home):
    assert title(CodexAgentCli()) is None


def test_the_log_names_the_thread_and_follows_new(home):
    agent = CodexAgentCli()
    log(home, os.getpid(), THREAD_A)
    assert title(agent) == "Resumed talk"
    log(home, os.getpid(), None)
    log(home, os.getpid(), THREAD_B)
    assert title(agent) == "After /new"


def test_log_lines_from_an_earlier_process_with_the_same_pid_are_ignored(home):
    started = psutil.Process(os.getpid()).create_time()
    log(home, os.getpid(), THREAD_A, ts=started - 3600)
    assert title(CodexAgentCli()) is None


def test_another_pid_with_the_same_prefix_is_not_mixed_up(home):
    log(home, int(f"{os.getpid()}1"), THREAD_A)
    assert title(CodexAgentCli()) is None


def test_a_thread_is_remembered_after_codex_trims_its_log(home):
    agent = CodexAgentCli()
    log(home, os.getpid(), THREAD_A)
    assert title(agent) == "Resumed talk"
    with sqlite3.connect(home / "logs_2.sqlite") as db:
        db.execute("DELETE FROM logs")
    assert title(agent) == "Resumed talk"


def test_resume_on_the_command_line_names_the_thread(home, monkeypatch):
    agent = CodexAgentCli()
    monkeypatch.setattr(psutil.Process, "cmdline", lambda self: ["codex", "resume", THREAD_A])
    assert title(agent) == "Resumed talk"


@pytest.mark.parametrize(
    ("name", "cmdline", "agent"),
    [
        ("codex", "codex --dangerously-bypass-hook-trust", True),
        ("codex", f"codex resume {THREAD_A}", True),
        ("codex", "codex -c features.code_mode_host=true app-server --listen unix://", False),
        ("codex", "/home/u/.codex/packages/standalone/releases/0.153.4/bin/codex app-server daemon pid-update-loop", False),
        ("codex-code-mode-host", "/home/u/.codex/packages/standalone/releases/0.165.0/bin/codex-code-mode-host", False),
    ],
)
def test_codex_background_processes_are_not_agents(name, cmdline, agent):
    assert CodexAgentCli().matches(name, cmdline) is agent
