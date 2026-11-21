"""Combine parallel branches into one (D-325): each branch given is merged in order, the logs' conflicts settled, a
bug or decision number two branches both took given anew to the later branch's entry, and the result checked.

    python scripts/combine.py BRANCH [BRANCH ...]    merge them into the current branch
    --onto REF                                       into a new branch from REF instead (combined-<date>)
    --no-check                                       skip the checks at the end
    --trial [--pairs] BRANCH [BRANCH ...]            only say what each merge (and each two) would conflict on


A conflict in a doc under docs/ is settled when the two sides changed different lines: both added entries at the end
of BUGS.md or decisions.md (both kept, then sorted by number), both added a section to WHATS_NEW.md (both kept),
each took a different item off TODO.md. Two changes to the same line, and any conflict in code, stop the script with
the merge left in progress and the files that need a person listed. Since D-328 each new entry is a file of its own
(docs/bugs/B-NNNN.md, docs/decisions/D-NNN.md), which merges with no conflict; an entry a branch made before then
added to BUGS.md or decisions.md, past where they stop, is moved into its file. A B- or D- number that the branch
being merged and the ones merged before it each gave to a new entry of their own (separate clones: the worktrees of
one clone are kept apart by scripts/new_entry.py) is given anew to the branch's entry (the next number no branch has
and this clone did not reserve), its file renamed, with its mentions in the lines that branch changed (code, tests,
docs), in a commit on top of the branch; the branch itself is left as it was. Each merge is committed; nothing is
pushed. The check: ``python scripts/check.py --rules`` on the files the merges changed: their tests, and the rules
that check the whole tree (its ``RULES``) whatever changed, run once as check.py runs them. Exit code 0 when all is
merged and checked, 1 when something needs a person, 2 when nothing was done.

A trial (``--trial``) changes no worktree, index or ref: each merge is made by ``git merge-tree --write-tree`` (git
2.38 or newer) as objects only, a number given anew and the docs the script settles included, and the next branch is
tried on top of it. For each merge (and with ``--pairs`` each two branches merged with each other) it prints the files
that would conflict: code, which a person settles, and docs, each said to be settled by the script or not, and why.
Exit code 1 when code would conflict, 0 when only docs (or nothing) would, 2 when nothing could be tried.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import itertools
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ is no package: new_entry.py is beside this one
import new_entry  # noqa: E402  (standard library only)
from new_entry import FILE_NAME, FOLDERS, FROZEN, LOGS  # noqa: E402  (the logs, frozen, and the entries' folders)

MENTION = re.compile(r"(?<![A-Za-z0-9_])([BD])-(\d+)(?!\d)")
ALIKE = 0.6  # two entries of one number this alike (words) are one entry, edited on a side: not two that clash
KINDS = {"B": "bugs", "D": "decisions", "N": "notes"}  # (each kind of new_entry.LOGS)


class Stop(Exception):
    """What the script can't settle: said, and left for a person."""


def run_git(args: list[str], data: bytes | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], input=data, env=env, capture_output=True)


def git(*args: str, data: bytes | None = None, env: dict | None = None) -> str:
    done = run_git(list(args), data, env)
    if done.returncode:
        said = (done.stdout + done.stderr).decode("utf-8", "replace").strip()
        raise Stop(f"git {' '.join(args)} failed:\n{said}")
    return done.stdout.decode("utf-8", "replace").strip()


def show(rev: str, path: str) -> str:
    """The file at a commit ("" where it has none)."""
    done = run_git(["show", f"{rev}:{path}"])
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else ""


def read(path: str) -> str:
    return Path(path).read_bytes().decode("utf-8")  # bytes: its line endings kept as they are


def write(path: str, text: str) -> None:
    Path(path).write_bytes(text.encode("utf-8"))
    git("add", "--", path)


# The logs: an entry is a line "- **B-12** · ..." and the indented lines under it, in a frozen log or a file of its own.


