"""``scripts/combine.py`` (D-325), on small git repositories made here: parallel branches merged into one, the logs'
conflicts settled with both sides kept and sorted, a number two branches both took given anew to the later branch's
entry and its mentions, a conflict in code left in progress for a person."""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

from tests.scripts import load_script

combine = load_script("combine", "ap_script_combine")

BUGS = "# Bug log\n\n## From B-10\n\n- **B-10** · first · low · `t1`\n- **B-11** · second · low · `t2`\n"
DECISIONS = "# Decisions\n\n## Working on the code\n\n- **D-4** · Four\n- **D-5** · **Five**\n  - Why: five.\n"
WHATS_NEW = "# What's new\n\nIntro.\n\n## Older\n\n- Old.\n"
TODO = "# To do\n\n- **One.**\n- **Two.**\n- **Three.**\n"


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.strip()


def put(files: dict[str, str]) -> None:
    for path, text in files.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(text.encode("utf-8"))


def text(path: str) -> str:
    return Path(path).read_bytes().decode("utf-8")


def branch(name: str, files: dict[str, str]) -> None:
    """A branch from main with these files written, in one commit."""
    git("checkout", "-q", "-b", name, "main")
    put(files)
    git("add", "-A")
    git("commit", "-q", "-m", name)
    git("checkout", "-q", "main")


def merging() -> bool:
    return subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True).returncode == 0


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository on main with the three logs, a to-do list and a module."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    for key, value in {"user.name": "t", "user.email": "t@example.invalid", "commit.gpgsign": "false",
                       "core.autocrlf": "false"}.items():  # fmt: skip
        git("config", key, value)
    put({"docs/BUGS.md": BUGS, "docs/decisions.md": DECISIONS, "docs/WHATS_NEW.md": WHATS_NEW, "docs/TODO.md": TODO,
         "app.py": "RATE = 1\n", "docs/notes.md": "B-12 is kept for later.\nNothing else.\n",
         "docs/GUIDE.md": "Step 1: start.\n"})  # fmt: skip
    git("add", "-A")
    git("commit", "-q", "-m", "Start")
    git("tag", "start")
    return tmp_path


def test_two_branches_adding_to_the_logs_are_merged_with_both_sides_kept_and_sorted(repo, capsys):
    b12, b13, b14 = (f"- **B-{n}** · {what} · low · `t{n}`\n" for n, what in [(12, "early"), (13, "late"),
                                                                              (14, "one both carry")])  # fmt: skip
    d6, d7 = "- **D-6** · **Six**\n  - Why: six.\n", "- **D-7** · **Seven**\n  - Why: seven.\n"
    reworded = BUGS.replace("second", "second, reworded")  # (B-14 as if cherry-picked to both)
    branch("late", {"docs/BUGS.md": reworded + b13 + b14, "docs/decisions.md": DECISIONS + d7,
                    "docs/WHATS_NEW.md": WHATS_NEW.replace("## Older", "## Late\n\n- Late.\n\n## Older"),
                    "docs/TODO.md": TODO.replace("- **One.**\n", "")})  # fmt: skip
    branch("early", {"docs/BUGS.md": BUGS + b12 + b14, "docs/decisions.md": DECISIONS + d6,
                     "docs/WHATS_NEW.md": WHATS_NEW.replace("## Older", "## Early\n\n- Early.\n\n## Older"),
                     "docs/TODO.md": TODO.replace("- **Two.**\n", "")})  # fmt: skip

    assert combine.main(["late", "early", "--onto", "main", "--no-check"]) == 0

    said = capsys.readouterr().out
    assert git("branch", "--show-current") == f"combined-{datetime.date.today():%Y-%m-%d}"
    assert text("docs/BUGS.md") == reworded + b12 + b13 + b14
    assert text("docs/decisions.md") == DECISIONS + d6 + d7
    both = "## Late\n\n- Late.\n\n## Early\n\n- Early.\n\n## Older"
    assert text("docs/WHATS_NEW.md") == WHATS_NEW.replace("## Older", both)
    assert text("docs/TODO.md") == "# To do\n\n- **Three.**\n"
    assert git("status", "--porcelain") == "" and not merging()
    assert "Fast-forwarded" in said and "docs/BUGS.md: the two sides changed different lines: kept both" in said
    message = git("log", "-1", "--format=%B")
    assert message.startswith("Merge early into combined-") and "Kept both sides of docs/WHATS_NEW.md." in message
    assert "Sorted the newest entries of docs/BUGS.md by number." in message
    assert git("log", "-1", "--format=%s", "main") == "Start"  # the branch it started from is left as it was


