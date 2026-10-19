"""The map-reviewer before every commit (scripts/map_review_gate.py): on a temporary git repository, an agent's
``git commit`` is stopped until the map-reviewer has seen the change as it is, and nothing else is ever stopped."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "scripts" / "map_review_gate.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "s.py").write_text("s = 1\n")
    (tmp_path / "CLAUDE.md").write_text("# map\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "start")
    return tmp_path


def gate(cwd: Path, mode: str, command: str = "") -> subprocess.CompletedProcess:
    event = {"cwd": str(cwd), "tool_input": {"command": command}}
    return subprocess.run([sys.executable, str(GATE), mode], input=json.dumps(event), capture_output=True, text=True)


def test_a_commit_the_reviewer_has_not_seen_is_stopped_until_it_has(repo):
    (repo / "a.py").write_text("x = 2\n")
    git(repo, "add", "a.py")
    stopped = gate(repo, "--commit", 'git commit -m "change"')
    assert stopped.returncode == 2 and "a.py" in stopped.stderr and "map-reviewer" in stopped.stderr
    assert gate(repo, "--reviewed").returncode == 0
    assert gate(repo, "--commit", 'git commit -m "change"').returncode == 0


def test_whatever_the_command_adds_first_counts(repo):
    """``git add -A && git commit``, ``commit -a`` and ``commit <paths>`` take unstaged and new files: the hook runs
    before the command, so every changed file is checked, not only what is staged yet."""
    (repo / "a.py").write_text("x = 2\n")
    (repo / "new.py").write_text("n = 1\n")
    for command in ("git add -A && git commit -m x", "git commit -am x", "git commit -m x a.py", "git commit -- a.py"):
        assert gate(repo, "--commit", command).returncode == 2, command
    gate(repo, "--reviewed")
    assert gate(repo, "--commit", "git add -A && git commit -m x").returncode == 0


def test_code_changed_after_the_review_needs_a_new_one_but_markdown_does_not(repo):
    (repo / "a.py").write_text("x = 2\n")
    gate(repo, "--reviewed")
    (repo / "CLAUDE.md").write_text("# map, updated as the reviewer asked\n")
    assert gate(repo, "--commit", "git add -A && git commit -m ok").returncode == 0
    (repo / "a.py").write_text("x = 3\n")
    assert gate(repo, "--commit", "git commit -am again").returncode == 2


def test_partial_staging_passes_once_reviewed_and_deletions_count(repo):
    (repo / "a.py").write_text("x = 2\n")
    git(repo, "add", "a.py")
    (repo / "a.py").write_text("x = 3\n")
    gate(repo, "--reviewed")
    assert gate(repo, "--commit", "git commit -m staged-part").returncode == 0
    (repo / "sub" / "s.py").unlink()
    assert gate(repo, "--commit", "git commit -am gone").returncode == 2
    gate(repo, "--reviewed")
    assert gate(repo, "--commit", "git commit -am gone").returncode == 0


def test_a_subfolder_another_folder_and_any_file_name(repo, tmp_path_factory):
    gate(repo, "--reviewed")
    (repo / "sub" / "s.py").write_text("s = 2\n")
    assert gate(repo / "sub", "--commit", "git commit -am from-a-subfolder").returncode == 2
    gate(repo / "sub", "--reviewed")
    assert gate(repo / "sub", "--commit", "git commit -am from-a-subfolder").returncode == 0
    (repo / "café.py").write_text("c = 1\n")
    assert gate(repo, "--commit", "git add -A && git commit -m accent").returncode == 2
    gate(repo, "--reviewed")
    (repo / "café.py").write_text("c = 2\n")
    assert gate(repo, "--commit", "git add -A && git commit -m accent").returncode == 2
    elsewhere = tmp_path_factory.mktemp("session")
    assert gate(elsewhere, "--commit", f'git -C "{repo}" commit -am x').returncode == 2
    assert gate(elsewhere, "--commit", f"cd {repo} && git commit -am x").returncode == 2
    assert gate(elsewhere, "--commit", "git commit -am x").returncode == 0  # not a repository: nothing to check


def test_prefixes_wrappers_and_folders_it_cannot_follow_still_count(repo, tmp_path_factory, monkeypatch):
    (repo / "a.py").write_text("x = 2\n")
    for command in ("HUSKY=0 git commit -am x", "env GIT_AUTHOR_DATE=now git commit -am x", "command git commit -am x",
                    "time git commit -am x", "bash -c 'git commit -am x'", "echo x | xargs git commit -am",
                    "git add -A & git commit -m x", "git.exe commit -am x", "(cd /tmp && ls) && git commit -am x",
                    "cd nonexistent; git commit -am x", 'git commit -m "$(cat <<\'EOF\'\nfix\nEOF\n)"'):  # fmt: skip
        assert gate(repo, "--commit", command).returncode == 2, command
    elsewhere = tmp_path_factory.mktemp("session")
    monkeypatch.setenv("REPO", str(repo))
    assert gate(elsewhere, "--commit", 'cd "$REPO" && git commit -am x').returncode == 2
    assert gate(elsewhere, "--commit", 'git -C "$REPO" commit -am x').returncode == 2


def test_other_commands_markdown_only_commits_and_finishing_a_merge_pass(repo):
    (repo / "a.py").write_text("x = 2\n")
    for command in ("git status && git log --oneline", "git commit-tree HEAD^{tree}", 'grep -rn "git commit" .',
                    "git log --grep commit", "echo git commit"):  # fmt: skip
        assert gate(repo, "--commit", command).returncode == 0, command
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
    assert gate(repo, "--commit", "git commit --no-edit").returncode == 0
    (repo / ".git" / "MERGE_HEAD").unlink()
    git(repo, "checkout", "--", "a.py")
    (repo / "CLAUDE.md").write_text("# only the map\n")
    assert gate(repo, "--commit", "git commit -am docs").returncode == 0


def test_input_it_cannot_read_lets_the_command_through(repo, tmp_path_factory):
    for text in ("not json", "[1, 2]", ""):
        done = subprocess.run([sys.executable, str(GATE), "--commit"], input=text, capture_output=True, text=True)
        assert done.returncode == 0
    outside = tmp_path_factory.mktemp("not-a-repo")
    assert gate(outside, "--reviewed").returncode == 0
    assert gate(repo, "--commit", "git commit -m 'unbalanced").returncode == 0  # nothing changed: nothing to stop


def test_a_windows_path_keeps_its_backslashes():
    """B-131: shell words were split the POSIX way only, where a backslash escapes the next character, so
    ``cd C:\\Users\\me\\repo && git commit`` looked for a folder ``C:Usersmerepo``; read both ways, an escaped space
    (``my\\ repo``) still names its folder too."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("map_review_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    here = Path("/session")
    assert here / r"C:\Users\me\repo" in module.commit_dirs(r"cd C:\Users\me\repo && git commit -am x", here)
    assert here / r"D:\work\repo" in module.commit_dirs(r'git -C "D:\work\repo" commit -am x', here)
    assert here / "my repo" in module.commit_dirs(r"git -C my\ repo commit -am x", here)
    assert here / "my repo" in module.commit_dirs(r"cd my\ repo && git commit -am x", here)
    assert module.commit_dirs(r"g\it commit -am x", here) == [here]
    assert module.commit_dirs("git commit -m 'it works'", here) == [here]