def entries(text: str, prefix: str) -> list[tuple[int, str]]:
    head = re.compile(rf"- \*\*{prefix}-(\d+)\*\*")
    found: list[tuple[int, list[str]]] = []
    current = None
    for line in text.split("\n"):
        if m := head.match(line):
            current = [line]
            found.append((int(m[1]), current))
        elif current is not None and line.startswith("  "):
            current.append(line)
        else:
            current = None
    return [(n, "\n".join(lines)) for n, lines in found]


def numbers(text: str, prefix: str) -> set[int]:
    return {n for n, _ in entries(text, prefix)}


def doubled(text: str, prefix: str) -> set[int]:
    """The numbers two different entries have."""
    seen: dict[int, set[str]] = {}
    for n, entry in entries(text, prefix):
        seen.setdefault(n, set()).add(entry)
    return {n for n, texts in seen.items() if len(texts) > 1}


def entry_files(rev: str, prefix: str) -> dict[int, str]:
    """The entry files of this kind at a commit: each one's number and path."""
    paths = git("ls-tree", "--name-only", rev, "--", f"{FOLDERS[prefix]}/").splitlines()
    return {int(m[2]): path for path in paths if (m := FILE_NAME.fullmatch(path.rpartition("/")[2])) and m[1] == prefix}


def entry_number(path: str) -> tuple[str, int] | None:
    """The kind and number of the entry a path is the file of (None for any other path)."""
    folder, _, name = path.rpartition("/")
    if (m := FILE_NAME.fullmatch(name)) and FOLDERS[m[1]] == folder:
        return m[1], int(m[2])
    return None


def renamed(path: str, given: dict[tuple[str, int], int]) -> str:
    """An entry file's path under the number given anew to it (else the path as it is)."""
    if (number := entry_number(path)) in given:
        return f"{FOLDERS[number[0]]}/{number[0]}-{given[number]}.md"
    return path


def without(text: str, prefix: str, gone: set[int]) -> str:
    """A log without the entries of these numbers (each its line and the indented lines under it)."""
    head = re.compile(rf"- \*\*{prefix}-(\d+)\*\*")
    kept, dropping = [], False
    for line in text.split("\n"):
        if m := head.match(line):
            dropping = int(m[1]) in gone
        elif not line.startswith("  "):
            dropping = False
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


def alike(a: str, b: str) -> bool:
    """Whether two entries of one number are one written twice: their words after the number."""
    a_words, b_words = a.split()[3:], b.split()[3:]  # (after "- **B-12** ·")
    return difflib.SequenceMatcher(None, a_words, b_words, autojunk=False).ratio() >= ALIKE


def alike_said(prefix: str, n: int, versions: list[str]) -> str:
    """What a person is asked of a number both sides gave an entry, the words alike: both entries, as written."""
    shown = "".join(f"\n      {line}" for version in versions for line in version.strip().split("\n"))
    return (
        f"{prefix}-{n} on both sides, the words alike: the same entry edited, or two {KINDS[prefix]}? Keep the "
        f"right one, or give the other the next number (python scripts/new_entry.py {prefix}).{shown}"
    )


def clashes(base: str, ours: str, theirs: str, prefix: str) -> list[int]:
    """The numbers both sides gave to a new entry (a number the merge base has not), each a different entry: in its
    file, or in the frozen log (a branch made before D-328)."""
    before = numbers(show(base, LOGS[prefix]), prefix) | entry_files(base, prefix).keys()

    def new(rev: str) -> dict[int, list[str]]:
        found: dict[int, list[str]] = {}
        for n, entry in entries(show(rev, LOGS[prefix]), prefix):
            if n not in before:
                found.setdefault(n, []).append(entry)
        for n, path in entry_files(rev, prefix).items():
            if n not in before:
                found.setdefault(n, []).append(show(rev, path).strip())
        return found

    mine, yours = new(ours), new(theirs)
    return sorted(n for n in mine.keys() & yours.keys() if not any(alike(a, b) for a in mine[n] for b in yours[n]))


