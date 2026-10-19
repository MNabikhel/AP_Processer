"""The codebase map's checker (scripts/check_map.py): on a small project made in a temporary git repository, each
check catches what it is for, and the real repository's map is clean."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_map.py"

GOOD_PKG = """# ap_coder: core

The core.

**Depends on:** `ap_coder/sub`
**Used by:** `ap_coder/sub`

## Interface
- `main()` (a.py): runs it.

## Gotchas and decisions
- Nothing yet.

### Bug lessons
- B-01: Don't. `tests/test_a.py::test_a`

## Files
- `__init__.py`, `a.py`: the package.
- `MODULES.md`: the module index (generated).
- `sub/`: the sub-package.
"""
GOOD_SUB = """# ap_coder/sub: sub

A sub-package.

**Depends on:** `ap_coder`
**Used by:** `ap_coder`

## Interface
- `helper()` (b.py): helps.

## Gotchas and decisions
- None.

## Files
- `__init__.py`, `b.py`: the sub-package.
"""
GOOD_ROOT = """# root

**Depends on:** none
**Used by:** none

## Files
- `ap_coder`, `docs`, `scripts`: the folders.
"""
GOOD_DOCS = """# docs

## Files
- `BUGS.md`: bugs.
"""
BUGS = """# Bug log
#### B-01 · a bug · low
- **Where:** `ap_coder/a.py:1`
- **Lesson:** Don't.
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "proj"
    files = {
        "CLAUDE.md": GOOD_ROOT,
        "ap_coder/__init__.py": "",
        "ap_coder/a.py": "from .sub.b import helper\n\n\ndef main():\n    return helper()\n",
        "ap_coder/CLAUDE.md": GOOD_PKG,
        "ap_coder/sub/__init__.py": "",
        "ap_coder/sub/b.py": "def helper():\n    from ap_coder import a\n    return a\n",
        "ap_coder/sub/CLAUDE.md": GOOD_SUB,
        "docs/BUGS.md": BUGS,
        "docs/CLAUDE.md": GOOD_DOCS,
    }
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    (repo / "scripts").mkdir()
    shutil.copy(SCRIPT, repo / "scripts" / "check_map.py")
    (repo / "scripts" / "CLAUDE.md").write_text("# scripts\n\n## Files\n- `check_map.py`: the checker.\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    subprocess.run([sys.executable, "scripts/check_map.py", "--modules"], cwd=repo, check=True, capture_output=True)
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "start")
    return repo