def test_a_number_both_branches_took_is_given_anew_to_the_later_branchs_entry_and_its_mentions(repo, capsys):
    first_bug, third_bug = "- **B-12** · the first's bug · low · `t1`\n", "- **B-13** · the third's · low · `t3`\n"
    second_bug = "- **B-12** · a quite different failure the second found in its reports (D-6) · high · `t9`\n"
    first_choice, second_choice = "- **D-6** · **The first's**\n", "- **D-6** · **The second's, unlike**\n  - B-12.\n"
    branch("first", {"docs/BUGS.md": BUGS + first_bug, "docs/decisions.md": DECISIONS + first_choice,
                     "first.py": "RATE = 1  # B-12: the first's fix\n"})  # fmt: skip
    branch("second", {"docs/BUGS.md": BUGS + second_bug, "docs/decisions.md": DECISIONS + second_choice,
                      "tests/test_second.py": '"""B-12: the second\'s test (D-6)."""\n',
                      "docs/notes.md": "B-12 is kept for later.\nSee B-12 and D-6.\n"})  # fmt: skip
    branch("third", {"docs/BUGS.md": BUGS + third_bug})

    assert combine.main(["first", "second", "third", "--onto", "main", "--no-check"]) == 0

    def anew(entry: str) -> str:
        return entry.replace("B-12", "B-14").replace("D-6", "D-7")

    # The second's B-12 is B-14: B-13 is the third's, merged after it.
    assert text("docs/BUGS.md") == BUGS + first_bug + third_bug + anew(second_bug)
    assert text("docs/decisions.md") == DECISIONS + first_choice + anew(second_choice)
    assert text("tests/test_second.py") == '"""B-14: the second\'s test (D-7)."""\n'
    assert text("first.py") == "RATE = 1  # B-12: the first's fix\n"
    assert text("docs/notes.md") == "B-12 is kept for later.\nSee B-14 and D-7.\n"  # only the line the second changed
    assert "B-12" in git("show", "second:docs/BUGS.md")  # the branch itself is left as it was
    said = capsys.readouterr().out
    assert "second's B-12 is B-14 now, D-6 is D-7 now: the branches merged before it use B-12, D-6" in said
    assert git("status", "--porcelain") == ""


def test_entry_files_two_branches_add_merge_with_plain_git(repo):
    """Each entry a file of its own (D-328): what two branches add merges with no conflict, where the same entries
    added to the end of the bug log conflict."""
    b12, b13 = "- **B-12** · one · low · `t1`\n", "- **B-13** · two · low · `t2`\n"
    branch("one", {"docs/bugs/B-12.md": b12, "docs/decisions/D-6.md": "- **D-6** · **Six.**\n  - **Why:** six.\n"})
    branch("two", {"docs/bugs/B-13.md": b13})
    branch("log-one", {"docs/BUGS.md": BUGS + b12})
    branch("log-two", {"docs/BUGS.md": BUGS + b13})

    git("merge", "-q", "--no-edit", "one")
    git("merge", "-q", "--no-edit", "two")
    assert (text("docs/bugs/B-12.md"), text("docs/bugs/B-13.md")) == (b12, b13) and git("status", "--porcelain") == ""

    git("checkout", "-q", "log-one")
    assert subprocess.run(["git", "merge", "-q", "--no-edit", "log-two"], capture_output=True).returncode == 1
    git("merge", "--abort")