def highest(rev: str, prefix: str) -> int:
    """The highest number of this kind its log mentions or an entry file has (the archive's are older)."""
    said = [int(n) for kind, n in MENTION.findall(show(rev, LOGS[prefix])) if kind == prefix]
    return max([*said, *entry_files(rev, prefix)], default=0)


def sort_newest(text: str, prefix: str, start: int) -> str | None:
    """The log with its entries from the first numbered ``start`` or more sorted by number, an entry there twice
    word for word kept once; None when something other than entries comes after them."""
    lines = text.split("\n")
    head = re.compile(rf"- \*\*{prefix}-(\d+)\*\*")
    first = next((i for i, line in enumerate(lines) if (m := head.match(line)) and int(m[1]) >= start), None)
    if first is None:
        return text
    found: list[tuple[int, list[str]]] = []
    end = len(lines)
    for i in range(first, len(lines)):
        if m := head.match(lines[i]):
            found.append((int(m[1]), [lines[i]]))
        elif lines[i].startswith("  "):
            found[-1][1].append(lines[i])
        elif not lines[i].strip():
            end = i
            break
        else:
            return None
    if any(line.strip() for line in lines[end:]):
        return None
    kept = list({"\n".join(entry): (n, entry) for n, entry in found}.values())  # (in order, the first of each kept)
    kept.sort(key=lambda e: e[0])
    return "\n".join(lines[:first] + [line for _, entry in kept for line in entry] + lines[end:])


# Conflicts in a doc: git's markers with the merge base between them (merge.conflictstyle=diff3).


