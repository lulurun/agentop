import json
import subprocess
import sys

from agentop import signals


def test_lifecycle_and_ignored_notifications():
    status = signals.Status()
    def send(event, kind="", tool="claude"):
        status.apply({"at": status.at + 1, "event": event, "kind": kind, "tool": tool})
    send("SessionStart")
    assert status.state == "idle"
    send("UserPromptSubmit")
    send("Notification", "idle_prompt")
    assert status.state == "working"
    send("Notification", "permission_prompt")
    assert status.state == "input"
    send("PostToolUse")
    assert status.state == "working"
    send("StopFailure")
    assert status.state == "done"
    send("PreToolUse", "request_user_input", "codex")
    assert status.state == "input"
    send("Interrupt", tool="codex")
    assert status.state == "done"


def test_hook_records_no_user_content(tmp_path):
    path = tmp_path / "events"
    hook = signals.Path(signals.__file__).with_name("signal_hook.py")
    result = subprocess.run([sys.executable, str(hook), str(path), "codex"],
                            input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "shell", "prompt": "secret"}),
                            text=True, capture_output=True, check=True)
    assert result.stdout == result.stderr == ""
    assert "secret" not in path.read_text()
    assert json.loads(path.read_text())["kind"] == "shell"


def test_interruption_fallback_requires_seen_busy():
    fallback = signals.ScreenFallback()
    status = signals.Status("working", 1, "UserPromptSubmit", "claude", True)
    assert fallback.apply(status, "idle", 2).state == "working"
    assert fallback.apply(status, "esc to interrupt", 3).state == "working"
    assert fallback.apply(status, "idle", 4).state == "working"
    assert fallback.apply(status, "idle", 5).state == "done"


def test_watcher_uses_events_instead_of_screen_changes(tmp_path, monkeypatch):
    from agentop.plugins.attention import Watcher

    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    signals.launch_flags("claude", "agent")
    path = signals.event_path("agent")
    row = {"id": "s", "managed": True, "status": "running", "tmux": {"session": "agent"}}
    text = ["starting"]
    watcher = Watcher(lambda: {"agent": (100, "80x24", "%1")}, lambda _: text[0])
    def event(name, at):
        with path.open("a") as stream:
            stream.write(json.dumps({"event": name, "at": at, "tool": "claude"}) + "\n")
    watcher.tick([row], now=100)
    event("UserPromptSubmit", 101)
    watcher.tick([row], now=102)
    assert watcher.waiting() == {}
    event("Stop", 103)
    watcher.tick([row], now=104)
    assert watcher.waiting() == {"s": 103}
    text[0] = "owner typing a draft"
    watcher.tick([row], now=105)
    assert watcher.waiting() == {"s": 103}
    event("UserPromptSubmit", 106)
    watcher.tick([row], now=107)
    assert watcher.waiting() == {}


def test_incremental_reader_handles_partial_records_truncation_and_relaunch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    signals.launch_flags("codex", "agent")
    path = signals.event_path("agent")
    reader = signals.EventReader("agent")
    assert reader.read() is None
    with path.open("ab") as stream:
        stream.write(b'{"event":"UserPromptSubmit","at":1,"tool":"codex"}\n{"event":"St')
    assert reader.read().state == "working"
    saved_offset = reader.offset
    assert reader.read().state == "working"
    assert reader.offset == saved_offset
    with path.open("ab") as stream:
        stream.write(b'op","at":2,"tool":"codex"}\n')
    assert reader.read().state == "done"
    path.write_text('{"event":"UserPromptSubmit","at":3,"tool":"codex"}\n')
    assert reader.read().state == "working"
    signals.launch_flags("codex", "agent")
    assert reader.read() is None
    path.write_text('{"event":"Stop","at":4,"tool":"codex"}\n')
    assert reader.read().state == "done"



def test_codex_permission_requires_visible_approval():
    fallback = signals.ScreenFallback()
    status = signals.Status("working", 1, "PermissionRequest", "codex", True)
    assert fallback.apply(status, "Automatically reviewing permission", 2).state == "working"
    screen = "Would you like to run the following command?\n1. Yes, proceed (y)\n2. No"
    assert fallback.apply(status, screen, 3).state == "input"
    assert fallback.apply(status, screen, 4).at == 3
