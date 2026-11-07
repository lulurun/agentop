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