def _changes(base: list[str], side: list[str]) -> list[tuple[int, int, list[str]]]:
    """What a side changed of the base: (from, to, the lines put there); an addition has from == to."""
    matcher = difflib.SequenceMatcher(None, base, side, autojunk=False)
    return [(i1, i2, side[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def merge_hunk(base: list[str], ours: list[str], theirs: list[str]) -> list[str] | None:
    """A conflict's lines with both sides' changes, when no line of the base was changed by both (None if one was).
    What both sides add at one place is kept, ours first; a line theirs adds that ours added too, once."""
    mine = _changes(base, ours)
    yours = [c for c in _changes(base, theirs) if c not in mine]  # the same change on both sides: once
    for a1, a2, _ in mine:
        for b1, b2, _ in yours:
            if max(a1, b1) < min(a2, b2) or a1 < b1 < a2 or b1 < a1 < b2:
                return None
    added = {line for _, _, lines in mine for line in lines if line.strip()}
    changes = [(i1, i2, lines, 0) for i1, i2, lines in mine]
    changes += [(i1, i2, [x for x in lines if i1 < i2 or not (x.strip() and x in added)], 1) for i1, i2, lines in yours]
    out: list[str] = []
    at = 0
    for i1, i2, lines, _ in sorted(changes, key=lambda c: (c[0], c[0] < c[1], c[3])):
        out += base[at:i1] + lines
        at = max(at, i2)
    return out + base[at:]


def settle(text: str) -> str | None:
    """A doc's text with each conflict settled by ``merge_hunk``; None when one can't be."""
    out: list[str] = []
    hunk: list[list[str] | None] | None = None  # ours, the base, theirs
    part = 0
    for line in text.split("\n"):
        mark = line.rstrip("\r")
        if hunk is None:
            if mark.startswith("<<<<<<<"):
                hunk, part = [[], None, []], 0
            else:
                out.append(line)
        elif mark.startswith("|||||||") and part == 0:
            hunk[1], part = [], 1
        elif mark == "=======" and part in (0, 1):
            part = 2
        elif mark.startswith(">>>>>>>") and part == 2:
            merged = None if hunk[1] is None else merge_hunk(hunk[1], hunk[0], hunk[2])
            if merged is None:
                return None
            out += merged
            hunk = None
        else:
            hunk[part].append(line)
    return None if hunk is not None else "\n".join(out)


def unsettled(path: str, stages: set[str], text: str | None, sides: list[str]) -> str | None:
    """Why a conflicted file needs a person (None when the script settles it): ``stages`` git's stages of it (1 the
    base, 2 and 3 the sides), ``text`` the file with git's markers (None when not UTF-8), ``sides`` an entry file's two
    versions when both sides added it."""
    if not (path.startswith("docs/") and path.endswith(".md")):
        return "a conflict in code (or a file outside docs/), never settled by the script"
    if (number := entry_number(path)) and stages == {"2", "3"}:  # (two unlike are given two numbers before the merge)
        return alike_said(*number, sides)
    if stages != {"1", "2", "3"}:
        return "added on both sides, or deleted on one"
    if text is None:
        return "not text in UTF-8"
    if re.search(r"^(<{8}|>{8}|\|{8})", text, re.M):  # git's markers in the base it made of two (criss-cross)
        return "the branches have more than one merge base, and git's merge of those conflicts here"
    if settle(text) is None:
        return "both sides changed the same lines"
    return None


def settle_file(path: str) -> str | None:
    """Settles a conflicted file the script may settle; else why a person is needed."""
    stages = {line.split()[2] for line in git("ls-files", "-u", "--", path).splitlines()}
    try:
        text = read(path)
    except UnicodeDecodeError:
        text = None
    sides = [show(f":{stage}", path) for stage in ("2", "3")] if entry_number(path) else []
    why = unsettled(path, stages, text, sides)
    if why is None:
        write(path, settle(text))
    return why


# A branch's numbers given anew: in the lines it changed since the merge base, on a commit of its own.


def changed_lines(base: str, tip: str) -> dict[str, set[int]]:
    """The lines (from 1) of each file the branch added or changed since the merge base."""
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", base, tip)
    found: dict[str, set[int]] = {}
    path, header = None, False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and (m := re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)):
            header = False
            if path:
                start = int(m[1])
                found.setdefault(path, set()).update(range(start, start + int(m[2] or 1)))
    return found


def renumbered(tip: str, base: str, given: dict[tuple[str, int], int], message: str) -> tuple[str, list[str]]:
    """A commit on top of the branch with its numbers given anew in the lines it changed: the commit and the files."""

    def anew(m: re.Match) -> str:
        return f"{m[1]}-{given.get((m[1], int(m[2])), m[2])}"

    files = []
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(folder) / "index")}
        git("read-tree", tip, env=env)
        for path, lines in sorted(changed_lines(base, tip).items()):
            try:
                text = run_git(["show", f"{tip}:{path}"]).stdout.decode("utf-8")
            except UnicodeDecodeError:
                continue  # not text
            old = text.split("\n")
            new = [MENTION.sub(anew, line) if n in lines else line for n, line in enumerate(old, 1)]
            moved = renamed(path, given)
            if new != old or moved != path:
                blob = git("hash-object", "-w", "--stdin", data="\n".join(new).encode("utf-8"))
                mode = git("ls-tree", tip, "--", path).split()[0]
                if moved != path:
                    git("update-index", "--force-remove", "--", path, env=env)
                git("update-index", "--add", "--cacheinfo", f"{mode},{blob},{moved}", env=env)
                files.append(path if moved == path else f"{path} (now {moved})")
        tree = git("write-tree", env=env)
    return git("commit-tree", tree, "-p", tip, "-m", message), files


def give_anew(base: str, head: str, tip: str, later: list[str], keep: bool = True) -> dict[tuple[str, int], int]:
    """The branch's numbers the branches merged before it gave to entries of their own, and the number each is given:
    the next one that neither these, the branches still to merge nor any branch of this clone use, reserved as
    scripts/new_entry.py reserves one (so no worktree of this clone takes it meanwhile; not for a trial, ``keep``
    False)."""
    given = {}
    for prefix in LOGS:
        clashing = clashes(base, head, tip, prefix)
        if clashing:
            top = max([*(highest(rev, prefix) for rev in [head, tip, *later]), *new_entry.taken(prefix)])
            for n in clashing:
                top = given[(prefix, n)] = new_entry.reserve(prefix, top) if keep else top + 1
    return given


# One merge.