def test_two_clones_that_took_one_number_have_the_later_branchs_file_renamed_with_its_mentions(repo, capsys):
    """Separate clones share no reserved numbers (scripts/new_entry.py): both may write B-12. The later branch's file
    is given the next number this clone has not reserved, the file renamed and its mentions with it."""
    here = {"docs/bugs/B-12.md": "- **B-12** · here's · low · `t1`\n",
            "docs/decisions/D-6.md": "- **D-6** · **Here.**\n"}  # fmt: skip
    there_bug = "- **B-12** · a quite different failure, found there (D-6) · high · `t9`\n"
    there_choice = "- **D-6** · **There, unlike.**\n  - **Why:** B-12.\n"
    branch("here", {**here, "here.py": "RATE = 1  # B-12\n"})
    branch("there", {"docs/bugs/B-12.md": there_bug, "docs/decisions/D-6.md": there_choice,
                     "tests/test_there.py": '"""B-12: there\'s test (D-6)."""\n'})  # fmt: skip
    reserved = Path(".git") / "ap-numbers"
    reserved.mkdir()
    (reserved / "B-13").write_text("a worktree of this clone\n", encoding="utf-8")

    assert combine.main(["here", "there", "--onto", "main", "--no-check"]) == 0

    assert text("docs/bugs/B-12.md") == here["docs/bugs/B-12.md"] and text("here.py") == "RATE = 1  # B-12\n"
    assert text("docs/bugs/B-14.md") == there_bug.replace("B-12", "B-14").replace("D-6", "D-7")
    assert text("docs/decisions/D-6.md") == here["docs/decisions/D-6.md"]
    assert text("docs/decisions/D-7.md") == there_choice.replace("B-12", "B-14").replace("D-6", "D-7")
    assert text("tests/test_there.py") == '"""B-14: there\'s test (D-7)."""\n'
    assert sorted(p.name for p in reserved.iterdir()) == ["B-13", "B-14", "D-7"]
    assert git("show", "there:docs/bugs/B-12.md") + "\n" == there_bug  # the branch itself is left as it was
    said = capsys.readouterr().out
    assert "there's B-12 is B-14 now, D-6 is D-7 now" in said and "docs/bugs/B-12.md (now docs/bugs/B-14.md)" in said
    assert git("status", "--porcelain") == ""


def test_an_entry_a_branch_made_before_the_cut_over_added_to_a_frozen_log_is_moved_to_its_file(repo, monkeypatch,
                                                                                               capsys):  # fmt: skip
    monkeypatch.setattr(combine, "FROZEN", {"B": 11, "D": 5})  # (the logs here stop at B-11 and D-5)
    old_choice = "- **D-6** · **The old one's.**\n  - **Why:** old.\n"
    branch("new", {"docs/bugs/B-12.md": "- **B-12** · new · low · `t1`\n"})
    branch("old", {"docs/BUGS.md": BUGS + "- **B-12** · old, unlike · high · `t2`\n",
                   "docs/decisions.md": DECISIONS + old_choice})  # fmt: skip

    assert combine.main(["new", "old", "--onto", "main", "--no-check"]) == 0

    assert (text("docs/BUGS.md"), text("docs/decisions.md")) == (BUGS, DECISIONS)
    assert text("docs/bugs/B-12.md") == "- **B-12** · new · low · `t1`\n"
    assert text("docs/bugs/B-13.md") == "- **B-13** · old, unlike · high · `t2`\n"  # (B-12 was new's)
    assert text("docs/decisions/D-6.md") == old_choice
    assert "Moved B-13 from docs/BUGS.md, which stops at B-11, into files of their own." in capsys.readouterr().out
    assert git("status", "--porcelain") == ""


