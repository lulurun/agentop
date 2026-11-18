from concurrent.futures import ThreadPoolExecutor

import pytest

from agentop import agentclis, tmux
from agentop.tmux import CapturePane, PasteError, Session


def _new(name):
    assert Session.new(name, "/tmp")


def test_targets_are_exact_not_prefix_matched(tmux_server):
    _new("foo-1")
    _new("foo-10")
    assert not Session.has("foo")
    assert Session.has("foo-1")
    Session.kill("foo-1")
    assert not Session.has("foo-1")
    assert Session.has("foo-10")


def test_pane_commands_hit_only_their_session(tmux_server):
    _new("foo-1")
    _new("foo-10")
    Session.send_keys("foo-10", "echo only-in-ten", "Enter")
    for _ in range(40):
        if "only-in-ten" in CapturePane.screen("foo-10").replace("echo only-in-ten", ""):
            break
        tmux.subprocess.run(["sleep", "0.05"])
    assert "only-in-ten" in CapturePane.screen("foo-10")
    assert "only-in-ten" not in CapturePane.screen("foo-1")


def test_list_all_reports_sessions(tmux_server):
    _new("foo-1")
    assert Session.list_all() == {"keeper": 0, "foo-1": 0}


def test_list_all_with_no_server_is_empty_not_an_error(monkeypatch, tmux_server):
    monkeypatch.setenv(tmux.SOCKET_ENV, "agentop-test-no-such-server")
    assert Session.list_all() == {}


def test_set_option_stays_on_one_session(tmux_server):
    _new("foo-1")
    _new("foo-2")
    assert Session.set_option("foo-1", "mouse", "on")
    assert b"on" in tmux.run("show-options", "-t", "=foo-1:", "mouse").stdout
    assert b"mouse" not in tmux.run("show-options", "-t", "=foo-2:", "mouse").stdout
    assert tmux.run("show-options", "-gv", "mouse").stdout.strip() == b"off"


# Turns bracketed paste on, like Codex does, and saves the raw bytes it reads.
READ_RAW = """import os, sys, termios, tty
sys.stdout.write("\\x1b[?2004h"); sys.stdout.flush()
tty.setraw(0)
open(sys.argv[1] + ".ready", "w").close()
data = b""
while b"done" not in data:
    data += os.read(0, 1024)
open(sys.argv[1] + ".tmp", "wb").write(data)
os.rename(sys.argv[1] + ".tmp", sys.argv[1])
"""


def test_a_bracketed_paste_arrives_as_real_escape_codes(tmux_server, tmp_path):
    script, out = tmp_path / "raw.py", tmp_path / "raw.out"
    script.write_text(READ_RAW)
    _new("raw")
    Session.send_keys("raw", f"python3 {script} {out}", "Enter")
    ready = tmp_path / "raw.out.ready"
    for _ in range(200):
        if ready.exists():
            break
        tmux.subprocess.run(["sleep", "0.05"])
    # tmux has to have seen the pane turn bracketed paste on.
    for _ in range(100):
        if tmux.run("display", "-p", "-t", "=raw:", "#{bracket_paste_flag}", text=True).stdout.strip() == "1":
            break
        tmux.subprocess.run(["sleep", "0.02"])

    Session.paste_text_bracketed("raw", "one\ntwo")
    Session.send_keys("raw", "done")

    for _ in range(100):
        if out.exists():
            break
        tmux.subprocess.run(["sleep", "0.05"])
    assert out.read_bytes().startswith(b"\x1b[200~one\rtwo\x1b[201~")


def test_leave_mode_takes_the_pane_out_of_copy_mode(tmux_server):
    _new("foo-1")
    in_mode = lambda: tmux.run("display-message", "-p", "-t", tmux.pane_target("foo-1"), "#{pane_in_mode}", text=True).stdout.strip()
    tmux.run("copy-mode", "-t", tmux.pane_target("foo-1"))
    assert in_mode() == "1"
    Session.leave_mode("foo-1")
    assert in_mode() == "0"
    Session.leave_mode("foo-1")  # not in a mode: nothing happens
    assert in_mode() == "0"


def _buffers():
    return tmux.run("list-buffers", "-F", "#{buffer_name}", text=True).stdout.split()


def _wait_for(name, text):
    for _ in range(100):
        if text in CapturePane.screen(name):
            return True
        tmux.subprocess.run(["sleep", "0.05"])
    return False


def test_a_paste_uses_a_buffer_of_its_own_and_leaves_none_behind(tmux_server):
    # A copy made in copy mode is in tmux's shared buffer: a paste must neither send it nor use it up.
    _new("foo-1")
    tmux.run("set-buffer", "-b", "mine", "the user's copy")
    Session.paste_text("foo-1", "echo pasted-text")
    Session.send_keys("foo-1", "Enter")
    assert _wait_for("foo-1", "pasted-text")
    assert "the user's copy" not in CapturePane.screen("foo-1")
    assert _buffers() == ["mine"]


def test_a_failed_paste_raises_and_leaves_no_buffer(tmux_server):
    with pytest.raises(PasteError):
        Session.paste_text("no-such-session", "hello")
    assert _buffers() == []


def test_concurrent_pastes_each_reach_their_own_session(tmux_server):
    names = [f"s-{i}" for i in range(6)]
    for name in names:
        _new(name)
    with ThreadPoolExecutor(len(names)) as pool:
        list(pool.map(lambda n: Session.paste_text(n, f"echo for-{n}-{'x' * 2000}"), names))
    for name in names:
        Session.send_keys(name, "Enter")
    for name in names:
        for _ in range(100):
            if f"for-{name}-" in CapturePane.scrollback(name):
                break
            tmux.subprocess.run(["sleep", "0.05"])
        screen = CapturePane.scrollback(name)
        assert f"for-{name}-" in screen
        assert all(f"for-{other}-" not in screen for other in names if other != name)


def test_no_enter_is_sent_after_a_failed_paste(monkeypatch):
    keys = []

    def fail(name, text):
        raise PasteError("paste-buffer failed")

    monkeypatch.setattr(Session, "paste_text", staticmethod(fail))
    monkeypatch.setattr(Session, "paste_text_bracketed", staticmethod(fail))
    monkeypatch.setattr(Session, "send_keys", staticmethod(lambda name, *k: keys.append(k)))
    monkeypatch.setattr(Session, "leave_mode", staticmethod(lambda name: None))
    monkeypatch.setattr("agentop.agentclis.codex.time.sleep", lambda s: None)
    for agent in agentclis.AGENTS:
        with pytest.raises(PasteError):
            agent.deliver("t", "hello")
    assert keys == []


def test_deliver_takes_the_pane_out_of_copy_mode_first(monkeypatch):
    calls = []
    monkeypatch.setattr(Session, "leave_mode", staticmethod(lambda name: calls.append("leave")))
    monkeypatch.setattr(Session, "paste_text_bracketed", staticmethod(lambda name, text: calls.append("paste")))
    monkeypatch.setattr(Session, "send_keys", staticmethod(lambda name, *k: calls.append(k)))
    agentclis.get_agent("claude").deliver("t", "hello")
    assert calls == ["leave", "paste", ("Enter",)]