def tidied(head: str, merged: Callable[[str], str | None]) -> tuple[dict[str, str], list[str], list[tuple[str, str]]]:
    """The logs after a merge, from ``merged`` (a merged file's text, None where there is none): the files to write
    (each log's newest entries sorted by number, the entries past a frozen log's last, a branch made before D-328's,
    moved to files of their own), the notes, and each file that needs a person and why (a number newly written twice
    differently, newest entries that can't be sorted, an entry to move whose file holds another)."""
    files: dict[str, str] = {}
    notes: list[str] = []
    person: list[tuple[str, str]] = []

    def now(path: str) -> str | None:  # (B and N share the bug log: the second kind reads it as the first left it)
        return files[path] if path in files else merged(path)

    for prefix, path in LOGS.items():
        if (text := now(path)) is None or "<<<<<<<" in text:
            continue
        asked = len(person)
        before = show(head, path)
        added = numbers(text, prefix) - numbers(before, prefix)
        tidy = sort_newest(text, prefix, min(added)) if added else text
        if tidy is None:
            person.append((path, "its newest entries could not be sorted by number (something other than entries "
                                 "comes after them)"))  # fmt: skip
        elif tidy != text:
            files[path] = text = tidy
            notes.append(f"Sorted the newest entries of {path} by number.")
        for n in sorted(doubled(text, prefix) - doubled(before, prefix)):
            versions = list(dict.fromkeys(entry for m, entry in entries(text, prefix) if m == n))
            person.append((path, alike_said(prefix, n, versions)))
        late = {n: entry for n, entry in entries(text, prefix) if n > FROZEN[prefix]}
        if len(person) > asked or not late:
            continue
        own = {n: f"{FOLDERS[prefix]}/{prefix}-{n}.md" for n in late}
        other = [own[n] for n in late if (there := now(own[n])) is not None and there.strip() != late[n].strip()]
        if other:
            person.append((path, f"entries past its last ({prefix}-{FROZEN[prefix]}) whose file holds another entry: "
                                 f"{', '.join(other)}"))  # fmt: skip
            continue
        files |= {own[n]: entry + "\n" for n, entry in late.items()}
        files[path] = without(text, prefix, set(late))
        said = ", ".join(f"{prefix}-{n}" for n in late)
        notes.append(f"Moved {said} from {path}, which stops at {prefix}-{FROZEN[prefix]}, into files of their own.")
    return files, notes, person


def tidy_logs(head: str, notes: list[str], person: list[str]) -> None:
    """After a merge, the logs tidied in the checkout (:func:`tidied`)."""
    files, said, asked = tidied(head, lambda path: read(path) if Path(path).is_file() else None)
    for path, text in files.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write(path, text)
    for note in said:
        print(f"  {note}")
    notes += said
    person += [f"{path}: {why}" for path, why in asked]


