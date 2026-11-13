"""The service's start: which agent tools are installed, and the skills it installs for them."""

from __future__ import annotations

import pytest

from agentop import agentclis, skills
from agentop.agentclis.base import AgentCli
from agentop.service import host


class Tool(AgentCli):
    def __init__(self, name, binary, skills_root=None):
        self.name = name
        self._binary = binary
        self._skills = skills_root

    @property
    def launch_cmd(self):
        return f"{self._binary} --flag"

    def skills_dir(self):
        return self._skills


def program(folder, name):
    path = folder / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


@pytest.fixture
def bare_path(tmp_path, monkeypatch):
    """A PATH with nothing on it, and a shell whose rc files add a folder to it."""
    service_bin = tmp_path / "service-bin"
    shell_bin = tmp_path / "shell-bin"
    service_bin.mkdir()
    shell_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    # The rc file talks, as real ones do, and only the shell sees shell_bin.
    (home / ".profile").write_text(f'echo "welcome"\nexport PATH="{shell_bin}:$PATH"\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("ENV", str(home / ".profile"))
    monkeypatch.setenv("PATH", f"{service_bin}:/usr/bin:/bin")
    return service_bin, shell_bin


def test_installed_finds_programs_on_the_service_path_and_the_shell_path(bare_path):
    service_bin, shell_bin = bare_path
    program(service_bin, "alpha")
    program(shell_bin, "beta")
    tools = [Tool("a", "alpha"), Tool("b", "beta"), Tool("c", "gamma")]
    assert [t.name for t in agentclis.installed(tools)] == ["a", "b"]


def test_installed_survives_a_missing_shell(bare_path, monkeypatch):
    service_bin, _ = bare_path
    program(service_bin, "alpha")
    monkeypatch.setenv("SHELL", "/nonexistent/shell")
    assert [t.name for t in agentclis.installed([Tool("a", "alpha"), Tool("b", "beta")])] == ["a"]


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    source = tmp_path / "bundle"
    for name in ("one", "two"):
        (source / name).mkdir(parents=True)
        (source / name / "SKILL.md").write_text(f"# {name}\n")
    (source / "not-a-skill").mkdir()
    monkeypatch.setattr(skills, "HERE", source)
    return source


def actions(results):
    return {r.name: r.action for r in results}


def test_skills_are_copied_with_a_marker(bundle, tmp_path):
    root = tmp_path / "skills"
    assert actions(skills.install(root)) == {"one": "installed", "two": "installed"}
    assert (root / "one" / "SKILL.md").read_text() == "# one\n"
    assert (root / "one" / skills.MARKER).is_file()
    assert not (root / "not-a-skill").exists()
    assert actions(skills.install(root)) == {"one": "unchanged", "two": "unchanged"}


def test_a_changed_skill_replaces_agentops_copy(bundle, tmp_path):
    root = tmp_path / "skills"
    skills.install(root)
    (bundle / "one" / "SKILL.md").write_text("# one, better\n")
    (root / "two" / "stale.md").write_text("left from an old version")
    assert actions(skills.install(root)) == {"one": "updated", "two": "updated"}
    assert (root / "one" / "SKILL.md").read_text() == "# one, better\n"
    assert not (root / "two" / "stale.md").exists()
    assert (root / "two" / skills.MARKER).is_file()


def test_the_users_own_folders_and_links_are_left_alone(bundle, tmp_path):
    root = tmp_path / "skills"
    (root / "one").mkdir(parents=True)
    (root / "one" / "SKILL.md").write_text("mine")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "two").symlink_to(elsewhere)
    assert actions(skills.install(root)) == {"one": "skipped", "two": "skipped"}
    assert (root / "one" / "SKILL.md").read_text() == "mine"
    assert (root / "two").resolve() == elsewhere


def test_links_to_the_bundle_stay_and_broken_links_are_replaced(bundle, tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "one").symlink_to(bundle / "one")
    (root / "two").symlink_to(tmp_path / "where-the-repo-used-to-keep-it")
    assert actions(skills.install(root)) == {"one": "linked", "two": "installed"}
    assert not (root / "two").is_symlink()
    assert (root / "two" / "SKILL.md").read_text() == "# two\n"


def test_the_shipped_skills_are_found():
    assert {"handoff", "recall"} <= {p.name for p in skills.bundled()}


def test_prepare_installs_skills_only_for_installed_tools(bundle, bare_path, tmp_path, monkeypatch):
    service_bin, _ = bare_path
    program(service_bin, "alpha")
    with_skills = tmp_path / "alpha-skills"
    missing_skills = tmp_path / "beta-skills"
    monkeypatch.setattr(agentclis, "AGENTS", [Tool("a", "alpha", with_skills), Tool("b", "beta", missing_skills)])
    assert [t.name for t in host.prepare()] == ["a"]
    assert (with_skills / "one" / "SKILL.md").is_file()
    assert not missing_skills.exists()


def test_prepare_refuses_a_host_with_no_tool(bare_path, monkeypatch):
    monkeypatch.setattr(agentclis, "AGENTS", [Tool("a", "alpha"), Tool("b", "beta")])
    with pytest.raises(host.NoAgentTools, match="alpha"):
        host.prepare()
