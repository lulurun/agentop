import multiprocessing
import os
from pathlib import Path

import pytest

from agentop import registry


def _writer(home, prefix):
    os.environ["AGENTOP_HOME"] = home
    for i in range(25):
        registry.upsert_session(f"{prefix}-{i}", {"managed": True})


def test_upsert_merges_and_none_removes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    registry.upsert_session("a", {"x": 1, "y": 2})
    registry.upsert_session("a", {"x": None, "z": 3})
    assert registry.load()["sessions"]["a"] == {"y": 2, "z": 3}


def test_concurrent_processes_do_not_lose_writes(tmp_path):
    procs = [multiprocessing.Process(target=_writer, args=(str(tmp_path), p)) for p in "abcd"]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    os.environ["AGENTOP_HOME"] = str(tmp_path)
    try:
        assert len(registry.load()["sessions"]) == 100
    finally:
        del os.environ["AGENTOP_HOME"]


def test_prune_stamps_then_removes_dead_records(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    registry.upsert_session("live", {"managed": True})
    registry.upsert_session("dead", {"managed": True})
    registry.upsert_session("failed", {"status": "failed"})

    assert registry.mark_ended_and_prune({"live"}, now=1000.0) == []
    sessions = registry.load()["sessions"]
    assert sessions["dead"]["ended_at"] == 1000.0
    assert "ended_at" not in sessions["live"]

    later = 1000.0 + registry.ENDED_RETENTION_SECONDS + 1
    assert registry.mark_ended_and_prune({"live"}, now=later) == ["dead"]
    assert set(registry.load()["sessions"]) == {"live", "failed"}


def test_a_corrupt_registry_is_moved_aside_not_saved_over(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    registry.upsert_session("a", {"managed": True})
    registry.upsert_session("b", {"managed": True})
    path = registry.registry_path()
    damaged = path.read_text()[:-10]
    path.write_text(damaged)

    assert registry.load() == {"sessions": {}}  # a reader sees nothing, and changes nothing
    assert path.read_text() == damaged

    with pytest.raises(registry.RegistryError):
        registry.upsert_session("c", {"managed": True})
    [aside] = path.parent.glob("sessions.json.corrupt-*")
    assert aside.read_text() == damaged

    registry.upsert_session("c", {"managed": True})  # the next write starts a new registry
    assert registry.load()["sessions"] == {"c": {"managed": True}}


def test_a_registry_that_cannot_be_read_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    registry.upsert_session("a", {"managed": True})
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(registry.RegistryError):
        registry.upsert_session("b", {"managed": True})
    monkeypatch.undo()
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    assert registry.load()["sessions"] == {"a": {"managed": True}}
    assert not list(registry.registry_path().parent.glob("*.corrupt-*"))