def test_a_number_both_sides_gave_entries_alike_is_asked_about_with_both_entries_shown(repo, capsys):
    """Alike (ALIKE), they are taken for one entry edited on a side, so not numbered anew: a person says which."""
    edited = "- **B-12** · the totals were wrong on a credit note · low · `t1`\n"
    again = "- **B-12** · the totals were wrong on a credit memo · low · `t1`\n"
    asked = "B-12 on both sides, the words alike: the same entry edited, or two bugs?"
    branch("edited", {"docs/bugs/B-12.md": edited})
    branch("again", {"docs/bugs/B-12.md": again})
    branch("log-edited", {"docs/BUGS.md": BUGS + edited})
    branch("log-again", {"docs/BUGS.md": BUGS + again})

    for one, other, where in (("edited", "again", "docs/bugs/B-12.md"), ("log-edited", "log-again", "docs/BUGS.md")):
        assert combine.main([one, other, "--onto", "main", "--no-check"]) == 1
        said = capsys.readouterr().out
        assert f"{where}: {asked}" in said and f"      {edited.strip()}" in said and f"      {again.strip()}" in said
        git("merge", "--abort")
        git("checkout", "-q", "main")

    assert combine.main(["--trial", "--onto", "main", "edited", "again"]) == 0  # (docs only)
    assert f"docs, for a person: docs/bugs/B-12.md: {asked}" in capsys.readouterr().out


def test_a_trial_says_what_each_merge_and_each_two_would_conflict_on_and_changes_nothing(repo, capsys):
    branch("one", {"app.py": "RATE = 2\n", "docs/BUGS.md": BUGS + "- **B-13** · one · low · `t3`\n",
                   "docs/GUIDE.md": "Step 1: open it.\n"})  # fmt: skip
    branch("two", {"app.py": "RATE = 3\n", "docs/BUGS.md": BUGS + "- **B-12** · two · low · `t2`\n"})
    branch("three", {"docs/GUIDE.md": "Step 1: start it.\n"})
    Path("untracked.txt").write_text("left as it is\n", encoding="utf-8")
    state = ("for-each-ref", "status --porcelain=v2 --branch --untracked-files=all", "ls-files --stage")
    before = {command: git(*command.split()) for command in state}

    assert combine.main(["--trial", "--pairs", "one", "two", "three"]) == 1

    assert {command: git(*command.split()) for command in state} == before
    assert not Path(".git", "ap-numbers").exists() and not merging()
    said = capsys.readouterr().out
    assert "A trial: one, two, three merged onto HEAD in this order (nothing is changed)." in said
    assert "\none: no conflict.\n" in said
    assert ("\ntwo: 2 files would conflict.\n  code, for a person: app.py\n"
            "  docs, settled by combine.py: docs/BUGS.md\n") in said  # fmt: skip
    assert "\nthree: 1 file would conflict.\n  docs, for a person: docs/GUIDE.md: both sides changed the same" in said
    assert "\none + two: 2 files would conflict.\n  code, for a person: app.py\n" in said
    assert "\none + three: 1 file would conflict.\n" in said and "\ntwo + three: no conflict.\n" in said
    assert said.endswith("Code would conflict (1 file): combine.py would stop there for a person.\n")


def test_a_trial_of_docs_the_script_settles_exits_0_with_a_number_it_would_give_anew(repo, capsys):
    new = "## Older"
    branch("first", {"docs/bugs/B-12.md": "- **B-12** · first's · low · `t1`\n",
                     "docs/WHATS_NEW.md": WHATS_NEW.replace(new, "## First\n\n- First.\n\n" + new)})  # fmt: skip
    branch("second", {"docs/bugs/B-12.md": "- **B-12** · a quite different failure, second's · high · `t2`\n",
                      "docs/WHATS_NEW.md": WHATS_NEW.replace(new, "## Second\n\n- Second.\n\n" + new)})  # fmt: skip
    branches = git("branch", "--list")

    assert combine.main(["--trial", "--onto", "main", "first", "second"]) == 0

    said = capsys.readouterr().out
    assert "\nsecond: 1 file would conflict (B-12 would be B-13).\n" in said
    assert "  docs, settled by combine.py: docs/WHATS_NEW.md\n" in said
    assert said.endswith("Only docs would conflict, and combine.py would settle them all.\n")
    assert git("branch", "--list") == branches and not Path(".git", "ap-numbers").exists()  # nothing kept