def run(repo: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run([sys.executable, str(repo / "scripts" / "check_map.py"), *args], cwd=repo,
                          capture_output=True, text=True)  # fmt: skip
    return done.returncode, done.stdout


def edit(repo: Path, name: str, old: str, new: str) -> None:
    path = repo / name
    path.write_text(path.read_text().replace(old, new), encoding="utf-8")


def test_a_map_that_matches_its_code_is_clean(project):
    assert run(project) == (0, "map: 0 error(s), 0 warning(s)\n")


def test_a_folder_without_its_map_fails(project):
    (project / "ap_coder" / "sub" / "CLAUDE.md").unlink()
    git(project, "rm", "-q", "--cached", "ap_coder/sub/CLAUDE.md")
    code, out = run(project)
    assert code == 1 and "ap_coder/sub: no CLAUDE.md" in out


def test_a_file_missing_from_the_index_or_listed_but_gone_fails(project):
    (project / "ap_coder" / "c.py").write_text("X = 1\n")
    git(project, "add", "ap_coder/c.py")
    edit(project, "ap_coder/sub/CLAUDE.md", "`b.py`", "`b.py`, `gone.py`")
    out = run(project)[1]
    assert "`c.py` is not in its ## Files index" in out and "lists `gone.py`, which is not in ap_coder/sub/" in out


def test_depends_on_and_used_by_must_match_the_imports(project):
    edit(project, "ap_coder/a.py", "from .sub.b import helper\n", "")
    edit(project, "ap_coder/a.py", "return helper()", "return 1")
    out = run(project)[1]
    assert "ap_coder/CLAUDE.md: **Depends on:** names `ap_coder/sub`, which the imports don't show" in out
    assert "ap_coder/sub/CLAUDE.md: **Used by:** names `ap_coder`, which the imports don't show" in out


def test_an_interface_name_that_is_not_defined_fails(project):
    edit(project, "ap_coder/CLAUDE.md", "`main()`", "`vanished()`, `main()`")
    assert "## Interface names `vanished()`, not defined in ap_coder/" in run(project)[1]


def test_an_interface_name_must_be_in_the_module_or_file_it_names(project):
    edit(project, "ap_coder/CLAUDE.md", "`main()`", "`main()`, `nosuchmodule.helper()`, `sub.b.main()`")
    out = run(project)[1]
    assert "`nosuchmodule.helper()`, which no module or class of that name defines" in out
    assert "`sub.b.main()`, which no module" in out  # main() exists, but in a.py, not b.py
    edit(project, "ap_coder/CLAUDE.md", ", `nosuchmodule.helper()`, `sub.b.main()`", ", `b.helper()`")
    assert run(project)[0] == 0
    edit(project, "ap_coder/sub/CLAUDE.md", "`helper()` (b.py)", "`helper()` (b.py), `main()` (b.py)")
    assert "`main()` (b.py), not defined in b.py" in run(project)[1]


def test_a_name_in_a_folder_without_code_must_exist_somewhere(project):
    edit(project, "docs/CLAUDE.md", "## Files", "## Interface\n- `helper()`, `invented()`\n\n## Files")
    out = run(project)[1]
    assert "docs/CLAUDE.md: ## Interface names `invented()`, defined nowhere" in out and "`helper()`" not in out


def test_a_new_module_counts_before_it_is_added_to_git(project):
    (project / "ap_coder" / "new.py").write_text("from .sub.b import helper\n")
    assert "`new.py` is not in its ## Files index" in run(project)[1]
    (project / "ap_coder" / "a.py").unlink()  # deleted, not yet git rm: gone from the map, no crash
    out = run(project)[1]
    assert "Traceback" not in out and "lists `a.py`, which is not in ap_coder/" in out


def test_a_source_file_with_a_byte_order_mark_is_read_like_python_reads_it(project):
    path = project / "ap_coder" / "sub" / "b.py"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert run(project) == (0, "map: 0 error(s), 0 warning(s)\n")


def test_every_bug_lesson_sits_in_the_folder_of_its_fix(project):
    edit(project, "ap_coder/CLAUDE.md", "- B-01: Don't. `tests/test_a.py::test_a`\n", "")
    assert "### Bug lessons misses B-01" in run(project)[1]
    edit(project, "ap_coder/sub/CLAUDE.md", "- None.\n", "- None.\n\n### Bug lessons\n- B-01: Don't.\n")
    assert "lists B-01, which docs/BUGS.md doesn't place in ap_coder/sub/" in run(project)[1]


def test_a_map_too_long_fails_but_its_bug_lessons_do_not_count(project):
    lessons = "".join(f"- B-{n:02d}: lesson.\n" for n in range(2, 120))
    edit(project, "ap_coder/CLAUDE.md", "- B-01:", lessons + "- B-01:")
    edit(project, "docs/BUGS.md", "#### B-01", "".join(f"#### B-{n:02d} · x · low\n- **Where:** `ap_coder/a.py:1`\n"
                                                   for n in range(2, 120)) + "#### B-01")  # fmt: skip
    assert run(project)[0] == 0
    edit(project, "ap_coder/CLAUDE.md", "- Nothing yet.\n", "".join(f"- note {n}\n" for n in range(70)))
    code, out = run(project)
    assert code == 1 and "the folder does too much, split it" in out


def test_a_map_past_fifty_lines_is_a_warning(project):
    """The root map's rule: each CLAUDE.md at 50 lines or fewer, bug lessons aside; past that, a warning."""
    edit(project, "ap_coder/CLAUDE.md", "- Nothing yet.\n", "".join(f"- note {n}\n" for n in range(33)))
    assert run(project) == (0, "map: 0 error(s), 0 warning(s)\n")  # 50 lines
    edit(project, "ap_coder/CLAUDE.md", "- note 0\n", "- note 0\n- one more\n")
    code, out = run(project)
    assert code == 0 and "ap_coder/CLAUDE.md: 51 lines: keep it short (about 50)" in out


def test_a_module_index_behind_the_imports_fails_until_it_is_made_again(project):
    edit(project, "ap_coder/a.py", "from .sub.b import helper", '"""Runs the core."""\nfrom .sub.b import helper')
    code, out = run(project)
    assert code == 1 and "ap_coder/MODULES.md is out of date" in out
    run(project, "--modules")
    assert run(project) == (0, "map: 0 error(s), 0 warning(s)\n")


def test_code_changed_without_its_map_is_a_warning(project):
    edit(project, "ap_coder/sub/b.py", "return a", "return a  # changed")
    code, out = run(project, "--changed", "HEAD")
    assert code == 0 and "ap_coder/sub: its code changed but its CLAUDE.md didn't" in out
    edit(project, "ap_coder/sub/CLAUDE.md", "helps.", "helps, changed.")
    assert "its code changed" not in run(project, "--changed", "HEAD")[1]


def test_a_base_git_cannot_compare_with_is_said_not_passed_over(project):
    code, out = run(project, "--changed", "no-such-branch")
    assert code == 0 and "git can't compare with no-such-branch" in out
    assert run(project, "--changed")[0] == 2


def test_the_hook_reminds_of_the_folder_map_and_never_blocks(project):
    event = json.dumps({"tool_input": {"file_path": str(project / "ap_coder" / "sub" / "b.py")}})
    done = subprocess.run([sys.executable, str(project / "scripts" / "check_map.py"), "--hook"], cwd=project,
                          input=event, capture_output=True, text=True)  # fmt: skip
    note = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert done.returncode == 0 and "ap_coder/sub/CLAUDE.md" in note and "Used by" in note
    quiet = subprocess.run([sys.executable, str(project / "scripts" / "check_map.py"), "--hook"], cwd=project,
                           input="not json", capture_output=True, text=True)  # fmt: skip
    assert quiet.returncode == 0 and quiet.stdout == ""
    listed = subprocess.run([sys.executable, str(project / "scripts" / "check_map.py"), "--hook"], cwd=project,
                            input="[1, 2]", capture_output=True, text=True)  # fmt: skip
    assert listed.returncode == 0 and listed.stdout == ""


def test_the_hook_reminds_of_an_edit_in_an_agents_worktree_too(project):
    inner = project / ".claude" / "worktrees" / "agent-1" / "ap_coder" / "sub" / "b.py"
    inner.parent.mkdir(parents=True)
    inner.write_text("x = 1\n")
    event = json.dumps({"tool_input": {"file_path": str(inner)}})
    done = subprocess.run([sys.executable, str(project / "scripts" / "check_map.py"), "--hook"], cwd=project,
                          input=event, capture_output=True, text=True)  # fmt: skip
    assert "ap_coder/sub/CLAUDE.md" in json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


def test_the_repository_map_is_clean():
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout
