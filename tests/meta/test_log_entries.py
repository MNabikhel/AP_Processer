"""The bug and decision logs (D-328): after B-2997 and D-327, each entry is a file of its own (``docs/bugs/``,
``docs/decisions/``) numbered when written by ``scripts/new_entry.py``; docs/BUGS.md and docs/decisions.md are frozen;
each number has one entry, and the lookup the docs give finds it."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path

import pytest

from tests.conftest import ROOT
from tests.scripts import load_script

new_entry = load_script("new_entry", "ap_script_new_entry")

# The frozen logs and the archive: how many entries each has, and its last. Nothing is added to them any more.
FROZEN = {"docs/BUGS.md": ("B", 1019, 2997), "docs/decisions.md": ("D", 292, 327),
          "docs/archive/bugs-2026-10.md": ("B", 892, 1441),
          "docs/archive/decisions-2026-10.md": ("D", 115, 124)}  # fmt: skip
# Numbers the code names that are no entry's.
NO_ENTRY = {"B-0": "an invoice number in a test (tests/erp/test_jde_sync_scale.py)",
            "B-989": "the end of a range of bugs (B-970 to B-989) in a docstring",
            "B-2187": "a bug named in a test's docstring, never logged (tests/core/test_schema.py)"}  # fmt: skip
MENTION = re.compile(r"(?<![A-Za-z0-9_])([BD])-(\d+)(?!\d)")
CODE = ("ap_coder", "tests", "scripts", ".github")
TEXT = {".py", ".html", ".js", ".css", ".sql", ".json", ".txt", ".yml", ".md", ".bat", ".command", ".toml", ".svg"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def entry_files(kind: str) -> list[Path]:
    return sorted(p for p in (ROOT / new_entry.FOLDERS[kind]).iterdir() if p.name != "README.md")


def all_entries(kind: str) -> dict[str, list[int]]:
    """Each place an entry of this kind can be (a frozen log, the archive, a file), and the numbers of its entries."""
    found = {path: new_entry.headers(read(ROOT / path), k) for path, (k, _, _) in FROZEN.items() if k == kind}
    return found | {p.relative_to(ROOT).as_posix(): new_entry.headers(read(p), kind) for p in entry_files(kind)}


# --- The entries in this checkout -----------------------------------------------------------------------------------


def test_each_entry_file_holds_the_one_entry_it_is_named_after():
    """``B-NNNN.md`` is the line ``- **B-NNNN** · ...``; ``D-NNN.md`` is ``- **D-NNN** · **...**`` and its lines,
    indented two spaces (as at the end of docs/decisions.md; at most 120 characters: test_agent_friendly.py). Nothing
    else is in the folders but their README."""
    bad = []
    for kind in new_entry.FOLDERS:
        for path in entry_files(kind):
            name = f"{new_entry.FOLDERS[kind]}/{path.name}"
            if not ((m := new_entry.FILE_NAME.fullmatch(path.name)) and m[1] == kind and not m[2].startswith("0")):
                bad.append(f"{name}: only entries here, each named {kind}-NNN.md")
                continue
            text, number = read(path), int(m[2])
            lines = text.split("\n")
            if not lines[0].startswith(f"- **{kind}-{number}** · "):
                bad.append(f"{name}: its first line starts '- **{kind}-{number}** · ' (python scripts/new_entry.py)")
            if not text.endswith("\n") or text.endswith("\n\n") or (kind == "B" and len(lines) != 2):
                bad.append(f"{name}: the entry, {'one line' if kind == 'B' else 'its lines'} and one line end")
            bad += [f"{name}:{n}: not indented two spaces (each line after the first is)"
                    for n, line in enumerate(lines[1:-1], 2) if not line.startswith("  ")]  # fmt: skip
    assert not bad, "\n".join(bad)


def test_entry_files_are_numbered_after_the_frozen_logs():
    """A number below the freeze with no entry was never used: it is not taken now (new_entry.py takes the next)."""
    early = [p.name for kind in new_entry.FOLDERS for p in entry_files(kind)
             if (m := new_entry.FILE_NAME.fullmatch(p.name)) and int(m[2]) <= new_entry.FROZEN[kind]]  # fmt: skip
    assert not early, early


def test_the_frozen_logs_take_no_new_entry():
    """docs/BUGS.md ends at B-2997 and docs/decisions.md at D-327, as at the cut-over (D-328); an entry already there
    may still be corrected, or marked "replaced by D-NNN"."""
    for path, (kind, count, last) in FROZEN.items():
        text = read(ROOT / path)
        numbers, lines = new_entry.headers(text, kind), text.rstrip("\n").split("\n")
        tail = next(i for i in range(len(lines) - 1, -1, -1) if not lines[i].startswith("  "))
        said = f"{path} is frozen: a new entry is a file of its own (python scripts/new_entry.py {kind} ..., D-328)"
        assert (len(numbers), max(numbers)) == (count, last), said
        assert lines[tail].startswith(f"- **{kind}-{last}** · "), f"{said}; nothing after its last entry"
    assert {kind: last for path, (kind, _, last) in FROZEN.items() if "archive" not in path} == new_entry.FROZEN


def test_no_number_has_two_entries():
    """Within a log, and between the files and the logs. (decisions.md has D-01 to D-124 in a line each, the archive
    in full: the one entry, twice written.)"""
    bad = []
    for kind in new_entry.FOLDERS:
        places = all_entries(kind)
        for path, numbers in places.items():
            bad += [f"{path}: {kind}-{n} {k} times" for n, k in Counter(numbers).items() if k > 1]
        logs = {n for path, numbers in places.items() if path in FROZEN for n in numbers}
        bad += [f"{path}: {kind}-{n} has an entry in a log already" for path, numbers in places.items()
                if path not in FROZEN for n in numbers if n in logs]  # fmt: skip
    assert not bad, bad


def test_every_number_named_in_the_code_has_an_entry():
    found: dict[str, set[str]] = {}
    for folder in CODE:
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in TEXT and path.is_file() and "__pycache__" not in path.parts:
                for kind, n in MENTION.findall(path.read_text(encoding="utf-8", errors="replace")):
                    found.setdefault(f"{kind}-{int(n)}", set()).add(path.relative_to(ROOT).as_posix())
    known = {f"{kind}-{n}" for kind in new_entry.FOLDERS for numbers in all_entries(kind).values() for n in numbers}
    missing = {number: sorted(paths)[:3] for number, paths in found.items() if number not in known | set(NO_ENTRY)}
    assert not missing, f"named in the code but no entry has the number: {missing}"
    assert set(NO_ENTRY) <= set(found) - known, "an allowance no longer needed: remove it from NO_ENTRY"


@pytest.mark.skipif(shutil.which("grep") is None, reason="no grep here")
def test_the_lookup_the_docs_give_finds_an_archived_a_frozen_and_a_new_entry(tmp_path):
    """CLAUDE.md, the tops of the two logs and .ignore give one command, which finds the entry itself wherever it is
    (the one they gave before, ``grep -n "B-1234 " docs/archive/*.md``, found no entry: they end ``B-1234**``)."""
    given = {name: re.findall(r'`?(grep -rnw "[BD]-\d+" [^`\n]+)', read(ROOT / name))
             for name in ("CLAUDE.md", "docs/BUGS.md", "docs/decisions.md", ".ignore")}  # fmt: skip
    commands = {re.sub(r"[BD]-\d+", "{}", command) for found in given.values() for command in found}
    assert all(given.values()) and len(commands) == 1, given
    command = commands.pop()
    new = f"B-{new_entry.FROZEN['B'] + 1}"  # (a bug's file, made here)
    (tmp_path / "docs" / "bugs").mkdir(parents=True)
    (tmp_path / "docs" / "bugs" / f"{new}.md").write_text(f"- **{new}** · a bug's file · low · `t`\n", encoding="utf-8")
    looked_up = {"B-1234": (ROOT, "docs/archive/bugs-2026-10.md"), "B-2997": (ROOT, "docs/BUGS.md"),
                 new: (tmp_path, f"docs/bugs/{new}.md"), "D-34": (ROOT, "docs/archive/decisions-2026-10.md"),
                 "D-200": (ROOT, "docs/decisions.md"), "D-328": (ROOT, "docs/decisions/D-328.md")}  # fmt: skip
    for number, (folder, where) in looked_up.items():
        out = subprocess.run(shlex.split(command.format(number)), cwd=folder, capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout  # fmt: skip
        assert re.search(rf"^{re.escape(where)}:\d+:- \*\*{number}\*\* · ", out, re.M), (number, out[:500])


# --- scripts/new_entry.py, on small git repositories made here ------------------------------------------------------


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.strip()


def put(files: dict[str, str], cwd: Path) -> None:
    for path, text in files.items():
        (cwd / path).parent.mkdir(parents=True, exist_ok=True)
        (cwd / path).write_bytes(text.encode("utf-8"))


def commit(files: dict[str, str], cwd: Path, message: str = "a change") -> None:
    put(files, cwd)
    git("add", "-A", cwd=cwd)
    git("commit", "-q", "-m", message, cwd=cwd)


# (Numbers an old entry has, as the code may name only numbers that have an entry.)
BUGS = "# Bug log\n\n- **B-10** · one · low · `t1`\n- **B-11** · the last · low · `t2`\n"
DECISIONS = "# Decisions\n\n- **D-5** · **The last.**\n  - **Why:** it was.\n"


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A clone on main with the frozen logs, the archive and an entry file."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)
    here = tmp_path / "clone"
    here.mkdir()
    monkeypatch.chdir(here)
    git("init", "-q", "-b", "main")
    for key, value in {"user.name": "t", "user.email": "t@example.invalid", "commit.gpgsign": "false",
                       "core.autocrlf": "false"}.items():  # fmt: skip
        git("config", key, value)
    commit({"docs/BUGS.md": BUGS, "docs/decisions.md": DECISIONS, "docs/decisions/D-6.md": "- **D-6** · **X.**\n",
            "docs/archive/bugs-2026-10.md": "# Bug log\n\n- **B-01** · first · low · `t`\n"},
           here, "Start")  # fmt: skip
    return here


def write(args: list[str], capsys) -> tuple[int, str, str]:
    code = new_entry.main(args)
    said = capsys.readouterr()
    return code, said.out, said.err


def test_the_number_is_after_every_entry_any_branch_or_worktree_of_the_clone_has(clone, tmp_path, monkeypatch, capsys):
    git("checkout", "-q", "-b", "other")
    commit({"docs/bugs/B-12.md": "- **B-12** · other's · low · `t`\n"}, clone)
    git("checkout", "-q", "-b", "old", "main")  # a branch made before the cut-over, as last fetched
    commit({"docs/BUGS.md": BUGS + "- **B-14** · added to the log · low · `t`\n"}, clone)
    git("update-ref", "refs/remotes/origin/old", "old")
    git("checkout", "-q", "main")
    git("branch", "-q", "-D", "old")
    worktree = tmp_path / "worktree"
    git("worktree", "add", "-q", "-b", "helper", str(worktree), "main")

    monkeypatch.chdir(worktree)
    code, out, err = write(["B", "the helper's · high · `tests/x.py::test_y`"], capsys)
    assert (code, out) == (0, "B-15\n") and "docs/bugs/B-15.md" in err
    assert read(worktree / "docs/bugs/B-15.md") == "- **B-15** · the helper's · high · `tests/x.py::test_y`\n"
    assert git("diff", "--cached", "--name-only", cwd=worktree) == "docs/bugs/B-15.md"  # added, not committed

    monkeypatch.chdir(clone)
    assert write(["b", "main's · low · `tests/x.py::test_z`"], capsys)[:2] == (0, "B-16\n")  # (the other reserved)
    assert read(clone / "docs/bugs/B-16.md") == "- **B-16** · main's · low · `tests/x.py::test_z`\n"
    assert sorted(p.name for p in (clone / ".git" / "ap-numbers").iterdir()) == ["B-15", "B-16"]
    assert write(["D", "**The next choice.**"], capsys)[:2] == (0, "D-7\n")


def test_runs_at_once_in_worktrees_of_one_clone_never_take_one_number(clone, tmp_path):
    worktrees = [tmp_path / f"w{k}" for k in range(4)]
    for k, worktree in enumerate(worktrees):
        git("worktree", "add", "-q", "-b", f"w{k}", str(worktree), "main")
    script = str(ROOT / "scripts" / "new_entry.py")
    runs = [subprocess.Popen([sys.executable, script, "B", f"bug {k} · low · test {k}"], cwd=w, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True) for k, w in enumerate(worktrees)]  # fmt: skip
    numbers = [run.communicate()[0].strip() for run in runs]
    assert sorted(numbers) == ["B-12", "B-13", "B-14", "B-15"], numbers
    for worktree, number in zip(worktrees, numbers, strict=True):
        assert [p.name for p in (worktree / "docs" / "bugs").iterdir()] == [f"{number}.md"]

    taken: list[int] = []
    threads = [threading.Thread(target=lambda: taken.append(new_entry.reserve("D", 400))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(taken) == list(range(401, 409))


def test_a_decision_comes_from_a_file_or_stdin_and_a_text_that_would_break_a_rule_is_refused(clone, capsys):
    decision = "**One choice** (B-11).\n  - **Why:** because.\n  - *Not chosen:* the other.\n"
    Path("entry.md").write_text(decision, encoding="utf-8")
    assert write(["D", "--file", "entry.md"], capsys)[:2] == (0, "D-7\n")
    assert read(clone / "docs/decisions/D-7.md") == "- **D-7** · " + decision
    script = str(ROOT / "scripts" / "new_entry.py")
    done = subprocess.run([sys.executable, script, "D"], input=decision, capture_output=True, text=True)
    assert done.stdout == "D-8\n" and read(clone / "docs/decisions/D-8.md") == "- **D-8** · " + decision

    refused = {("B", "two\nlines"): "A bug is one line", ("B", "- **B-12** · x"): "without its start",
               ("D", "**A.**\n- **Why:** not indented."): "Line 2 is not indented",
               ("D", "**A.**\n  - " + "long " * 30): "Line 2 is 153 characters",
               ("D", "  \n"): "No text given",
               # B-3000: what bash leaves of "what broke · low · `tests/x.py::test_y`" (the backticks run), and more
               ("B", "what broke · low ·"): "in single quotes", ("B", "what broke · low"): "has 2 parts",
               ("B", "what broke · whenever · `tests/x.py::test_y`"): "The severity",
               ("B", "what broke · low · fixed"): "names the test"}  # fmt: skip
    for (kind, text), why in refused.items():
        code, out, err = write([kind, text], capsys)
        assert (code, out) == (1, "") and why in err, err
    assert sorted(p.name for p in (clone / ".git" / "ap-numbers").iterdir()) == ["D-7", "D-8"]  # none taken
    assert not (clone / "docs" / "bugs").exists()


def test_words_on_stdin_are_read_as_utf_8_whatever_the_code_page(clone):
    """B-3001: stdin was read in the code page (Windows before Python 3.15: cp1252), so a decision piped from a UTF-8
    file was written garbled ("QuÃ©bec"), or stopped at a byte cp1252 has no character for (” ends in 0x9D)."""
    decision = "**Québec’s rate · “the one rule”** (D-5).\n  - **Why:** a café’s receipt.\n"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}  # (Windows' code page, as in tests/launcher/)
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "new_entry.py"), "D"], input=decision.encode(),
                          capture_output=True, env=env)  # fmt: skip
    assert done.stdout == b"D-7\n", done.stderr.decode("utf-8", "replace")
    assert read(clone / "docs/decisions/D-7.md") == "- **D-7** · " + decision


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="bash's own quoting")
def test_words_in_single_quotes_keep_their_backticks_and_dollars_and_in_double_quotes_are_refused(clone):
    """B-3000: the form the docs gave, in double quotes, let bash run the test in backticks and expand $1: the line
    was written without its test, or "$1,234.56" as ",234.56"."""
    words = "A total of $1,234.56 was read as $12.34 · high · `tests/x/test_y.py::test_z`"
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'scripts' / 'new_entry.py'))} B"
    done = subprocess.run(["bash", "-c", f"{command} '{words}'"], capture_output=True, text=True, encoding="utf-8")
    assert done.stdout == "B-12\n" and read(clone / "docs/bugs/B-12.md") == f"- **B-12** · {words}\n"
    done = subprocess.run(["bash", "-c", f'{command} "{words}"'], capture_output=True, text=True, encoding="utf-8")
    assert (done.returncode, done.stdout) == (1, "") and "in single quotes" in done.stderr, done.stderr
    assert not (clone / "docs" / "bugs" / "B-13.md").exists()
