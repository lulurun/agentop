"""Moving ~/.agent-dashboard and its flat layout into ~/.agentop."""

import json

import pytest

from agentop import paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    return tmp_path


def flat_layout(folder):
    (folder / "dialogues" / "d1").mkdir(parents=True)
    (folder / "memory" / "archive" / "claude").mkdir(parents=True)
    (folder / "plugins").mkdir()
    for name in ("config.toml", "service.json", "sessions.json", "sessions.lock", "plugins.json", "serve.log", "update.log"):
        (folder / name).write_text(name)
    (folder / "update-status.json").write_text("{}")
    (folder / "memory" / "state.json").write_text("{}")
    (folder / "plugins" / "attention.json").write_text(json.dumps({"seen": 1}))


def files(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def test_the_old_folder_is_renamed_and_reorganized(home):
    flat_layout(home / ".agent-dashboard")
    paths.migrate()
    new = home / ".agentop"
    assert files(new) == [
        "config.toml",
        "logs/serve.log",
        "logs/update.log",
        "plugins/attention/store.json",
        "plugins/memory/state.json",
        "service.json",
        "state/plugins.json",
        "state/sessions.json",
        "state/sessions.lock",
        "state/update-status.json",
    ]
    assert (new / "plugins" / "dialogue" / "dialogues" / "d1").is_dir()
    assert (new / "plugins" / "memory" / "archive" / "claude").is_dir()
    assert not (home / ".agent-dashboard").exists()


def test_migrating_again_changes_nothing(home):
    flat_layout(home / ".agent-dashboard")
    paths.migrate()
    before = files(home / ".agentop")
    paths.migrate()
    assert files(home / ".agentop") == before


def test_an_existing_new_folder_is_never_overwritten(home):
    (home / ".agent-dashboard").mkdir()
    (home / ".agent-dashboard" / "service.json").write_text("old")
    (home / ".agentop").mkdir()
    (home / ".agentop" / "service.json").write_text("new")
    paths.migrate()
    assert (home / ".agentop" / "service.json").read_text() == "new"
    assert (home / ".agent-dashboard" / "service.json").read_text() == "old"


def test_agentop_home_is_left_where_it_is(home, monkeypatch):
    (home / ".agent-dashboard").mkdir()
    monkeypatch.setenv(paths.HOME_ENV, str(home / "elsewhere"))
    paths.migrate()
    assert (home / ".agent-dashboard").is_dir()
    assert not (home / ".agentop").exists()


def test_until_the_service_moves_it_the_old_folder_is_used_as_it_was(home):
    # The CLI can run while an older service still writes there.
    flat_layout(home / ".agent-dashboard")
    old = home / ".agent-dashboard"
    assert (paths.data_dir(), paths.state_dir(), paths.logs_dir()) == (old, old, old)
    assert paths.dialogues_dir() == old / "dialogues"
    paths.migrate()
    new = home / ".agentop"
    assert (paths.data_dir(), paths.state_dir(), paths.logs_dir()) == (new, new / "state", new / "logs")
    assert paths.dialogues_dir() == new / "plugins" / "dialogue" / "dialogues"
