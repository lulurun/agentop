import subprocess

import pytest

from agentop.service import files


def git(root, *args):
    env = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "-C", str(root), *env, *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / ".gitignore").write_text("ignored.txt\nbuild/\n")
    (root / "kept.txt").write_text("v1\n")
    (root / "gone.txt").write_text("v1\n")
    (root / "old-name.txt").write_text("rename me\n" * 20)
    (root / "sub").mkdir()
    (root / "sub/inner.txt").write_text("x")
    git(root, "add", ".")
    git(root, "commit", "-m", "first")
    return root


def names(listing):
    return [e["name"] for e in listing["entries"]]


def test_lists_folders_first_and_hides_ignored(repo):
    (repo / "ignored.txt").write_text("x")
    (repo / "build").mkdir()
    listing = files.list_directory(str(repo))
    assert names(listing) == ["sub", ".gitignore", "gone.txt", "kept.txt", "old-name.txt"]
    assert listing["entries"][0]["is_dir"]


def test_lists_a_subdirectory_with_paths_from_the_root(repo):
    listing = files.list_directory(str(repo), "sub")
    assert listing["path"] == "sub"
    assert listing["entries"][0]["path"] == "sub/inner.txt"


def test_outside_a_repository_nothing_is_ignored(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert names(files.list_directory(str(tmp_path))) == ["a.txt"]


@pytest.mark.parametrize("path", ["..", "../..", "sub/../..", "/etc", "../repo-sibling"])
def test_paths_cannot_leave_the_root(repo, path):
    (repo.parent / "repo-sibling").mkdir()
    (repo / "x").mkdir(exist_ok=True)
    if path == "/etc":
        # An absolute path is taken as relative to the root, so it is merely missing.
        with pytest.raises(FileNotFoundError):
            files.list_directory(str(repo), path)
    else:
        with pytest.raises(files.OutsideRoot):
            files.list_directory(str(repo), path)


def test_a_symlink_cannot_lead_out_of_the_root(repo, tmp_path):
    (tmp_path / "secret").mkdir()
    (repo / "escape").symlink_to(tmp_path / "secret")
    with pytest.raises(files.OutsideRoot):
        files.list_directory(str(repo), "escape")
    entry = next(e for e in files.list_directory(str(repo))["entries"] if e["name"] == "escape")
    assert entry["is_link"] and not entry["is_dir"]


def test_long_listings_are_cut_short(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_ENTRIES", 2)
    for name in "abc":
        (tmp_path / name).write_text("x")
    listing = files.list_directory(str(tmp_path))
    assert len(listing["entries"]) == 2 and listing["truncated"]


def test_changes_cover_every_kind(repo):
    (repo / "kept.txt").write_text("v2\n")
    (repo / "gone.txt").unlink()
    (repo / "new file.txt").write_text("x")
    (repo / "日本語.txt").write_text("x")
    git(repo, "mv", "old-name.txt", "new-name.txt")
    changes = files.git_changes(str(repo))
    status = {f["path"]: f["status"] for f in changes["files"]}
    assert changes["is_repo"] and changes["branch"] == "main"
    assert status == {
        "kept.txt": "M",
        "gone.txt": "D",
        "new-name.txt": "R",
        "new file.txt": "?",
        "日本語.txt": "?",
    }


def test_a_clean_repository_has_no_changes(repo):
    assert files.git_changes(str(repo))["files"] == []


def test_not_a_repository(tmp_path):
    assert files.git_changes(str(tmp_path))["is_repo"] is False


def test_detached_head_has_no_branch(repo):
    git(repo, "checkout", "--detach")
    assert files.git_changes(str(repo))["branch"] is None


def test_looking_at_a_repository_runs_nothing_from_it(repo, tmp_path):
    marker = tmp_path / "ran"
    git(repo, "config", "core.fsmonitor", f"touch {marker}; false")
    files.git_changes(str(repo))
    assert not marker.exists()


def test_a_modified_file_diffs_against_head(repo):
    (repo / "kept.txt").write_text("v2\n")
    diff = files.git_diff(str(repo), "kept.txt")
    assert "-v1\n+v2\n" in diff["text"]
    assert not diff["binary"] and not diff["truncated"]


def test_a_staged_change_is_in_the_diff_too(repo):
    (repo / "kept.txt").write_text("v2\n")
    git(repo, "add", "kept.txt")
    assert "+v2" in files.git_diff(str(repo), "kept.txt")["text"]


def test_a_deleted_file_diffs_as_removed(repo):
    (repo / "gone.txt").unlink()
    assert "deleted file mode" in files.git_diff(str(repo), "gone.txt")["text"]


def test_an_untracked_file_diffs_as_added(repo):
    (repo / "sub" / "new.txt").write_text("hello\n")
    diff = files.git_diff(str(repo / "sub"), "new.txt")
    assert "new file mode" in diff["text"] and "+hello" in diff["text"]


def test_a_binary_change_is_marked(repo):
    (repo / "kept.txt").write_bytes(b"\0\1\2")
    assert files.git_diff(str(repo), "kept.txt")["binary"]


@pytest.mark.parametrize("path", ["kept.txt", "ignored.txt", "missing.txt"])
def test_no_change_has_no_diff(repo, path):
    (repo / "ignored.txt").write_text("x")
    with pytest.raises(FileNotFoundError):
        files.git_diff(str(repo), path)


def test_a_diff_cannot_leave_the_root(repo):
    with pytest.raises(files.OutsideRoot):
        files.git_diff(str(repo / "sub"), "../kept.txt")


def test_outside_a_repository_there_is_no_diff(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(FileNotFoundError):
        files.git_diff(str(tmp_path), "a.txt")


def test_a_long_diff_ends_on_a_whole_line(repo):
    (repo / "kept.txt").write_text("line\n" * 1000)
    diff = files.git_diff(str(repo), "kept.txt", max_bytes=500)
    assert diff["truncated"] and diff["text"].endswith("\n") and len(diff["text"]) <= 500
