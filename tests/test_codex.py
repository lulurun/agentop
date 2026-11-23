"""Which conversation a running codex works on, and which codex processes are agents."""

from __future__ import annotations

import os
import sqlite3
import time
import tomllib

import psutil
import pytest

from agentop.agentclis.codex import CodexAgentCli
from agentop.tmux import CapturePane

THREAD_A = "01a18bcf-f7bc-7af3-91b5-74bcf3f51fca"
THREAD_B = "01a19215-cdb4-7361-9271-43cddf1afd12"
THREAD_C = "01a20835-9fc9-7cc1-8f3d-9440bcd6fa01"


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


def footer(home, monkeypatch, line: str):
    with sqlite3.connect(home / "state_5.sqlite") as db:
        for column in ("name TEXT", "recency_at_ms INT", "updated_at_ms INT"):
            db.execute(f"ALTER TABLE threads ADD COLUMN {column}")
        db.execute("UPDATE threads SET name = title, recency_at_ms = 5000")
        db.execute("INSERT INTO threads VALUES (?, NULL, 'please make a plan for the release', 0, NULL, 4000, '/w', 0, NULL, 4000, 4000)", (THREAD_C,))
    monkeypatch.setattr(CodexAgentCli, "_pane_of", staticmethod(lambda pid: "s"))
    monkeypatch.setattr(CapturePane, "screen", staticmethod(lambda pane: f"› Ask Codex to do anything\n\n  {line}\n  ? for shortcuts\n"))


def test_on_a_shared_app_server_the_footer_names_the_thread_after_a_clear(home, monkeypatch):
    agent = CodexAgentCli()
    monkeypatch.setattr(psutil.Process, "cmdline", lambda self: ["codex", "resume", THREAD_A])
    footer(home, monkeypatch, "GPT-6-Sol medium · /w · After /new")
    assert title(agent) == "After /new"


def test_a_footer_cut_short_matches_the_first_message(home, monkeypatch):
    footer(home, monkeypatch, "GPT-6-Sol medium · /w · please make a plan for the…")
    assert CodexAgentCli().get_session_meta(os.getpid(), "/w").get("session_id") == THREAD_C


def test_a_footer_in_another_folder_names_nothing(home, monkeypatch):
    footer(home, monkeypatch, "GPT-6-Sol medium · /elsewhere · After /new")
    assert title(CodexAgentCli()) is None


@pytest.mark.parametrize(
    ("name", "cmdline", "agent"),
    [
        ("codex", "codex --dangerously-bypass-hook-trust", True),
        ("codex", f"codex resume {THREAD_A}", True),
        ("codex", "codex -c features.code_mode_host=true app-server --listen unix://", False),
        ("codex", "/home/u/.codex/packages/standalone/releases/0.153.4/bin/codex app-server daemon pid-update-loop", False),
        ("codex-code-mode-host", "/home/u/.codex/packages/standalone/releases/0.165.0/bin/codex-code-mode-host", False),
        ("codex", "codex exec --ephemeral --model gpt-6-luna -", False),
        ("codex", "/home/u/.codex/packages/standalone/releases/0.169.0/bin/codex exec -", False),
        ("codex", "codex 'exec the tests'", True),
    ],
)
def test_codex_background_processes_are_not_agents(name, cmdline, agent):
    assert CodexAgentCli().matches(name, cmdline) is agent


@pytest.fixture
def config(home):
    path = home / "config.toml"
    path.write_text('model = "gpt-5"\n')
    return path


def test_pretrust_adds_the_folder_once(config):
    CodexAgentCli()._pretrust_dir("/w/proj")
    CodexAgentCli()._pretrust_dir("/w/proj")
    data = tomllib.loads(config.read_text())
    assert data["projects"] == {"/w/proj": {"trust_level": "trusted"}}
    assert config.read_text().count("/w/proj") == 1


def test_a_folder_name_cannot_add_its_own_settings(config):
    # A quote ends the TOML key unless escaped; this name would add an MCP server.
    cwd = '/w/x"]\ntrust_level = "trusted"\n[mcp_servers.evil]\ncommand = "sh'
    CodexAgentCli()._pretrust_dir(cwd)
    assert "mcp_servers" not in tomllib.loads(config.read_text())


def test_a_quote_or_backslash_in_the_name_is_escaped(config):
    cwd = '/w/a "quoted" \\ folder ü'
    CodexAgentCli()._pretrust_dir(cwd)
    data = tomllib.loads(config.read_text())
    assert data["projects"] == {cwd: {"trust_level": "trusted"}}


def test_a_name_with_another_entry_inside_it_is_still_added(config):
    config.write_text(config.read_text() + '\n[projects."/w/a"]\ntrust_level = "untrusted"\n')
    CodexAgentCli()._pretrust_dir("/w")
    assert tomllib.loads(config.read_text())["projects"]["/w"] == {"trust_level": "trusted"}


def test_a_symlinked_config_stays_a_symlink(config, tmp_path):
    real = tmp_path / "dotfiles-config.toml"
    config.rename(real)
    config.symlink_to(real)
    CodexAgentCli()._pretrust_dir("/w/proj")
    assert config.is_symlink()
    assert "/w/proj" in tomllib.loads(real.read_text())["projects"]


@pytest.mark.parametrize("cwd", ["/w/new\nline", "/w/tab\there", "/w/bad\udcff"])
def test_a_name_with_control_or_surrogate_characters_is_not_written(config, cwd):
    before = config.read_text()
    CodexAgentCli()._pretrust_dir(cwd)
    assert config.read_text() == before


def test_a_config_that_does_not_parse_is_left_alone(config):
    config.write_text("this is = = not toml\n")
    CodexAgentCli()._pretrust_dir("/w/proj")
    assert config.read_text() == "this is = = not toml\n"
