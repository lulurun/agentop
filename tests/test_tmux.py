from agentop import tmux
from agentop.tmux import CapturePane, Session


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