def merge(branch: str, later: list[str]) -> bool:
    """Merges one branch into the current one and commits it; whether there was anything to merge. Raises Stop
    when something needs a person, the merge left in progress."""
    head, tip = git("rev-parse", "HEAD"), git("rev-parse", f"{branch}^{{commit}}")
    if run_git(["merge-base", "--is-ancestor", tip, head]).returncode == 0:
        print("  Already in this branch: nothing to merge.")
        return False
    if run_git(["merge-base", head, tip]).returncode:
        raise Stop(f"  {branch} has no history in common with this branch: not merged.")
    base = git("merge-base", head, tip)
    current = git("branch", "--show-current")
    title = f"Merge {branch} into {current}" if current else f"Merge {branch}"
    notes: list[str] = []
    given = give_anew(base, head, tip, later)
    if given:
        olds = ", ".join(f"{p}-{n}" for p, n in given)
        said = ", ".join(f"{p}-{n} is {p}-{new} now" for (p, n), new in given.items())
        tip, files = renumbered(tip, base, given, f"Number {branch}'s entries anew: {said} (scripts/combine.py)\n\n"
                                f"The branches merged before it gave {olds} to entries of their own; the mentions "
                                "in the lines this branch changed follow.")  # fmt: skip
        notes.append(f"{branch}'s {said}: the branches merged before it use {olds} (in {', '.join(files)}).")
        print(f"  {notes[-1]} The branch itself is left as it was.")
    done = run_git(["-c", "merge.conflictstyle=diff3", "merge", "--no-commit", "--no-edit", "-m", title, tip])
    if run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"]).returncode:
        if git("rev-parse", "HEAD") == tip:
            print("  Fast-forwarded: the branch had all this branch has.")
            return True
        raise Stop("  git could not merge it:\n" + (done.stdout + done.stderr).decode("utf-8", "replace").strip())
    person = []
    for path in git("diff", "--name-only", "--diff-filter=U").splitlines():
        why = settle_file(path)
        if why:
            person.append(f"{path}: {why}")
        else:
            notes.append(f"Kept both sides of {path}.")
            print(f"  {path}: the two sides changed different lines: kept both.")
    tidy_logs(head, notes, person)
    message = "\n\n".join([f"{title} (scripts/combine.py)", "\n".join(notes)]).strip()
    if person:
        Path(git("rev-parse", "--git-path", "MERGE_MSG")).write_bytes(message.encode("utf-8") + b"\n")
        listed = "\n".join(f"    {line}" for line in person)
        raise Stop(f"  Stopped: these need a person:\n{listed}\n  The merge of {branch} is left in progress: fix "
                   "them, then git add the files and git commit --no-edit (git merge --abort undoes it).")  # fmt: skip
    git("commit", "-q", "-m", message)
    print(f"  Merged and committed ({git('rev-parse', '--short', 'HEAD')}).")
    return True


# A trial: each merge made by git merge-tree, as objects only (no worktree, index or ref changed).


def merge_tree(ours: str, theirs: str) -> tuple[str, dict[str, dict[str, str]]]:
    """git's merge of two commits as a tree, and each file in conflict with its stages (1 the base, 2 and 3 the
    sides) and their blobs."""
    done = run_git(["-c", "merge.conflictstyle=diff3", "merge-tree", "--write-tree", "--no-messages", ours, theirs])
    out = done.stdout.decode("utf-8", "replace").splitlines()
    if done.returncode not in (0, 1) or not out:
        said = done.stderr.decode("utf-8", "replace").strip()
        raise Stop(f"git merge-tree --write-tree failed (a trial needs git 2.38 or newer):\n{said}")
    stages: dict[str, dict[str, str]] = {}
    for line in out[1:]:  # "<mode> <blob> <stage>\t<path>" for each file in conflict
        info, _, path = line.partition("\t")
        stages.setdefault(path, {})[info.split()[2]] = info.split()[1]
    return out[0], stages


def utf8(object_name: str) -> str | None:
    """A blob's text (``<tree>:<path>``, or its id), None when there is none or it is not UTF-8."""
    done = run_git(["show", object_name])
    try:
        return None if done.returncode else done.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def with_files(tree: str, texts: dict[str, str]) -> str:
    """A tree with these files written in it (as objects: a throwaway index, not the checkout's)."""
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(folder) / "index")}
        git("read-tree", tree, env=env)
        for path, text in texts.items():
            blob = git("hash-object", "-w", "--stdin", data=text.encode("utf-8"))
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        return git("write-tree", env=env)