def test_a_conflict_in_code_stops_with_the_merge_left_for_a_person(repo, capsys):
    branch("one", {"app.py": "RATE = 2\n", "docs/BUGS.md": BUGS + "- **B-13** · one · low · `t3`\n",
                   "docs/GUIDE.md": "Step 1: open it.\n"})  # fmt: skip
    branch("two", {"app.py": "RATE = 3\n", "docs/BUGS.md": BUGS + "- **B-12** · two · low · `t2`\n"})
    branch("three", {"docs/GUIDE.md": "Step 1: start it.\n"})
    branch("four", {"later.py": "\n"})

    assert combine.main(["one", "two", "four", "--no-check"]) == 1

    said = capsys.readouterr().out
    assert "app.py: a conflict in code" in said and "Not merged yet: four" in said
    assert "python scripts/combine.py four" in said
    assert merging() and "<<<<<<<" in text("app.py")
    assert git("diff", "--name-only", "--diff-filter=U") == "app.py"  # the bug log settled, sorted and added
    assert text("docs/BUGS.md") == BUGS + "- **B-12** · two · low · `t2`\n- **B-13** · one · low · `t3`\n"
    assert "Kept both sides of docs/BUGS.md." in text(".git/MERGE_MSG")
    assert not Path("later.py").exists()

    git("merge", "--abort")
    assert combine.main(["one", "three", "--onto", "start", "--no-check"]) == 1  # a doc's line changed on both sides
    assert "docs/GUIDE.md: both sides changed the same lines" in capsys.readouterr().out


def test_a_log_whose_merge_base_git_could_not_make_is_left_to_a_person(repo, capsys):
    """Branches that merged each other (criss-cross) have two merge bases; git merges those into one, with its own
    markers where they conflict, so what the base was there is not known."""
    b = {n: f"- **B-{n}** · bug {n} · low · `t{n}`\n" for n in (12, 13, 14, 15)}
    branch("p", {"docs/BUGS.md": BUGS + b[12]})
    branch("q", {"docs/BUGS.md": BUGS + b[13]})
    tips = {name: git("rev-parse", name) for name in ("p", "q")}
    for here, there, order, last in (("p", "q", (12, 13), 14), ("q", "p", (13, 12), 15)):
        git("checkout", "-q", here)
        subprocess.run(["git", "merge", "-q", "--no-commit", tips[there]], capture_output=True)
        put({"docs/BUGS.md": BUGS + "".join(b[n] for n in order)})
        git("commit", "-qam", f"{there} into {here}")
        put({"docs/BUGS.md": BUGS + "".join(b[n] for n in order) + b[last]})
        git("commit", "-qam", f"B-{last}")

    assert combine.main(["q", "--onto", "p", "--no-check"]) == 1

    said = capsys.readouterr().out
    assert "docs/BUGS.md: the branches have more than one merge base, and git's merge of those conflicts here" in said


def test_the_checks_run_on_the_files_the_merges_changed(repo, monkeypatch, capsys):
    branch("one", {"app.py": "RATE = 2\n", "docs/BUGS.md": BUGS + "- **B-12** · one · low · `t2`\n"})
    ran = []
    monkeypatch.setattr(combine, "run_step", lambda command: ran.append(command[1:]) or True)

    assert combine.main(["one", "--onto", "main"]) == 0

    assert ran == [["-m", "pytest", "-q", "-m", "", "tests/meta"], ["scripts/check.py", "app.py", "docs/BUGS.md"]]
    monkeypatch.setattr(combine, "run_step", lambda command: False)
    git("checkout", "-q", "-b", "again", "main")
    assert combine.main(["one"]) == 1
    assert "Checks: FAILED" in capsys.readouterr().out


def test_nothing_is_done_with_changes_not_committed_or_a_branch_not_found(repo, capsys):
    branch("one", {"app.py": "RATE = 2\n"})
    assert combine.main(["one", "nowhere", "--no-check"]) == 2
    assert "No branch or commit named nowhere: nothing done." in capsys.readouterr().out
    Path("app.py").write_bytes(b"RATE = 5\n")
    assert combine.main(["one", "--no-check"]) == 2
    assert "changes not committed" in capsys.readouterr().out
    assert git("log", "-1", "--format=%s") == "Start"
