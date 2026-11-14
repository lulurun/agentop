import os
import stat

import pytest

from agentop.service import auth, config, identity
from agentop.service.store import _default_name, merge_sessions

NOW = 1000.0


def scan_row(name, tmux=None, **kw):
    return {"name": name, "tool": "claude", "pid": 1, "cwd": "/w", "managed": True, "tmux": tmux, **kw}


def test_running_row_from_scan_and_tmux():
    rows = merge_sessions(
        [scan_row("a-1", {"session": "a-1"}, runtime_seconds=10)],
        {"a-1": 2},
        {"a-1": {"managed": True, "display_name": "a", "created_at": 5.0}},
        {},
        {"a-1": {"ai_title": "Title"}},
        NOW,
    )
    row = rows["a-1"]
    assert row["status"] == "running" and row["attachable"] and row["managed"]
    assert row["display_name"] == "a" and row["ai_title"] == "Title"
    assert row["tmux"] == {"session": "a-1", "clients": 2} and row["created_at"] == 5.0


def test_tmux_alive_without_process_is_exited():
    rows = merge_sessions([], {"a-1": 0}, {"a-1": {"managed": True, "status": "running"}}, {}, {}, NOW)
    assert rows["a-1"]["status"] == "exited" and rows["a-1"]["attachable"]


def test_tmux_gone_without_process_is_removed():
    assert merge_sessions([], {}, {"a-1": {"managed": True, "status": "running"}}, {}, {}, NOW) == {}


def test_a_stranger_tmux_session_is_not_listed():
    assert merge_sessions([], {"mine": 0}, {}, {}, {}, NOW) == {}


def test_failed_stays_with_or_without_tmux():
    record = {"status": "failed", "error": "boom"}
    with_tmux = merge_sessions([], {"a-1": 0}, {"a-1": record}, {}, {}, NOW)["a-1"]
    without = merge_sessions([], {}, {"a-1": record}, {}, {}, NOW)["a-1"]
    assert with_tmux["status"] == "failed" and with_tmux["attachable"] and with_tmux["error"] == "boom"
    assert without["status"] == "failed" and not without["attachable"]


def test_starting_wins_even_once_the_process_is_visible():
    rows = merge_sessions(
        [scan_row("a-1", {"session": "a-1"})], {"a-1": 0}, {}, {"a-1": {"step": "confirming"}}, {}, NOW
    )
    assert rows["a-1"]["status"] == "starting" and rows["a-1"]["step"] == "confirming"


def test_starting_before_tmux_exists():
    rows = merge_sessions([], {}, {}, {"a-1": {"step": "creating", "tool": "claude"}}, {}, NOW)
    assert rows["a-1"]["status"] == "starting" and not rows["a-1"]["attachable"]


def test_external_process_outside_tmux_is_not_attachable():
    row = merge_sessions([scan_row("claude-9", managed=False)], {}, {}, {}, {}, NOW)["claude-9"]
    assert row["status"] == "running" and not row["attachable"] and not row["managed"]


def test_external_tmux_session_can_differ_from_the_id():
    row = merge_sessions([scan_row("claude-9", {"session": "work"}, managed=False)], {"work": 1}, {}, {}, {}, NOW)
    assert row["claude-9"]["tmux"] == {"session": "work", "clients": 1}


@pytest.mark.parametrize(
    "cwd, expected",
    [("/home/me/my.project", "my-project"), ("/", "session"), ("/a/" + "x" * 50, "x" * 32), ("/a/日本語", "session")],
)
def test_default_name(cwd, expected):
    assert _default_name(cwd) == expected


def test_ticket_is_single_use():
    tickets = auth.TicketStore()
    ticket = tickets.issue("events")
    assert tickets.consume(ticket, "events")
    assert not tickets.consume(ticket, "events")


def test_ticket_expires():
    now = [0.0]
    tickets = auth.TicketStore(clock=lambda: now[0])
    ticket = tickets.issue("events")
    now[0] = auth.TICKET_TTL_SECONDS + 1
    assert not tickets.consume(ticket, "events")


def test_ticket_for_the_wrong_scope_fails_and_is_spent():
    tickets = auth.TicketStore()
    ticket = tickets.issue(auth.terminal_scope("a"))
    assert not tickets.consume(ticket, auth.terminal_scope("b"))
    assert not tickets.consume(ticket, auth.terminal_scope("a"))


@pytest.mark.parametrize("ticket", [None, "", "made-up"])
def test_unknown_ticket(ticket):
    assert not auth.TicketStore().consume(ticket, "events")


@pytest.mark.parametrize(
    "header, expected",
    [("Bearer good", True), ("bearer good", True), ("Bearer bad", False), ("good", False), ("", False), (None, False)],
)
def test_bearer_matches(header, expected):
    assert auth.bearer_matches(header, "good") is expected


def test_identity_is_private_and_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    first = identity.load_or_create()
    assert stat.S_IMODE(os.stat(identity.identity_path()).st_mode) == 0o600
    assert identity.load_or_create() == first


def test_rotate_keeps_the_instance_id(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    first = identity.load_or_create()
    rotated = identity.rotate_token()
    assert rotated.token != first.token and rotated.instance_id == first.instance_id


def test_config_file_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('host = "0.0.0.0"\nport = 1234\nunknown = 1\n')
    settings = config.load(port=None, name="box")
    assert (settings.host, settings.port, settings.name) == ("0.0.0.0", 1234, "box")
    assert not settings.is_loopback


@pytest.mark.parametrize("host, expected", [("127.0.0.1", True), ("localhost", True), ("::1", True), ("0.0.0.0", False)])
def test_is_loopback(host, expected):
    assert config.Settings(host=host).is_loopback is expected


def test_serve_refuses_plain_http_on_a_non_loopback_address(monkeypatch):
    from agentop.service import app

    monkeypatch.setattr(app.host, "prepare", lambda: pytest.fail("should refuse before preparing the host"))
    with pytest.raises(SystemExit):
        app.serve(config.Settings(host="0.0.0.0"))


def test_generated_api_schema_is_current():
    """If this fails, run `npm run gen:api` in ui/ and commit the result."""
    from pathlib import Path

    from agentop.service.openapi import render

    assert (Path(__file__).parent.parent / "ui/openapi.json").read_text() == render()


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--host", "10.0.0.5"], "http://10.0.0.5:9775#token=secret"),
        (["--url", "http://203.0.113.9:7700/agentop/"], "http://203.0.113.9:7700/agentop#token=secret"),
    ],
)
def test_connect_info_can_name_a_proxy_path(argv, expected, monkeypatch, capsys):
    from types import SimpleNamespace

    from agentop import cli
    from agentop.service import config, identity

    monkeypatch.setattr(config, "load", lambda: SimpleNamespace(tls=False, host="127.0.0.1", port=9775))
    monkeypatch.setattr(identity, "load_or_create", lambda: SimpleNamespace(token="secret"))
    args = SimpleNamespace(host=None, url=None)
    setattr(args, argv[0].removeprefix("--"), argv[1])
    cli.cmd_service_connect_info(args)
    assert capsys.readouterr().out.strip() == expected


def test_pages_are_revalidated_and_hashed_assets_are_kept(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentop.service.app import AppFiles

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "assets" / "index-abc123.js").write_text("")
    web = FastAPI()
    web.mount("/", AppFiles(directory=str(tmp_path), html=True))
    client = TestClient(web)

    assert client.get("/").headers["cache-control"] == "no-cache"
    assert "immutable" in client.get("/assets/index-abc123.js").headers["cache-control"]