def try_merge(head: str, branch: str, later: list[str]) -> tuple[str, list[str], dict[str, str | None]]:
    """One merge tried as the script would make it: the commit it would make (an object no ref names), what it would
    do first, and each file in conflict with why a person is needed (None when the script settles it)."""
    tip = git("rev-parse", f"{branch}^{{commit}}")
    if run_git(["merge-base", "--is-ancestor", tip, head]).returncode == 0:
        return head, ["already in"], {}
    if run_git(["merge-base", head, tip]).returncode:
        raise Stop(f"{branch} has no history in common with the branch before it: nothing tried.")
    base = git("merge-base", head, tip)
    notes = []
    given = give_anew(base, head, tip, later, keep=False)
    if given:
        tip = renumbered(tip, base, given, f"Trial: {branch}'s entries numbered anew")[0]
        notes.append(", ".join(f"{p}-{n} would be {p}-{new}" for (p, n), new in given.items()))
    tree, conflicts = merge_tree(head, tip)
    verdicts, settled = {}, {}
    for path, stages in sorted(conflicts.items()):
        sides = [utf8(stages[s]) or "" for s in ("2", "3") if s in stages] if entry_number(path) else []
        text = utf8(f"{tree}:{path}")
        verdicts[path] = unsettled(path, set(stages), text, sides)
        if verdicts[path] is None:
            settled[path] = settle(text)
    if run_git(["merge-base", "--is-ancestor", head, tip]).returncode:  # (not a fast-forward: the logs are tidied)
        files, _, person = tidied(head, lambda path: settled[path] if path in settled else utf8(f"{tree}:{path}"))
        settled |= files
        verdicts |= dict(person)
    commit = git("commit-tree", with_files(tree, settled), "-p", head, "-p", tip, "-m", f"Trial merge of {branch}")
    return commit, notes, verdicts


def n_files(n: int) -> str:
    return f"{n} file{'s' if n != 1 else ''}"


def said_trial(name: str, notes: list[str], verdicts: dict[str, str | None]) -> list[str]:
    """Prints what one merge tried would conflict on; the code files in conflict."""
    code = [path for path, why in verdicts.items() if not (path.startswith("docs/") and path.endswith(".md"))]
    docs = {path: why for path, why in verdicts.items() if path not in code}
    print(f"{name}: {n_files(len(verdicts)) + ' would conflict' if verdicts else 'no conflict'}"
          f"{''.join(f' ({note})' for note in notes)}.")  # fmt: skip
    if code:
        print(f"  code, for a person: {', '.join(code)}")
    if settles := [path for path, why in docs.items() if why is None]:
        print(f"  docs, settled by combine.py: {', '.join(settles)}")
    for path, why in docs.items():
        if why is not None:
            print(f"  docs, for a person: {path}: {why}")
    return code


def trial(branches: list[str], start: str, pairs: bool) -> int:
    """What merging the branches in order onto ``start`` (and, with ``pairs``, each two) would conflict on; 1 when
    code would, else 0."""
    print(f"A trial: {', '.join(branches)} merged onto {start} in this order (nothing is changed).\n")
    head, code, tried = git("rev-parse", f"{start}^{{commit}}"), [], []
    for k, branch in enumerate(branches):
        head, notes, verdicts = try_merge(head, branch, branches[k + 1 :])
        tried.append((branch, verdicts))
        code += said_trial(branch, notes, verdicts)
    if pairs:
        print("\nEach two, merged with each other:")
        for one, other in itertools.combinations(branches, 2):
            _, notes, verdicts = try_merge(git("rev-parse", f"{one}^{{commit}}"), other, [])
            tried.append((f"{one} + {other}", verdicts))
            code += said_trial(f"{one} + {other}", notes, verdicts)
    people = [f"{name}: {path}" for name, verdicts in tried for path, why in verdicts.items()
              if why is not None and path.startswith("docs/") and path.endswith(".md")]  # fmt: skip
    docs = any(verdicts for _, verdicts in tried)
    if code:
        print(f"\nCode would conflict ({n_files(len(set(code)))}): combine.py would stop there for a person.")
    elif people:
        print(f"\nOnly docs would conflict; combine.py would stop for a person on {', '.join(people)}.")
    else:
        print("\nOnly docs would conflict, and combine.py would settle them all." if docs else "\nNo conflict.")
    return 1 if code else 0


# The run.


def prepare(branches: list[str], onto: str | None, trial: bool = False) -> str:
    """Checks the checkout and the branches, makes the --onto branch; the commit the merges start from. For a trial,
    only the checkout and the branches (nothing is changed)."""
    if run_git(["rev-parse", "--show-toplevel"]).returncode:
        raise Stop("Not in a git checkout: run it from the AP Coder folder.")
    os.chdir(git("rev-parse", "--show-toplevel"))
    unknown = [b for b in [*branches, *([onto] if onto else [])]
               if run_git(["rev-parse", "-q", "--verify", f"{b}^{{commit}}"]).returncode]  # fmt: skip
    if unknown:
        raise Stop(f"No branch or commit named {', '.join(unknown)}: nothing done.")
    if trial:
        return onto or "HEAD"
    if run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"]).returncode == 0:
        raise Stop("A merge is in progress: commit it (or git merge --abort) first.")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise Stop("There are changes not committed: commit or stash them first.")
    if onto:
        name = f"combined-{datetime.date.today():%Y-%m-%d}"
        taken = set(git("branch", "--list", "--format=%(refname:short)").splitlines())
        name = next(n for n in [name, *(f"{name}-{k}" for k in range(2, 1000))] if n not in taken)
        git("checkout", "-q", "-b", name, onto)
        print(f"On a new branch, {name}, from {onto} (git branch -m NEW-NAME renames it).")
    elif not git("branch", "--show-current"):
        raise Stop("Not on a branch (a detached HEAD): switch to one first, or give --onto REF.")
    else:
        print(f"On the branch {git('branch', '--show-current')}.")
    return git("rev-parse", "HEAD")


def run_step(command: list[str]) -> bool:
    print("$ " + " ".join(f'"{c}"' if not c or " " in c else c for c in command), flush=True)
    return subprocess.run(command).returncode == 0


def check(start: str) -> bool:
    """scripts/check.py on the files the merges changed, with the rules that check the whole tree (its RULES)
    whatever changed (``--rules``: docs alone bring only the docs' own rules), run once, as check.py runs them."""
    files = git("diff", "--name-only", start, "HEAD").splitlines()
    if not files:
        print("\nNothing changed: no check needed.")
        return True
    print(f"\nChecking: scripts/check.py on the {n_files(len(files))} the merges changed, with the rules that check "
          "the whole tree.", flush=True)  # fmt: skip
    passed = run_step([sys.executable, "scripts/check.py", "--rules", *files])
    print("Checks: OK." if passed else "Checks: FAILED: see above; the merges are committed, not pushed.")
    return passed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("branches", nargs="+", metavar="BRANCH", help="the branches to merge, in this order")
    parser.add_argument("--onto", metavar="REF", help="merge into a new branch from REF (combined-<date>)")
    parser.add_argument("--no-check", action="store_true", help="skip the meta tests and scripts/check.py")
    parser.add_argument("--trial", action="store_true", help="only say what each merge would conflict on")
    parser.add_argument("--pairs", action="store_true", help="with --trial: also each two branches with each other")
    args = parser.parse_args(argv)
    if args.pairs and not args.trial:
        parser.error("--pairs goes with --trial")
    try:
        start = prepare(args.branches, args.onto, args.trial)
        if args.trial:
            return trial(args.branches, start, args.pairs)
    except Stop as stop:
        print(stop)
        return 2
    merged = []
    for k, branch in enumerate(args.branches):
        print(f"\nMerging {branch} ({k + 1} of {len(args.branches)})...", flush=True)
        try:
            if merge(branch, args.branches[k + 1 :]):
                merged.append(branch)
        except Stop as stop:
            print(stop)
            rest = args.branches[k + 1 :]
            if rest:
                print(f"  Not merged yet: {', '.join(rest)}. Once this merge is committed: "
                      f"python scripts/combine.py {' '.join(rest)}")  # fmt: skip
            if not args.no_check:
                print(f"  Not checked: once all is merged, python scripts/check.py --rules on the files of git diff "
                      f"--name-only {start[:10]} HEAD.")  # fmt: skip
            return 1
    print(f"\nMerged {len(merged)} of {len(args.branches)} branches: {', '.join(merged) or 'none'}. Nothing pushed.")
    if args.no_check:
        print("Checks skipped (--no-check).")
        return 0
    return 0 if check(start) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
